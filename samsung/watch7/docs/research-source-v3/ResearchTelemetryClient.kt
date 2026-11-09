package com.example.heartrateweartest

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicLong

/**
 * Research flavor only. Never initiates Samsung sampling or transfers waveforms.
 * Transmits explicitly opted-in bounded scalars or PPG recording metadata over
 * the already paired Watch7 v2 ingress. Fail closed on unknown/off-body wear.
 *
 * Jobs are serialized: active device_state -> scalar/manifest -> inactive state.
 * No persistent queue, auto-retry, remote trigger or consent persistence.
 */
class ResearchTelemetryClient(
    private val context: Context,
    private val report: (String) -> Unit,
) {
    private val io = Executors.newSingleThreadExecutor()
    private var session = UUID.randomUUID().toString()
    private val stateSeq = AtomicLong()
    private val metricSeq = AtomicLong()
    private val manifestSeq = AtomicLong()
    @Volatile private var active = false
    @Volatile private var scalarEnabled = false
    @Volatile private var manifestEnabled = false
    @Volatile private var wear = "unknown"
    private var lastStateAt = 0L

    fun updateWear(state: String) {
        if (state !in setOf("on_body", "off_body", "unknown")) return
        wear = state
        if (active && state == "off_body") {
            // Synchronously close the consent window for any new calls.
            active = false
            io.execute { sendState(false, false) }
            report("摘下手表：停止高级指标上传")
        }
    }

    fun begin(allowScalar: Boolean, allowManifest: Boolean): Boolean {
        if (active) return false
        if (!allowScalar && !allowManifest) return false
        if (wear != "on_body") {
            report("佩戴状态未确认，未开启上传")
            return false
        }
        if (credentials() == null) {
            report("研究版尚未配置有效网关地址及设备密钥")
            return false
        }
        session = UUID.randomUUID().toString()
        stateSeq.set(0L)
        metricSeq.set(0L)
        manifestSeq.set(0L)
        lastStateAt = 0L
        scalarEnabled = allowScalar
        manifestEnabled = allowManifest
        active = true
        report("仅本次研究测量授权：标量=${allowScalar}，PPG清单=${allowManifest}")
        return true
    }

    fun scalar(metric: String, value: Double, sampleTimestampMs: Long) {
        if (!active || !scalarEnabled || wear != "on_body") return
        val (unit, limits) = when (metric) {
            "ibi_ms" -> "ms" to (200.0..5000.0)
            "spo2_pct" -> "%" to (1.0..100.0)
            "skin_temperature_c" -> "degC" to (-20.0..60.0)
            else -> return
        }
        if (!value.isFinite() || value !in limits) return
        // SDK data may be delivered as a batch. Never relabel old samples
        // as fresh by replacing their acquisition time with upload time.
        val observedAge = System.currentTimeMillis() - sampleTimestampMs
        if (observedAge !in 0L..15_000L) {
            report("跳过无法确认新鲜度的 SDK 指标：$metric")
            return
        }
        val sessionAtCall = session
        io.execute {
            if (!active || wear != "on_body" || session != sessionAtCall) return@execute
            val now = System.currentTimeMillis()
            val age = now - sampleTimestampMs
            if (age !in 0L..15_000L || !ensureLiveHeartbeat(now)) return@execute
            val record = JSONObject()
                .put("value", value).put("unit", unit).put("quality", "valid")
                .put("provider", "samsung_sdk").put("age_ms", age)
            val frame = base("summary", metricSeq.getAndIncrement(), now)
                .put("metrics", JSONObject().put(metric, record))
            if (send(frame)) report("高级指标已同步：$metric")
            else report("高级指标上传失败；本地测量记录保留")
        }
    }

    fun ppgManifest(local: JSONObject) {
        if (!active || !manifestEnabled || wear != "on_body") return
        if (local.optString("signal") != "ppg" ||
            local.optString("status") !in setOf("timeout_no_sdk_completion", "stopped_by_user")) return
        val count = local.optInt("sample_count", 0)
        val rate = local.optDouble("estimated_rate_hz", Double.NaN)
        val id = local.optString("recording_id")
        if (count !in 1..2_000_000 || !rate.isFinite() || rate !in 0.1..2000.0 ||
            !id.matches(Regex("[A-Za-z0-9._:-]{1,96}"))
        ) return
        val sessionAtCall = session
        io.execute {
            if (!active || wear != "on_body" || session != sessionAtCall) return@execute
            val now = System.currentTimeMillis()
            if (!ensureLiveHeartbeat(now)) return@execute
            var ok = 0
            for (signal in listOf("ppg_green", "ppg_red", "ppg_ir")) {
                val frame = base("signal_manifest", manifestSeq.getAndIncrement(),
                    System.currentTimeMillis())
                    .put("recording_id", id).put("signal", signal)
                    .put("sample_count", count).put("sample_rate_hz", rate)
                    .put("stored_on_watch", true)
                if (send(frame)) ok++
            }
            report(if (ok == 3) "PPG录制清单已同步（波形留在手表）"
                   else "PPG清单未全部同步；原始波形仍留在手表")
        }
    }

    fun finish() {
        if (!active) return
        val oldSession = session
        // Work already queued by SDK callbacks must finish in order.
        io.execute {
            if (session == oldSession) {
                sendState(false, false)
                active = false
                scalarEnabled = false
                manifestEnabled = false
            }
        }
    }

    fun close() {
        finish()
        io.shutdown() // queued work finishes in order, no new measurements
    }

    private fun ensureLiveHeartbeat(now: Long): Boolean {
        if (wear != "on_body") return false
        if (now - lastStateAt <= 3_000L) return true
        return sendState(true, manifestEnabled).also {
            if (it) lastStateAt = now
        }
    }

    private fun base(kind: String, seq: Long, now: Long) = JSONObject()
        .put("protocol_version", 2).put("device_id", "watch7")
        .put("source", "watch7_real").put("session_id", session)
        .put("kind", kind).put("seq", seq).put("captured_at_ms", now)
        .put("wear_state", wear)

    private fun sendState(collecting: Boolean, indexAllowed: Boolean): Boolean {
        // No fabricated on-body state after removing the Watch.
        val now = System.currentTimeMillis()
        return send(base("device_state", stateSeq.getAndIncrement(), now)
            .put("online", collecting).put("collecting", collecting)
            .put("recording_manifest_enabled", collecting && indexAllowed &&
                wear == "on_body"))
    }

    private data class Pairing(val endpoint: String, val token: String)
    private fun credentials(): Pairing? {
        val prefs = context.getSharedPreferences("watch7_network", Context.MODE_PRIVATE)
        val url = prefs.getString("url", "").orEmpty().trimEnd('/')
        val token = prefs.getString("credential", "").orEmpty()
        if (token.length < 16 || url.length > 200) return null
        val uri = try { URI(url) } catch (_: Exception) { return null }
        if (uri.scheme !in setOf("http", "https") || uri.host.isNullOrBlank()
            || uri.userInfo != null || (uri.path != "" && uri.path != "/")
            || uri.query != null || uri.fragment != null) return null
        return Pairing(url, token)
    }

    private fun send(frame: JSONObject): Boolean {
        val pairing = credentials() ?: return false
        var conn: HttpURLConnection? = null
        try {
            conn = URL(pairing.endpoint + "/v2/telemetry").openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.connectTimeout = 3500
            conn.readTimeout = 3500
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("X-Wearable-Token", pairing.token)
            conn.outputStream.use { it.write(frame.toString().toByteArray(Charsets.UTF_8)) }
            if (conn.responseCode != 200) return false
            val body = conn.inputStream.bufferedReader().use { it.readText() }
            return JSONObject(body).optBoolean("ok", false)
        } catch (error: Exception) {
            Log.w("Watch7ResearchLink", "v2 upload unavailable: ${error.javaClass.simpleName}")
            return false
        } finally {
            conn?.disconnect()
        }
    }
}
