package com.example.heartrateweartest

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject

/**
 * A true step event source, distinct from TYPE_STEP_COUNTER's cumulative count.
 * Explicit foreground capture only, with source timestamps and bounded JSONL.
 * One sample is one actual TYPE_STEP_DETECTOR notification.
 */
class ResearchStepDetectorCapture(
    context: Context,
    private val gateway: ResearchV3Gateway,
    private val report: (String) -> Unit
) : SensorEventListener {
    private val context = context.applicationContext
    private val manager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    private val sensor = manager.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR)
    private var thread: HandlerThread? = null
    private var store: SamsungSignalStore? = null
    private var samples = JSONArray()
    private var firstTimestampNs = -1L
    private var lastTimestampNs = -1L
    private var lastAcceptedNs = -1L
    @Volatile private var active = false

    fun isCapturing(): Boolean = active
    fun supported(): Boolean = sensor != null

    @Synchronized
    fun start(): Boolean {
        if (active || sensor == null) return false
        if (context.checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION)
            != PackageManager.PERMISSION_GRANTED) {
            report("逐步事件需先授权运动识别")
            return false
        }
        samples = JSONArray()
        firstTimestampNs = -1L
        lastTimestampNs = -1L
        lastAcceptedNs = -1L
        val newStore = SamsungSignalStore(context, "step_events")
        val newThread = HandlerThread("watch7-step-event").apply { start() }
        val ok = manager.registerListener(this, sensor, SensorManager.SENSOR_DELAY_FASTEST,
            Handler(newThread.looper))
        if (!ok) {
            newStore.finish("sensor_registration_failed")
            newThread.quitSafely()
            report("设备未开放逐步事件，未启动采集")
            return false
        }
        store = newStore
        thread = newThread
        active = true
        report("正在采集逐步事件（每步真实传感器通知）")
        return true
    }

    @Synchronized
    override fun onSensorChanged(event: SensorEvent) {
        if (!active || event.sensor.type != Sensor.TYPE_STEP_DETECTOR ||
            event.values.isEmpty() || !event.values[0].isFinite() ||
            event.values[0] < 0.5f || event.timestamp <= lastAcceptedNs) return
        if (firstTimestampNs >= 0 &&
            (event.timestamp - firstTimestampNs > 20_000_000_000L ||
             samples.length() >= 32)) flush()
        if (firstTimestampNs < 0) firstTimestampNs = event.timestamp
        val offset = (event.timestamp - firstTimestampNs) / 1_000_000L
        if (offset !in 0L..30000L) return
        if (samples.length() > 0) {
            val previous = samples.optJSONArray(samples.length()-1)?.optLong(0) ?: -1L
            if (offset <= previous) return
        }
        samples.put(JSONArray().put(offset))
        lastTimestampNs = event.timestamp
        lastAcceptedNs = event.timestamp
        if (samples.length() >= 32) flush()
    }

    @Synchronized
    private fun flush() {
        val count = samples.length()
        if (count > 0 && firstTimestampNs >= 0 && lastTimestampNs >= firstTimestampNs) {
            val wall = System.currentTimeMillis()
            val mono = SystemClock.elapsedRealtimeNanos()
            val started = wall - (mono - firstTimestampNs)/1_000_000L
            val ended = wall - (mono - lastTimestampNs)/1_000_000L
            val chunk = JSONObject()
                .put("samples", samples)
                .put("window_started_at_ms", started)
                .put("window_ended_at_ms", ended)
            if (store?.append(chunk, ended) == true) {
                gateway.send("step_events", chunk)
            } else {
                report("逐步事件本地存储不可用，跳过上传")
            }
        }
        samples = JSONArray()
        firstTimestampNs = -1L
        lastTimestampNs = -1L
    }

    @Synchronized
    fun stop() {
        if (!active) return
        active = false
        manager.unregisterListener(this)
        flush()
        try { store?.finish("stopped_by_user") } catch (_: Exception) { }
        store = null
        thread?.quitSafely()
        thread = null
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
}
