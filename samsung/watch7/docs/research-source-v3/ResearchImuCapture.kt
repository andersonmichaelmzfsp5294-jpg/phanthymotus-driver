package com.example.heartrateweartest

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Handler
import android.os.HandlerThread
import org.json.JSONArray
import org.json.JSONObject

/**
 * User-started bounded IMU windows; real Android sensor event timestamps.
 * Keep a local JSONL record; optional authorized v3 transport is separate.
 */
class ResearchImuCapture(
    private val context: Context,
    private val gateway: ResearchV3Gateway,
    private val report: (String) -> Unit
) : SensorEventListener {
    private val sensors = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    private var worker: HandlerThread? = null
    private var store: SamsungSignalStore? = null
    private var startNs = 0L
    private var lastGyroNs = 0L
    private var lastGyro = doubleArrayOf(0.0,0.0,0.0)
    private var events = JSONArray()
    private var lastSampleMs = -1L
    private var firstSampleNs = 0L
    private var lastSampleNs = 0L
    @Volatile private var capturing = false

    fun isCapturing(): Boolean = capturing

    fun start(): Boolean {
        if (capturing) return false
        val accel = sensors.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        val gyro = sensors.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
        if (accel == null || gyro == null) {
            report("此设备缺少加速度或陀螺仪")
            return false
        }
        store = SamsungSignalStore(context,"imu")
        val thread = HandlerThread("watch7-imu-research").apply { start() }
        worker = thread
        val handler = Handler(thread.looper)
        lastGyroNs=0L
        startNs=0L
        lastSampleMs=-1L
        events=JSONArray()
        capturing=true
        val gyroOk=sensors.registerListener(this,gyro,10000,handler)
        val accelOk=sensors.registerListener(this,accel,10000,handler)
        if (!gyroOk || !accelOk) {
            stop()
            report("IMU 注册失败")
            return false
        }
        report("IMU 已在前台采集，目标 100Hz（实际采样率以测量结果为准）")
        return true
    }

    @Synchronized
    override fun onSensorChanged(event: SensorEvent) {
        if (!capturing || event.values.size<3) return
        when(event.sensor.type) {
            Sensor.TYPE_GYROSCOPE -> {
                lastGyroNs=event.timestamp
                for(i in 0..2) lastGyro[i]=event.values[i].toDouble()
            }
            Sensor.TYPE_ACCELEROMETER -> {
                if (lastGyroNs==0L ||
                    kotlin.math.abs(event.timestamp-lastGyroNs)>50_000_000L) return
                if (startNs==0L) startNs=event.timestamp
                if (events.length()==0) firstSampleNs=event.timestamp
                val offsetMs=(event.timestamp-firstSampleNs)/1_000_000L
                if (offsetMs<=lastSampleMs || offsetMs>30_000L) {
                    if (offsetMs>30_000L) flush()
                    else return
                }
                if (events.length()==0) firstSampleNs=event.timestamp
                val offset=(event.timestamp-firstSampleNs)/1_000_000L
                if (events.length()>0 && offset<=lastSampleMs) return
                val row=JSONArray().put(offset).put(event.values[0].toDouble())
                    .put(event.values[1].toDouble()).put(event.values[2].toDouble())
                    .put(lastGyro[0]).put(lastGyro[1]).put(lastGyro[2])
                events.put(row)
                lastSampleMs=offset
                lastSampleNs=event.timestamp
                if (events.length()>=32) flush()
            }
        }
    }

    @Synchronized
    private fun flush() {
        val count=events.length()
        if (count<2) {
            events=JSONArray()
            lastSampleMs=-1
            return
        }
        val elapsedNs=lastSampleNs-firstSampleNs
        val hz=if (elapsedNs>0) (count-1)*1_000_000_000.0/elapsedNs else 0.0
        if (hz in 1.0..600.0) {
            // SensorEvent.timestamp uses elapsedRealtimeNanos() as its clock.
            // Preserve the true acquisition interval, independent of HTTP delay.
            val elapsedNow = android.os.SystemClock.elapsedRealtimeNanos()
            val wallNow = System.currentTimeMillis()
            val startWall = wallNow - (elapsedNow - firstSampleNs) / 1_000_000L
            val endWall = wallNow - (elapsedNow - lastSampleNs) / 1_000_000L
            val chunk=JSONObject().put("sample_rate_hz",hz).put("samples",events)
                .put("window_started_at_ms",startWall)
                .put("window_ended_at_ms",endWall)
            if (store?.append(chunk, System.currentTimeMillis()) == true)
                gateway.send("imu_window", chunk)
            else
                report("IMU 本地存储达到上限，跳过上传")
        }
        events=JSONArray()
        lastSampleMs=-1
    }

    @Synchronized
    fun stop() {
        if (!capturing) return
        capturing=false
        sensors.unregisterListener(this)
        flush()
        try { store?.finish("stopped_by_user") } catch (_: Exception) { }
        store=null
        worker?.quitSafely()
        worker=null
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
}
