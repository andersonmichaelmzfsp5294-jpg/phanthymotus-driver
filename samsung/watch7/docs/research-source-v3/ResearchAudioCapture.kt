package com.example.heartrateweartest

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Base64
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.Executors

/** One explicit 3-second microphone recording; no always-on listening. */
class ResearchAudioCapture(
    private val context: Context,
    private val gateway: ResearchV3Gateway,
    private val report: (String) -> Unit,
    private val onFinished: () -> Unit
) {
    private val executor = Executors.newSingleThreadExecutor()
    @Volatile private var recording = false

    fun isRecording() = recording

    fun start(): Boolean {
        if (recording || context.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) return false
        val rate = 8000
        val minimum = AudioRecord.getMinBufferSize(
            rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minimum <= 0) return false
        val recorder = try {
            AudioRecord(MediaRecorder.AudioSource.MIC, rate,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minimum, 4096))
        } catch (_: Exception) { return false }
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            return false
        }
        recording = true
        val id = UUID.randomUUID().toString()
        executor.execute {
            val local = SamsungSignalStore(context, "audio")
            try {
                recorder.startRecording()
                val end = android.os.SystemClock.elapsedRealtime() + 3000L
                val sample = ShortArray(1024)
                while (recording && android.os.SystemClock.elapsedRealtime() < end) {
                    val count = recorder.read(sample, 0, sample.size,
                        AudioRecord.READ_BLOCKING)
                    if (count <= 0) break
                    val bytes = ByteArray(count * 2)
                    for (i in 0 until count) {
                        val value = sample[i].toInt()
                        bytes[2*i] = value.toByte()
                        bytes[2*i+1] = (value shr 8).toByte()
                    }
                    val duration = count * 1000 / rate
                    val chunk = JSONObject().put("recording_id", id)
                        .put("encoding", "pcm_s16le_mono")
                        .put("sample_rate_hz", rate)
                        .put("duration_ms", duration.coerceAtLeast(1))
                        .put("pcm_b64", Base64.encodeToString(bytes, Base64.NO_WRAP))
                    if (!local.append(chunk, System.currentTimeMillis())) break
                    gateway.send("audio_chunk", chunk)
                }
            } catch (_: Exception) {
                report("麦克风短录制失败")
            } finally {
                recording = false
                try { recorder.stop() } catch (_: Exception) { }
                recorder.release()
                try { local.finish("stopped_by_user") } catch (_: Exception) { }
                onFinished()
                report("本次录音已结束；数据保存在手表私有目录")
            }
        }
        return true
    }

    fun stop() { recording = false }
    fun close() { stop(); executor.shutdown() }
}
