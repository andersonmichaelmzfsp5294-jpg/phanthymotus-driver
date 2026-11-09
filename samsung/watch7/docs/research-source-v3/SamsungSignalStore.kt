package com.example.heartrateweartest

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.UUID

/**
 * User-owned, app-private JSONL recording with bounded local footprint.
 * A manifest is emitted only after close; no ROS2/MCP/LLM raw-waveform upload.
 */
class SamsungSignalStore(context: Context, val signal: String) {
    companion object {
        private const val MAX_SESSION_BYTES = 5L * 1024L * 1024L
        private const val MAX_ROWS = 30_000
        private const val MAX_TOTAL_BYTES = 32L * 1024L * 1024L
        private const val RETENTION_MS = 7L * 24L * 3600L * 1000L
    }

    val recordingId: String = UUID.randomUUID().toString()
    private val folder = File(context.filesDir, "samsung_recordings").apply { mkdirs() }
    private val dataFile = File(folder, "$recordingId.jsonl")
    private val manifestFile = File(folder, "$recordingId.manifest.json")
    private val digest = MessageDigest.getInstance("SHA-256")
    private val stream: FileOutputStream
    private val startedAtMs = System.currentTimeMillis()
    private var count = 0
    private var bytes = 0L
    private var firstTimestampMs = -1L
    private var lastTimestampMs = -1L
    private var completed = false

    init {
        require(signal.matches(Regex("[a-z_0-9]{1,32}")))
        prune()
        stream = FileOutputStream(dataFile)
    }

    @Synchronized
    fun append(record: JSONObject, timestampMs: Long): Boolean {
        if (completed || count >= MAX_ROWS) return false
        val line = (record.toString() + "\n").toByteArray(Charsets.UTF_8)
        if (bytes + line.size > MAX_SESSION_BYTES) return false
        stream.write(line)
        digest.update(line)
        bytes += line.size
        count++
        if (timestampMs >= 0) {
            if (firstTimestampMs < 0) firstTimestampMs = timestampMs
            lastTimestampMs = timestampMs
        }
        return true
    }

    @Synchronized
    fun finish(outcome: String): JSONObject {
        require(outcome.matches(Regex("[A-Za-z0-9_:-]{1,96}")))
        if (completed) return readManifest()
        completed = true
        stream.fd.sync()
        stream.close()
        val elapsedMs = System.currentTimeMillis() - startedAtMs
        val durationMs = (lastTimestampMs - firstTimestampMs).coerceAtLeast(0)
        val rate = if (count > 1 && durationMs > 0)
            (count - 1) * 1000.0 / durationMs else null
        val sha = digest.digest().joinToString("") { "%02x".format(it) }
        val manifest = JSONObject()
            .put("recording_id", recordingId)
            .put("signal", signal)
            .put("status", outcome)
            .put("source", "watch7_real")
            .put("stored_on_watch", true)
            .put("sample_count", count)
            .put("size_bytes", bytes)
            .put("sha256", sha)
            .put("started_at_ms", startedAtMs)
            .put("duration_ms", elapsedMs)
            .put("first_timestamp_ms", firstTimestampMs)
            .put("last_timestamp_ms", lastTimestampMs)
            .put("estimated_rate_hz", rate ?: JSONObject.NULL)
            .put("file", dataFile.name)
        FileOutputStream(manifestFile).use { out ->
            out.write(manifest.toString(2).toByteArray(Charsets.UTF_8))
            out.fd.sync()
        }
        prune()
        return manifest
    }

    private fun readManifest(): JSONObject =
        if (manifestFile.isFile) JSONObject(manifestFile.readText(Charsets.UTF_8))
        else JSONObject()

    private fun prune() {
        val now = System.currentTimeMillis()
        folder.listFiles()?.filter { it.isFile && now - it.lastModified() > RETENTION_MS }
            ?.forEach { it.delete() }
        val files = folder.listFiles()?.filter { it.isFile }
            ?.sortedByDescending { it.lastModified() } ?: return
        var size = 0L
        files.forEach { item ->
            size += item.length()
            if (size > MAX_TOTAL_BYTES && item != dataFile && item != manifestFile) {
                item.delete()
            }
        }
    }
}
