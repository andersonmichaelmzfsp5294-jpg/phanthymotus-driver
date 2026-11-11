package com.example.heartrateweartest

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.health.services.client.ExerciseUpdateCallback
import androidx.health.services.client.HealthServices
import androidx.health.services.client.data.Availability
import androidx.health.services.client.data.DataType
import androidx.health.services.client.data.ExerciseConfig
import androidx.health.services.client.data.ExerciseLapSummary
import androidx.health.services.client.data.ExerciseType
import androidx.health.services.client.data.ExerciseTrackedStatus
import androidx.health.services.client.data.ExerciseState
import androidx.health.services.client.data.ExerciseUpdate
import com.google.common.util.concurrent.FutureCallback
import com.google.common.util.concurrent.Futures
import com.samsung.android.service.health.tracking.data.TrackerUserProfile
import org.json.JSONObject
import java.util.concurrent.Executor

/**
 * Foreground, wearer-started real running exercise. Samsung's estimator
 * receives actual Health Services STEPS_PER_MINUTE samples. Its final
 * SDK status is authoritative; invalid status never becomes a valid mL value.
 * Does not replace another application's active exercise.
 */
class ResearchSweatRun(
    private val context: Context,
    private val gateway: ResearchV3Gateway,
    private val report: (String) -> Unit,
    private val finished: () -> Unit
) {
    private val ui = Handler(Looper.getMainLooper())
    private val executor = Executor { command -> ui.post(command) }
    private val exerciseClient = HealthServices.getClient(context).exerciseClient
    private var sdk: SamsungSweatTracker? = null
    private var phase = "idle"
    private var allowUpload = false
    private var ownsExercise = false
    private var startedElapsedMs = 0L
    private var stopElapsedMs = 0L
    private var spmSamples = 0
    private var lastSpmBootMs = -1L
    private var availableTypes: Set<DataType<*, *>> = emptySet()
    private var store: SamsungSignalStore? = null
    private var updateRegistered = false
    private var foregroundStarted = false
    private var runGeneration = 0L
    private val expiry = Runnable {
        if (phase == "ending") {
            report("等待三星汗液估计超时；没有生成估计结果")
            cleanup()
        }
    }

    fun isBusy(): Boolean = phase != "idle"

    private val updates = object : ExerciseUpdateCallback {
        override fun onRegistered() = Unit
        override fun onRegistrationFailed(throwable: Throwable) {
            report("运动数据回调注册失败，停止采集")
            cleanup()
        }
        override fun onLapSummaryReceived(lapSummary: ExerciseLapSummary) = Unit
        override fun onAvailabilityChanged(dataType: DataType<*, *>, availability: Availability) = Unit

        override fun onExerciseUpdateReceived(update: ExerciseUpdate) {
            if (phase != "running" && phase != "ending") return
            for (point in update.latestMetrics.getData(DataType.STEPS_PER_MINUTE)) {
                val bootMs = point.timeDurationFromBoot.toMillis()
                if (bootMs <= lastSpmBootMs || bootMs < startedElapsedMs ||
                    bootMs > SystemClock.elapsedRealtime() + 1000L) continue
                lastSpmBootMs = bootMs
                val spm = point.value.toFloat()
                // Use elapsed-time -> UTC conversion; never send arrival clock as
                // a made-up sensor timestamp.
                val timestamp = System.currentTimeMillis() -
                    (SystemClock.elapsedRealtime() - bootMs)
                if (sdk?.feedCadence(spm, timestamp) == true) spmSamples++
            }
            if (phase == "running" &&
                update.exerciseStateInfo.state == ExerciseState.ENDED) {
                report("Health Services 已提前结束跑步运动，汗液结果无效")
                cleanup()
            }
        }
    }

    fun start(profile: TrackerUserProfile, uploadAfterExercise: Boolean): Boolean {
        if (phase != "idle" || gateway.isActive() ||
            context.checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION)
            != PackageManager.PERMISSION_GRANTED ||
            context.checkSelfPermission(
                "com.samsung.android.hardware.sensormanager.permission.READ_ADDITIONAL_HEALTH_DATA"
            ) != PackageManager.PERMISSION_GRANTED) return false
        phase = "connecting"
        val generation = ++runGeneration
        allowUpload = uploadAfterExercise
        ownsExercise = false
        startedElapsedMs = 0L
        spmSamples = 0
        lastSpmBootMs = -1
        store = SamsungSignalStore(context, "sweat_loss")
        try {
            context.startForegroundService(Intent(context,
                ResearchSweatKeepAliveService::class.java))
            foregroundStarted = true
        } catch (_: Exception) {
            report("跑步前台服务启动失败；不会开始持续追踪")
            cleanup()
            return false
        }
        val tracker = SamsungSweatTracker(context, object : SamsungSweatTracker.Listener {
            override fun onReady() {
                if (phase == "connecting" && generation == runGeneration)
                    checkExerciseState(generation)
            }
            override fun onStatus(state: String) {
                if (generation != runGeneration) return
                report("汗液SDK：" + state)
                if (state.contains("failed") || state.contains("unavailable") ||
                    state.contains("not_supported") || state.contains("connection_ended") ||
                    state.startsWith("samsung_tracker_error")) cleanup()
            }
            override fun onResult(sdkStatus: Int, sweatLossMl: Double?) {
                if (phase != "ending" || generation != runGeneration) return
                val duration = (stopElapsedMs-startedElapsedMs).coerceAtLeast(0L)
                val valid = sdkStatus == 0 && sweatLossMl != null &&
                    duration >= 300000L && spmSamples > 0
                val record = JSONObject().put("exercise_type", "running")
                    .put("provider", "samsung_sdk")
                    .put("sdk_status", sdkStatus)
                    .put("exercise_duration_ms", duration)
                    .put("spm_samples", spmSamples)
                    .put("sweat_loss_ml", if (valid) sweatLossMl else JSONObject.NULL)
                val persisted = store?.append(record, System.currentTimeMillis()) == true
                val publishable = valid || sdkStatus != 0
                val shareResult = persisted && allowUpload &&
                    gateway.isActive() && publishable
                if (shareResult)
                    gateway.send("sweat_loss_result", record)
                report(if (valid) "三星估计跑步汗液流失量：" + sweatLossMl + " mL"
                       else "汗液估计无有效结果，SDK状态=" + sdkStatus)
                cleanup(keepResultWindow = shareResult)
            }
        })
        sdk = tracker
        if (!tracker.connect(profile)) {
            cleanup()
            return false
        }
        report("正在连接汗液SDK；先核查是否有其他应用正在记录运动")
        return true
    }

    private fun checkExerciseState(generation: Long) {
        Futures.addCallback(exerciseClient.getCurrentExerciseInfoAsync(),
            object : FutureCallback<androidx.health.services.client.data.ExerciseInfo> {
                override fun onSuccess(info: androidx.health.services.client.data.ExerciseInfo) {
                    if (phase != "connecting" || generation != runGeneration) return
                    if (info.exerciseTrackedStatus != ExerciseTrackedStatus.NO_EXERCISE_IN_PROGRESS) {
                        report("有其他运动进行中；本应用不会中断原有运动")
                        cleanup()
                    } else checkCapabilities(generation)
                }
                override fun onFailure(error: Throwable) {
                    if (generation != runGeneration) return
                    report("无法确认运动是否占用；取消本次汗液采集")
                    cleanup()
                }
            }, executor)
    }

    private fun checkCapabilities(generation: Long) {
        Futures.addCallback(exerciseClient.getCapabilitiesAsync(),
            object : FutureCallback<androidx.health.services.client.data.ExerciseCapabilities> {
                override fun onSuccess(capabilities: androidx.health.services.client.data.ExerciseCapabilities) {
                    if (phase != "connecting" || generation != runGeneration) return
                    if (capabilities == null ||
                        ExerciseType.RUNNING !in capabilities.supportedExerciseTypes) {
                        report("此设备不支持 Health Services 跑步运动")
                        cleanup()
                        return
                    }
                    val supported = capabilities
                        .getExerciseTypeCapabilities(ExerciseType.RUNNING).supportedDataTypes
                    if (DataType.STEPS_PER_MINUTE !in supported) {
                        report("此设备未提供跑步步频，无法得到有效汗液估计")
                        cleanup()
                        return
                    }
                    availableTypes = setOf(DataType.STEPS_PER_MINUTE).let {
                        if (DataType.DISTANCE in supported) it + DataType.DISTANCE else it
                    }
                    startHealthExercise(generation)
                }
                override fun onFailure(error: Throwable) {
                    if (generation != runGeneration) return
                    report("无法获取跑步运动能力")
                    cleanup()
                }
            }, executor)
    }

    private fun startHealthExercise(generation: Long) {
        if (phase != "connecting" || generation != runGeneration) return
        val config = ExerciseConfig.Builder(ExerciseType.RUNNING)
            .setDataTypes(availableTypes)
            .setIsAutoPauseAndResumeEnabled(false)
            .setIsGpsEnabled(false)
            .build()
        exerciseClient.setUpdateCallback(updates)
        updateRegistered = true
        Futures.addCallback(exerciseClient.startExerciseAsync(config),
            object : FutureCallback<Void> {
                override fun onSuccess(value: Void) {
                    if (phase != "connecting" || generation != runGeneration) {
                        // Start completed after a cancellation. Stop only our
                        // own exercise, not one started by another app.
                        Futures.addCallback(exerciseClient.getCurrentExerciseInfoAsync(),
                            object : FutureCallback<androidx.health.services.client.data.ExerciseInfo> {
                                override fun onSuccess(info: androidx.health.services.client.data.ExerciseInfo) {
                                    if (info.exerciseTrackedStatus ==
                                        ExerciseTrackedStatus.OWNED_EXERCISE_IN_PROGRESS)
                                        exerciseClient.endExerciseAsync()
                                }
                                override fun onFailure(error: Throwable) = Unit
                            }, executor)
                        return
                    }
                    ownsExercise = true
                    if (sdk?.beginExercise() != true) {
                        report("三星 SDK 未成功进入运动状态")
                        cleanup()
                        return
                    }
                    startedElapsedMs = SystemClock.elapsedRealtime()
                    phase = "running"
                    report("跑步采集开始：真实运动至少5分钟，结束后等待SDK结果")
                }
                override fun onFailure(error: Throwable) {
                    if (generation != runGeneration) return
                    report("Health Services 无法启动跑步记录")
                    cleanup()
                }
            }, executor)
    }

    fun finishExercise(): Boolean {
        if (phase != "running") return false
        phase = "ending"
        stopElapsedMs = SystemClock.elapsedRealtime()
        if (allowUpload && !gateway.start("watch_sweat_loss", 90000))
            report("本次汗液结果仅保留在手表：上传会话未建立")
        if (sdk?.stopExercise() != true) {
            report("三星汗液估计无法结束")
            cleanup()
            return false
        }
        if (ownsExercise) {
            ownsExercise = false
            exerciseClient.endExerciseAsync()
        }
        ui.postDelayed(expiry, 30000L)
        return true
    }

    private fun cleanup(keepResultWindow: Boolean = false) {
        if (phase == "idle") return
        phase = "idle"
        ++runGeneration
        ui.removeCallbacks(expiry)
        if (ownsExercise) {
            ownsExercise = false
            try { exerciseClient.endExerciseAsync() } catch (_: Exception) { }
        }
        if (updateRegistered) {
            updateRegistered = false
            exerciseClient.clearUpdateCallbackAsync(updates)
        }
        sdk?.close()
        sdk = null
        if (foregroundStarted) {
            foregroundStarted = false
            context.stopService(Intent(context, ResearchSweatKeepAliveService::class.java))
        }
        try { store?.finish("exercise_ended") } catch (_: Exception) { }
        store = null
        if (keepResultWindow) {
            // Preserve an explicitly authorized, temporary MCP read window
            // after exercise and SDK trackers have both stopped.
            report("跑步结束：结果仅在12秒授权窗口内可由 Card 查询")
            ui.postDelayed({ gateway.stop() }, 12000)
        } else {
            gateway.stop()
        }
        allowUpload = false
        finished()
    }

    fun close() {
        cleanup()
        gateway.stop()
    }
}
