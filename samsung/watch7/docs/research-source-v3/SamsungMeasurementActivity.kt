package com.example.heartrateweartest

import android.app.Activity
import android.Manifest
import android.app.AlertDialog
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import com.samsung.android.service.health.tracking.data.HealthTrackerType
import com.samsung.android.service.health.tracking.data.TrackerUserProfile
import java.io.File

/**
 * Explicit, on-Watch user action is the sole way to start a Samsung SDK
 * recording. No MCP/Agent/remote intents; v3 raw sharing requires separate
 * on-Watch consent plus an operator-enabled Driver.
 */
class SamsungMeasurementActivity : Activity() {
    private lateinit var bridge: SamsungTrackerBridge
    private lateinit var status: TextView
    private lateinit var telemetry: ResearchTelemetryClient
    private lateinit var scalarOptIn: CheckBox
    private lateinit var ppgIndexOptIn: CheckBox
    private lateinit var offBody: OffBodyReader
    private lateinit var v3: ResearchV3Gateway
    private lateinit var imu: ResearchImuCapture
    private lateinit var location: ResearchLocationCapture
    private lateinit var audio: ResearchAudioCapture
    private lateinit var step: ResearchStepDetectorCapture
    private lateinit var sweat: ResearchSweatRun
    private lateinit var v3OptIn: CheckBox
    private val uiHandler = Handler(Looper.getMainLooper())
    private var researchActive = false

    private fun button(box: LinearLayout, label: String, action: () -> Unit) {
        box.addView(Button(this).apply {
            text = label
            isAllCaps = false
            textSize = 12f
            setOnClickListener { action() }
        })
    }

    private fun setState(value: String) { status.text = value }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(18, 22, 18, 22)
        }
        val scroller = ScrollView(this).apply { addView(panel) }
        status = TextView(this).apply { textSize = 12f }
        panel.addView(TextView(this).apply {
            text = "Samsung Health Sensor · 研究版"
            textSize = 17f
        })
        panel.addView(TextView(this).apply {
            text = "高级测量只能在手表前台由本人启动。数据默认仅保存在私有目录；如勾选本次 v3 上传，可将有效波形、位置或音频片段发送给已配对 Driver。研究数据不用于诊断。" 
            textSize = 11f
        })
        panel.addView(status)

        telemetry = ResearchTelemetryClient(this) { message ->
            runOnUiThread { setState(message) }
        }
        v3 = ResearchV3Gateway(this) { message ->
            runOnUiThread { setState(message) }
        }
        imu = ResearchImuCapture(this, v3) { message ->
            runOnUiThread { setState(message) }
        }
        location = ResearchLocationCapture(this, v3,
            { message -> runOnUiThread { setState(message) } },
            { v3.stop() })
        audio = ResearchAudioCapture(this, v3,
            { message -> runOnUiThread { setState(message) } },
            { v3.stop() })
        step = ResearchStepDetectorCapture(this, v3) { message ->
            runOnUiThread { setState(message) }
        }
        sweat = ResearchSweatRun(this, v3,
            { message -> runOnUiThread { setState(message) } },
            { runOnUiThread { v3OptIn.isEnabled = true } })
        offBody = OffBodyReader(this) { state ->
            telemetry.updateWear(state)
            v3.updateWear(state)
            if (state != "on_body") {
                runOnUiThread {
                    imu.stop()
                    audio.stop()
                    if (::step.isInitialized) step.stop()
                    if (::sweat.isInitialized) sweat.close()
                    if (::bridge.isInitialized && bridge.isMeasuring())
                        bridge.stop("watch_not_worn")
                }
            }
        }
        offBody.start()

        scalarOptIn = CheckBox(this).apply {
            text = "仅本次测量：把有效 IBI / SpO₂ / 皮温值发给已配对 Driver"
            isChecked = false
            textSize = 11f
        }
        ppgIndexOptIn = CheckBox(this).apply {
            text = "仅本次 PPG：发送录制 ID、采样数、采样率（不传原始波形）"
            isChecked = false
            textSize = 11f
        }
        panel.addView(scalarOptIn)
        panel.addView(ppgIndexOptIn)
        v3OptIn = CheckBox(this).apply {
            text = "仅本次数据授权：IMU / PPG / ECG / BIA / GPS / 音频 / 步伐事件 / 跑步汗液估计"
            isChecked = false
            textSize = 11f
        }
        panel.addView(v3OptIn)
        panel.addView(TextView(this).apply {
            text = "上传必须单独勾选、确认佩戴，并在研究版主界面配置网关地址/密钥。每次重新测量均需重新授权。"
            textSize = 10f
        })
        bridge = SamsungTrackerBridge(this, object : SamsungTrackerBridge.Listener {
            override fun onStatus(state: String, id: String, samples: Int) {
                runOnUiThread {
                    setState("SDK状态：" + state + "\n会话：" +
                        (if (id.isBlank()) "--" else id) + "\n已保存样本：" + samples)
                }
                if (state !in setOf("connecting", "ready", "starting", "measuring") &&
                    !bridge.isMeasuring()) {
                    telemetry.finish()
                    v3.stop()
                    runOnUiThread {
                        scalarOptIn.isEnabled = true
                        ppgIndexOptIn.isEnabled = true
                        v3OptIn.isEnabled = true
                        v3OptIn.isChecked = false
                        // Never carry opt-in to the next physical measurement.
                        scalarOptIn.isChecked = false
                        ppgIndexOptIn.isChecked = false
                    }
                }
            }

            override fun onScalar(metric: String, value: Double, timestampMs: Long) {
                telemetry.scalar(metric, value, timestampMs)
            }

            override fun onManifest(manifest: org.json.JSONObject) {
                telemetry.ppgManifest(manifest)
            }

            override fun onV3Chunk(kind: String, payload: org.json.JSONObject) {
                v3.send(kind, payload)
            }
        })

        val options = listOf(
            "三色PPG 30秒" to HealthTrackerType.PPG_CONTINUOUS,
            "心率与IBI 60秒" to HealthTrackerType.HEART_RATE_CONTINUOUS,
            "ECG心电 30秒" to HealthTrackerType.ECG_ON_DEMAND,
            "血氧 SpO2 按需" to HealthTrackerType.SPO2_ON_DEMAND,
            "皮温按需" to HealthTrackerType.SKIN_TEMPERATURE_ON_DEMAND,
            "皮温连续 60秒" to HealthTrackerType.SKIN_TEMPERATURE_CONTINUOUS
        )
        options.forEach { (name, type) -> button(panel, name) { requestMeasurement(type) } }
        button(panel, "BIA体成分（需确认禁忌）") { showBiaConsent() }
        button(panel, "IMU 高频采集 30秒") {
            if (prepareResearch("watch_imu_stream")) {
                if (imu.start()) {
                    uiHandler.postDelayed({
                        imu.stop()
                        v3.stop()
                        v3OptIn.isChecked = false
                    }, 30_000)
                } else v3.stop()
            }
        }
        button(panel, "逐步事件采集 60秒") {
            if (checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION) !=
                PackageManager.PERMISSION_GRANTED) {
                requestPermissions(arrayOf(Manifest.permission.ACTIVITY_RECOGNITION), 7201)
                setState("请授权运动识别后再次点击逐步检测")
            } else if (prepareResearch("watch_step_detector")) {
                if (step.start()) {
                    uiHandler.postDelayed({
                        step.stop()
                        v3.stop()
                        v3OptIn.isChecked = false
                    }, 60_000)
                } else v3.stop()
            }
        }
        button(panel, "跑步汗液流失估计：开始") { showSweatStart() }
        button(panel, "结束跑步并获取汗液估计") {
            if (!sweat.finishExercise())
                setState("当前没有可结束的跑步采集")
        }
        button(panel, "GPS 单次定位") {
            if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) !=
                PackageManager.PERMISSION_GRANTED) {
                requestPermissions(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION), 7101)
                setState("请授权精确定位后再次点击")
            } else if (prepareResearch("watch_location")) {
                if (!location.once()) v3.stop()
            }
        }
        button(panel, "麦克风录音 3秒（发送PCM）") {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) !=
                PackageManager.PERMISSION_GRANTED) {
                requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 7102)
                setState("请授权麦克风后再次点击")
            } else if (prepareResearch("watch_audio")) {
                if (!audio.start()) v3.stop()
            }
        }
        button(panel, "停止并保存") {
            imu.stop()
            step.stop()
            audio.stop()
            if (sweat.isBusy()) sweat.close()
            bridge.stop("stopped_by_user")
            v3.stop()
            v3OptIn.isChecked = false
        }
        button(panel, "删除本机高级传感器记录") { confirmDelete() }
        setContentView(scroller)
        setState("正在连接三星健康平台；未启动任何测量")
        bridge.connect()
    }

    private fun prepareResearch(card: String): Boolean {
        if (WatchRuntime.collecting || bridge.isMeasuring() ||
            audio.isRecording() || step.isCapturing() ||
            sweat.isBusy() || imu.isCapturing() || v3.isActive()) {
            setState("请先结束正在运行的监测或测量")
            return false
        }
        if (!v3OptIn.isChecked) {
            setState("请先勾选本次 v3 数据上传授权")
            return false
        }
        if (!v3.start(card)) {
            setState("佩戴状态、配对或当前会话尚未满足上传条件")
            return false
        }
        v3OptIn.isChecked = false
        return true
    }

    private fun requestMeasurement(type: HealthTrackerType) {
        if (WatchRuntime.collecting || imu.isCapturing() || audio.isRecording() ||
            step.isCapturing() || sweat.isBusy() || v3.isActive()) {
            setState("请先停止基础监测、研究采集或其他已授权会话。")
            return
        }
        if (!bridge.isSupported(type)) {
            setState("SDK未连接或未报告支持：" + type.name)
            return
        }
        val permission = SamsungTrackerBridge.permissionFor(type)
        if (checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) {
            setState("请先批准三星健康权限，再次点击开始。")
            requestPermissions(arrayOf(permission), 7001)
            return
        }
        bridge.start(type, null)
        if (bridge.isMeasuring()) {
            val scalars = type == HealthTrackerType.HEART_RATE_CONTINUOUS ||
                type == HealthTrackerType.SPO2_ON_DEMAND ||
                type == HealthTrackerType.SKIN_TEMPERATURE_ON_DEMAND ||
                type == HealthTrackerType.SKIN_TEMPERATURE_CONTINUOUS
            val ppg = type == HealthTrackerType.PPG_CONTINUOUS
            telemetry.begin(scalars && scalarOptIn.isChecked,
                ppg && ppgIndexOptIn.isChecked)
            val rawCard = when (type) {
                HealthTrackerType.PPG_CONTINUOUS -> "watch_ppg_raw"
                HealthTrackerType.ECG_ON_DEMAND -> "watch_ecg"
                else -> null
            }
            if (rawCard != null && v3OptIn.isChecked) {
                if (!v3.start(rawCard)) setState("SDK可本地测量；原始流未获上传授权")
            }
            v3OptIn.isChecked = false
            v3OptIn.isEnabled = false
            scalarOptIn.isEnabled = false
            ppgIndexOptIn.isEnabled = false
        }
    }

    private fun showSweatStart() {
        if (WatchRuntime.collecting || bridge.isMeasuring() ||
            imu.isCapturing() || step.isCapturing() ||
            audio.isRecording() || v3.isActive() || sweat.isBusy()) {
            setState("请先停止其他测量或正在进行的运动")
            return
        }
        if (!bridge.isSupported(HealthTrackerType.SWEAT_LOSS)) {
            setState("三星 SDK 当前未报告 SWEAT_LOSS 能力")
            return
        }
        val permissions = arrayOf(
            Manifest.permission.ACTIVITY_RECOGNITION,
            "com.samsung.android.hardware.sensormanager.permission.READ_ADDITIONAL_HEALTH_DATA")
        val missing = permissions.filter {
            checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            requestPermissions(missing.toTypedArray(), 7202)
            setState("请授权运动识别及三星健康权限后重新开始")
            return
        }
        val form = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(14, 8, 14, 8)
        }
        val fields = listOf("身高 cm", "体重 kg", "年龄（周岁）").map { title ->
            EditText(this).apply {
                hint = title
                inputType = android.text.InputType.TYPE_CLASS_NUMBER or
                    android.text.InputType.TYPE_NUMBER_FLAG_DECIMAL
                form.addView(this)
            }
        }
        val gender = Spinner(this)
        gender.adapter = ArrayAdapter(this,
            android.R.layout.simple_spinner_dropdown_item, listOf("女", "男"))
        form.addView(gender)
        val acknowledge = CheckBox(this).apply {
            text = "本人确认开始独立跑步记录；至少5分钟的真实跑步，其他App正在记录时不覆盖。汗液流失为SDK估算。"
            isChecked = false
            textSize = 11f
        }
        form.addView(acknowledge)
        AlertDialog.Builder(this)
            .setTitle("跑步汗液流失估计（Samsung SDK）")
            .setView(form)
            .setNegativeButton("取消", null)
            .setPositiveButton("本人开始跑步") { _, _ ->
                val height = fields[0].text.toString().toFloatOrNull()
                val weight = fields[1].text.toString().toFloatOrNull()
                val age = fields[2].text.toString().toIntOrNull()
                if (!acknowledge.isChecked || height == null || height !in 100f..230f ||
                    weight == null || weight !in 25f..250f ||
                    age == null || age !in 20..100) {
                    setState("输入资料或跑步确认未满足，未启动")
                    return@setPositiveButton
                }
                val profile = TrackerUserProfile.Builder()
                    .setHeight(height).setWeight(weight).setAge(age)
                    .setGender(gender.selectedItemPosition).build()
                val authorized = v3OptIn.isChecked
                if (sweat.start(profile, authorized)) {
                    v3OptIn.isChecked = false
                    v3OptIn.isEnabled = false
                } else {
                    setState("无法启动本次跑步记录")
                }
            }.show()
    }

    private fun showBiaConsent() {
        if (WatchRuntime.collecting || bridge.isMeasuring() ||
            imu.isCapturing() || audio.isRecording() || step.isCapturing() ||
            sweat.isBusy() || v3.isActive()) {
            setState("请先停止其他正在进行的采集。")
            return
        }
        if (!bridge.isSupported(HealthTrackerType.BIA_ON_DEMAND)) {
            setState("此设备/SDK目前未报告支持BIA。")
            return
        }
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16, 8, 16, 8)
        }
        val fields = listOf("身高(cm)", "体重(kg)", "年龄(周岁)").map { hint ->
            EditText(this).apply {
                this.hint = hint
                inputType = android.text.InputType.TYPE_CLASS_NUMBER or
                    android.text.InputType.TYPE_NUMBER_FLAG_DECIMAL
                panel.addView(this)
            }
        }
        val gender = Spinner(this)
        gender.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item,
            listOf("女", "男"))
        panel.addView(gender)
        val consent = CheckBox(this).apply {
            text = "本人确认：无心脏起搏器等植入式医疗设备，非妊娠状态；了解BIA仅用于研究，需双指接触两枚电极。"
            textSize = 11f
        }
        panel.addView(consent)
        AlertDialog.Builder(this)
            .setTitle("BIA测量：阅读并确认禁忌")
            .setView(panel)
            .setNegativeButton("取消", null)
            .setPositiveButton("确认并开始") { _, _ ->
                val height = fields[0].text.toString().toFloatOrNull()
                val weight = fields[1].text.toString().toFloatOrNull()
                val age = fields[2].text.toString().toIntOrNull()
                if (!consent.isChecked || height == null || height !in 100f..230f ||
                    weight == null || weight !in 25f..250f ||
                    age == null || age !in 20..100
                ) {
                    setState("BIA安全确认或输入条件未满足；未发起测量。")
                    return@setPositiveButton
                }
                val permission = SamsungTrackerBridge.permissionFor(HealthTrackerType.BIA_ON_DEMAND)
                if (checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) {
                    setState("请先批准三星健康权限，再重新点击BIA并确认。")
                    requestPermissions(arrayOf(permission), 7002)
                    return@setPositiveButton
                }
                val profile = TrackerUserProfile.Builder()
                    .setHeight(height).setWeight(weight).setAge(age)
                    .setGender(gender.selectedItemPosition).build()
                // BIA is measured only after explicit contraindication review;
                // an independent v3 checkbox controls optional completed-result sharing.
                bridge.start(HealthTrackerType.BIA_ON_DEMAND, profile)
                if (bridge.isMeasuring()) {
                    if (v3OptIn.isChecked && !v3.start("watch_bia"))
                        setState("BIA本地测量中；没有开启远程结果分享")
                    v3OptIn.isChecked = false
                    v3OptIn.isEnabled = false
                }
                scalarOptIn.isEnabled = false
                ppgIndexOptIn.isEnabled = false
            }.show()
    }

    private fun confirmDelete() {
        if (bridge.isMeasuring()) {
            setState("请先停止并保存当前记录，再执行删除。")
            return
        }
        AlertDialog.Builder(this)
            .setTitle("删除全部本地高级传感器记录？")
            .setMessage("永久删除，不会触发网络同步。")
            .setNegativeButton("取消", null)
            .setPositiveButton("确认删除") { _, _ ->
                val folder = File(filesDir, "samsung_recordings")
                val items = folder.listFiles()?.filter { it.isFile } ?: emptyList()
                val deleted = items.count { it.delete() }
                setState("已删除本地记录文件：" + deleted + "/" + items.size)
            }.show()
    }

    override fun onDestroy() {
        uiHandler.removeCallbacksAndMessages(null)
        imu.stop()
        step.stop()
        sweat.close()
        audio.close()
        offBody.stop()
        bridge.close()
        telemetry.close()
        v3.close()
        super.onDestroy()
    }
}
