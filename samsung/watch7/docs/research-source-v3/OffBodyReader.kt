package com.example.heartrateweartest

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager

/** Reads the public low-latency off-body sensor when available. */
class OffBodyReader(context: Context, private val onWearState: (String) -> Unit) :
    SensorEventListener {
    private val manager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    private val offBody = manager.getDefaultSensor(Sensor.TYPE_LOW_LATENCY_OFFBODY_DETECT)
    private var registered = false

    fun start(): Boolean {
        if (registered) return true
        registered = offBody?.let {
            manager.registerListener(this, it, SensorManager.SENSOR_DELAY_NORMAL)
        } ?: false
        if (!registered) onWearState("unknown")
        return registered
    }

    fun stop() {
        if (registered) manager.unregisterListener(this)
        registered = false
    }

    override fun onSensorChanged(event: SensorEvent) {
        if (event.sensor.type != Sensor.TYPE_LOW_LATENCY_OFFBODY_DETECT) return
        val value = event.values.firstOrNull() ?: return
        // Android definition: 1 = on-body, 0 = off-body.
        onWearState(if (value >= 0.5f) "on_body" else "off_body")
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
}
