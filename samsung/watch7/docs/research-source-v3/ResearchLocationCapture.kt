package com.example.heartrateweartest

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.LocationManager
import android.os.CancellationSignal
import org.json.JSONObject
import java.util.concurrent.Executor

/** Foreground, once-only GPS fix. No historical route or background tracking. */
class ResearchLocationCapture(
    private val context: Context,
    private val gateway: ResearchV3Gateway,
    private val report: (String) -> Unit,
    private val onFinished: () -> Unit,
) {
    fun once(): Boolean {
        if (context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) !=
            PackageManager.PERMISSION_GRANTED) {
            report("请在手表上授权精确定位后重试")
            return false
        }
        val manager=context.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        if (!manager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
            report("手表 GPS 未启用")
            return false
        }
        try {
            manager.getCurrentLocation(LocationManager.GPS_PROVIDER,
                CancellationSignal(), Executor { it.run() }) { fix ->
                if (fix==null || !fix.hasAccuracy() ||
                    fix.accuracy !in 0f..100000f ||
                    android.os.SystemClock.elapsedRealtimeNanos() -
                    fix.elapsedRealtimeNanos > 15_000_000_000L) {
                    report("GPS 无有效新鲜定位，未上传")
                } else {
                    val data=JSONObject().put("lat",fix.latitude).put("lon",fix.longitude)
                        .put("accuracy_m",fix.accuracy.toDouble()).put("provider","gps")
                    if (fix.hasSpeed()) data.put("speed_m_s",fix.speed.toDouble())
                    if (fix.hasAltitude()) data.put("altitude_m",fix.altitude)
                    gateway.send("location_fix",data)
                    report("GPS 获取到有效位置，已提交至研究网关")
                }
                onFinished()
            }
            return true
        } catch (_: SecurityException) {
            report("没有定位权限")
        } catch (_: Exception) {
            report("GPS 当前不可用")
        }
        return false
    }
}
