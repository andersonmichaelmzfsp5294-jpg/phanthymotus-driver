package com.example.heartrateweartest

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.util.UUID
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.Executors

/**
 * Research-only, wearer-initiated v3 data plane. One sensor at a time.
 * Opt-in per session; no remote sensor activation, no background restart.
 * HTTPS is required outside isolated testing networks (deployment policy).
 */
class ResearchV3Gateway(
    private val context: Context,
    private val report: (String) -> Unit
) {
    private val io = Executors.newSingleThreadExecutor()
    private val pendingChunks = AtomicInteger(0)
    private val maxPendingChunks = 12
    private val ui = Handler(Looper.getMainLooper())
    private val allowed = setOf(
        "watch_imu_stream", "watch_ppg_raw", "watch_ecg",
        "watch_bia", "watch_location", "watch_audio",
        "watch_step_detector", "watch_sweat_loss"
    )
    private val kinds = mapOf(
        "imu_window" to "watch_imu_stream",
        "ppg_chunk" to "watch_ppg_raw", "ecg_chunk" to "watch_ecg",
        "bia_result" to "watch_bia", "location_fix" to "watch_location",
        "audio_chunk" to "watch_audio",
        "step_events" to "watch_step_detector",
        "sweat_loss_result" to "watch_sweat_loss"
    )
    private var session = UUID.randomUUID().toString()
    private val seq = mutableMapOf<String, Long>()
    private var sequenceState = 0L
    private var expiresAt = 0L
    private val heartbeat = object : Runnable {
        override fun run() {
            if (phase != "active") return
            if (System.currentTimeMillis() > expiresAt || wear != "on_body") {
                stop()
                return
            }
            val current = session
            io.execute { if (session == current && phase == "active") sendState(true) }
            ui.postDelayed(this, 5000)
        }
    }

    @Volatile private var wear = "unknown"
    @Volatile private var phase = "idle"
    @Volatile private var grant = ""

    fun isActive(): Boolean = phase != "idle"

    fun updateWear(value: String) {
        if (value !in setOf("on_body","off_body","unknown")) return
        wear = value
        if (value != "on_body") stop()
    }

    fun start(card: String, expiresAfterMs: Long = 90000): Boolean {
        if (card !in allowed || phase != "idle" || wear != "on_body") return false
        if (paired() == null) {
            report("v3：需 HTTPS 网关或 ADB 回环地址，以及有效配对密钥")
            return false
        }
        grant = card
        session = UUID.randomUUID().toString()
        synchronized(seq) { seq.clear(); sequenceState = 0 }
        expiresAt = System.currentTimeMillis() + expiresAfterMs.coerceIn(10000,120000)
        phase = "active"
        val current = session
        io.execute { if (phase == "active" && session == current) sendState(true) }
        ui.removeCallbacks(heartbeat)
        ui.postDelayed(heartbeat, 5000)
        report("v3 会话已开始："+card+"，仅本次手表操作")
        return true
    }

    fun send(kind: String, values: JSONObject) {
        if (phase != "active" || wear != "on_body" || kinds[kind] != grant) return
        val current = session
        // Deep copy only from bounded producer-generated JSON, never log values.
        if (pendingChunks.incrementAndGet() > maxPendingChunks) {
            pendingChunks.decrementAndGet()
            report("v3 网络拥塞：丢弃该数据块，避免积压")
            return
        }
        val copied = JSONObject(values.toString())
        try {
            io.execute {
                try {
                    // Drain already approved chunks before terminal stream_state.
                    if (phase !in setOf("active","closing") ||
                        session != current || wear != "on_body") return@execute
                    val now = System.currentTimeMillis()
                    if (now > expiresAt) return@execute
                    val frame = base(kind, next(kind), now)
                    val keys = copied.keys()
                    while (keys.hasNext()) {
                        val key = keys.next()
                        frame.put(key, copied.get(key))
                    }
                    if (frame.toString().toByteArray(Charsets.UTF_8).size > 24576) {
                        report("v3 块长度超出上限，跳过")
                        return@execute
                    }
                    if (!post(frame)) report("v3 数据未被 Driver 接收："+kind)
                } finally {
                    pendingChunks.decrementAndGet()
                }
            }
        } catch (_: java.util.concurrent.RejectedExecutionException) {
            pendingChunks.decrementAndGet()
        }
    }

    fun stop() {
        if (phase != "active") return
        phase = "closing"
        ui.removeCallbacks(heartbeat)
        val old = session
        io.execute {
            if (session == old) {
                sendState(false)
                grant = ""
                phase = "idle"
                report("v3 会话已结束")
            }
        }
    }

    fun close() {
        stop()
        ui.removeCallbacks(heartbeat)
        io.shutdown()
    }

    private fun next(kind: String): Long = synchronized(seq) {
        val next = seq.getOrDefault(kind, 0L)
        seq[kind] = next + 1
        next
    }

    private fun base(kind: String, seqNumber: Long, timeMs: Long): JSONObject =
        JSONObject().put("protocol_version",3).put("device_id","watch7")
            .put("source","watch7_real").put("kind",kind)
            .put("session_id",session).put("seq",seqNumber)
            .put("captured_at_ms",timeMs).put("wear_state",wear)

    private fun sendState(collecting: Boolean): Boolean {
        val seqNumber = synchronized(seq) { sequenceState++ }
        return post(base("stream_state",seqNumber,System.currentTimeMillis())
            .put("online", collecting && wear == "on_body")
            .put("collecting", collecting && wear == "on_body")
            .put("enabled_streams",
                JSONArray(if (collecting && wear == "on_body") listOf(grant) else emptyList<String>())))
    }

    private data class Pairing(val endpoint: String, val token: String)
    private fun paired(): Pairing? {
        val prefs = context.getSharedPreferences("watch7_network",Context.MODE_PRIVATE)
        val endpoint = prefs.getString("url","").orEmpty().trimEnd('/')
        val secret = prefs.getString("credential","").orEmpty()
        val uri = try { URI(endpoint) } catch (_: Exception) { return null }
        // Sensitive raw health, GPS and microphone data must not disclose
        // the pairing token or payload on an unencrypted shared LAN.
        // ADB reverse uses loopback HTTP; deployed remote ingress uses TLS.
        val localTunnel = uri.scheme == "http" && uri.host in
            setOf("127.0.0.1", "localhost")
        if (secret.length < 16 || endpoint.length > 200 ||
            !(uri.scheme == "https" || localTunnel) ||
            uri.userInfo != null || (uri.path != "" && uri.path != "/") ||
            uri.query != null || uri.fragment != null) return null
        return Pairing(endpoint,secret)
    }

    private fun post(frame: JSONObject): Boolean {
        val pairing = paired() ?: return false
        var conn: HttpURLConnection? = null
        try {
            conn = URL(pairing.endpoint + "/v3/telemetry").openConnection() as HttpURLConnection
            conn.requestMethod="POST"
            conn.connectTimeout=3000
            conn.readTimeout=3000
            conn.doOutput=true
            conn.setRequestProperty("Content-Type","application/json")
            conn.setRequestProperty("X-Wearable-Token",pairing.token)
            conn.outputStream.use { it.write(frame.toString().toByteArray(Charsets.UTF_8)) }
            if (conn.responseCode != 200) return false
            return JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                .optBoolean("ok",false)
        } catch (error: Exception) {
            Log.w("Watch7V3Gateway","Transport unavailable: "+error.javaClass.simpleName)
            return false
        } finally {
            conn?.disconnect()
        }
    }
}
