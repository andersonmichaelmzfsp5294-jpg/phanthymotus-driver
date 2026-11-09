package com.example.heartrateweartest;

import com.samsung.android.service.health.tracking.data.DataPoint;
import com.samsung.android.service.health.tracking.data.HealthTrackerType;
import com.samsung.android.service.health.tracking.data.ValueKey;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Research-only conversion of actual Samsung SDK samples into bounded v3
 * transport payloads. Callback caller owns foreground user consent.
 * Keeps only a 48-row window, sends no data from partial BIA measurements.
 */
public final class SamsungV3Export {
    public interface Listener {
        void onChunk(String kind, JSONObject chunk);
    }

    private final Listener sink;
    private String recordingId;
    private String kind;
    private JSONArray rows = new JSONArray();
    private long firstTs = -1;
    private long lastTs = -1;
    private long lastAcceptedTs = -1;
    private boolean biaEmitted = false;

    public SamsungV3Export(Listener sink) { this.sink = sink; }

    public void start(HealthTrackerType type, String id) {
        flush();
        recordingId = id;
        kind = type == HealthTrackerType.PPG_CONTINUOUS ? "ppg_chunk"
            : type == HealthTrackerType.ECG_ON_DEMAND ? "ecg_chunk" : null;
        rows = new JSONArray();
        firstTs = -1;
        lastTs = -1;
        lastAcceptedTs = -1;
        biaEmitted = false;
    }

    public void sample(HealthTrackerType type, DataPoint point, Integer ecgLead) {
        if (type == HealthTrackerType.BIA_ON_DEMAND) {
            emitCompleteBia(point);
            return;
        }
        if (kind == null || point == null) return;
        long ts = point.getTimestamp();
        if (ts <= lastAcceptedTs || ts < 0) return;
        JSONArray row = new JSONArray();
        if ("ppg_chunk".equals(kind)) {
            Number green = goodPpg(point, ValueKey.PpgSet.PPG_GREEN,
                ValueKey.PpgSet.GREEN_STATUS);
            Number red = goodPpg(point, ValueKey.PpgSet.PPG_RED,
                ValueKey.PpgSet.RED_STATUS);
            Number ir = goodPpg(point, ValueKey.PpgSet.PPG_IR,
                ValueKey.PpgSet.IR_STATUS);
            if (green == null && red == null && ir == null) return;
            if (rows.length() == 0) firstTs = ts;
            long offset = ts - firstTs;
            if (offset < 0 || offset > 30000L) { flush(); firstTs = ts; offset = 0L; }
            row.put(offset).put(green != null ? green : JSONObject.NULL)
                .put(red != null ? red : JSONObject.NULL)
                .put(ir != null ? ir : JSONObject.NULL);
        } else if ("ecg_chunk".equals(kind)) {
            // SDK reports LEAD_OFF per batch: reject any disconnected-lead data.
            if (ecgLead == null || ecgLead != 0) return;
            Number mv = get(point, ValueKey.EcgSet.ECG_MV);
            if (mv == null || !Double.isFinite(mv.doubleValue())
                || Math.abs(mv.doubleValue()) > 50) return;
            if (rows.length() == 0) firstTs = ts;
            long offset = ts - firstTs;
            if (offset < 0 || offset > 30000L) { flush(); firstTs = ts; offset = 0L; }
            row.put(offset).put((Object) Double.valueOf(mv.doubleValue()));
        } else return;
        rows.put(row);
        lastTs = ts;
        lastAcceptedTs = ts;
        if (rows.length() >= 48) flush();
    }

    public void finish() {
        flush();
        recordingId = null;
        kind = null;
    }

    private void flush() {
        if (kind == null || recordingId == null || rows.length() < 2) {
            rows = new JSONArray(); firstTs = -1; lastTs = -1; return;
        }
        try {
            long first = rows.getJSONArray(0).getLong(0);
            long last = rows.getJSONArray(rows.length()-1).getLong(0);
            double rate = last > first ? (rows.length()-1)*1000.0/(last-first) : 0;
            if (rate >= 1 && rate <= 600) {
                JSONObject chunk = new JSONObject()
                    .put("recording_id", recordingId)
                    .put("sample_rate_hz", rate).put("samples", rows)
                    .put("window_started_at_ms", firstTs)
                    .put("window_ended_at_ms", lastTs);
                sink.onChunk(kind, chunk);
            }
        } catch (Exception ignored) { /* Cannot fabricate lost/invalid samples. */ }
        rows = new JSONArray(); firstTs = -1; lastTs = -1;
    }

    private void emitCompleteBia(DataPoint point) {
        if (biaEmitted) return;
        Integer status = get(point, ValueKey.BiaSet.STATUS);
        Number progress = get(point, ValueKey.BiaSet.PROGRESS);
        if (status == null || status != 0 || progress == null
            || progress.doubleValue() < .999) return;
        JSONObject stats = new JSONObject();
        add(stats, "body_fat_pct", get(point, ValueKey.BiaSet.BODY_FAT_RATIO), 0, 100);
        add(stats, "body_fat_mass_kg", get(point, ValueKey.BiaSet.BODY_FAT_MASS), 0, 200);
        add(stats, "skeletal_muscle_kg", get(point, ValueKey.BiaSet.SKELETAL_MUSCLE_MASS), 0, 200);
        add(stats, "total_body_water_kg", get(point, ValueKey.BiaSet.TOTAL_BODY_WATER), 0, 200);
        add(stats, "impedance_ohm", get(point, ValueKey.BiaSet.BODY_IMPEDANCE_MAGNITUDE),
            1, 10000);
        if (stats.length() == 0 || recordingId == null) return;
        try {
            sink.onChunk("bia_result", new JSONObject()
                .put("recording_id", recordingId).put("stats", stats));
            biaEmitted = true;
        } catch (org.json.JSONException ignored) {
            // A malformed local result must never become a network payload.
        }
    }

    private static void add(JSONObject target, String key, Number value,
                            double lower, double upper) {
        if (value == null) return;
        double v = value.doubleValue();
        if (Double.isFinite(v) && v >= lower && v <= upper) {
            try { target.put(key, v); }
            catch (org.json.JSONException ignored) { }
        }
    }

    private static <T> T get(DataPoint point, ValueKey<T> key) {
        try { return point.getValue(key); }
        catch (Exception ignored) { return null; }
    }

    private static Number goodPpg(DataPoint p, ValueKey<? extends Number> value,
                                  ValueKey<? extends Number> status) {
        Number code = get(p, status);
        Number raw = get(p, value);
        if (code == null || code.intValue() != 0 || raw == null) return null;
        return Double.isFinite(raw.doubleValue()) ? raw : null;
    }
}
