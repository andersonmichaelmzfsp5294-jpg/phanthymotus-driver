package com.example.heartrateweartest

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder

/**
 * Research-only foreground health lifetime for the user-started running
 * measurement. No remote trigger and no auto-restart after process death.
 */
class ResearchSweatKeepAliveService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val notifications = getSystemService(NotificationManager::class.java)
        val channelId = "watch7_sweat_exercise"
        notifications.createNotificationChannel(NotificationChannel(
            channelId, "Wearable 研究跑步采集", NotificationManager.IMPORTANCE_LOW))
        val notification = Notification.Builder(this, channelId)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("Watch7 跑步汗液估计")
            .setContentText("正在跟踪本人启动的跑步运动，请在应用内结束")
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(2601, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_HEALTH)
        } else {
            startForeground(2601, notification)
        }
        return START_NOT_STICKY
    }
}
