package com.example.heartrateweartest;

import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import com.samsung.android.service.health.tracking.ConnectionListener;
import com.samsung.android.service.health.tracking.HealthTracker;
import com.samsung.android.service.health.tracking.HealthTrackerException;
import com.samsung.android.service.health.tracking.HealthTrackingService;
import com.samsung.android.service.health.tracking.data.DataPoint;
import com.samsung.android.service.health.tracking.data.HealthTrackerType;
import com.samsung.android.service.health.tracking.data.PpgType;
import com.samsung.android.service.health.tracking.data.TrackerUserProfile;
import com.samsung.android.service.health.tracking.data.ValueKey;

import org.json.JSONArray;
import org.json.JSONObject;
import java.util.EnumSet;
import java.util.List;

/**
 * Samsung SDK research adapter. One explicit on-watch measurement at a time.
 * Never bypass SDK_POLICY_ERROR, SSENSOR, signatures or device permissions.
 * Raw values go exclusively to app-private SamsungSignalStore.
 */
public final class SamsungTrackerBridge {
    public interface Listener {
        void onStatus(String state, String recordingId, int samples);
        /** Called only for bounded, status-validated scalar SDK results. */
        default void onScalar(String metric, double value, long timestampMs) { }
        /** Manifest only; never contains raw waveform samples. */
        default void onManifest(JSONObject manifest) { }
        /** Explicitly authorized v3 raw chunks or a completed BIA result only. */
        default void onV3Chunk(String kind, JSONObject payload) { }
    }

    private static final String TAG = "SamsungTracker";
    private static final String EXTRA =
        "com.samsung.android.hardware.sensormanager.permission.READ_ADDITIONAL_HEALTH_DATA";
    private static final String HEART = "android.permission.health.READ_HEART_RATE";
    private static final String OXYGEN = "android.permission.health.READ_OXYGEN_SATURATION";
    private static final String SKIN = "android.permission.health.READ_SKIN_TEMPERATURE";

    private final Context context;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private HealthTrackingService service;
    private HealthTracker tracker;
    private List<HealthTrackerType> supported;
    private HealthTrackerType mode;
    private SamsungSignalStore store;
    private final SamsungV3Export v3Export;
    private String status = "disconnected";
    private boolean connected = false;
    private int count = 0;
    private boolean finished = false;
    private final Runnable timeout = () -> stop("timeout_no_sdk_completion");

    public SamsungTrackerBridge(Context context, Listener listener) {
        this.context = context;
        this.listener = listener;
        this.v3Export = new SamsungV3Export(
            (kind, payload) -> listener.onV3Chunk(kind, payload));
    }

    public void connect() {
        if (service != null) return;
        status = "connecting";
        report();
        try {
            service = new HealthTrackingService(new ConnectionListener() {
                @Override public void onConnectionSuccess() {
                    main.post(() -> {
                        connected = true;
                        try {
                            supported = service.getTrackingCapability().getSupportHealthTrackerTypes();
                            status = "ready";
                        } catch (Exception e) {
                            status = "capability_error_" + e.getClass().getSimpleName();
                        }
                        report();
                    });
                }
                @Override public void onConnectionEnded() {
                    main.post(() -> {
                        stop("sdk_connection_ended");
                        connected = false;
                        status = "disconnected";
                        report();
                    });
                }
                @Override public void onConnectionFailed(HealthTrackerException error) {
                    main.post(() -> {
                        status = "sdk_connect_error_" + error.getErrorCode();
                        report();
                    });
                }
            }, context);
            service.connectService();
        } catch (Exception e) {
            status = "sdk_connect_exception_" + e.getClass().getSimpleName();
            report();
        }
    }

    public boolean isSupported(HealthTrackerType type) {
        return connected && supported != null && supported.contains(type);
    }

    public boolean isMeasuring() { return tracker != null; }
    public String currentState() { return status; }

    public static String permissionFor(HealthTrackerType type) {
        if (type == HealthTrackerType.HEART_RATE_CONTINUOUS) return HEART;
        if (type == HealthTrackerType.SPO2_ON_DEMAND) return OXYGEN;
        if (type == HealthTrackerType.SKIN_TEMPERATURE_ON_DEMAND ||
            type == HealthTrackerType.SKIN_TEMPERATURE_CONTINUOUS) return SKIN;
        return EXTRA;
    }

    public void start(HealthTrackerType type, TrackerUserProfile profile) {
        if (WatchRuntime.INSTANCE.getCollecting()) {
            status = "stop_basic_monitor_before_advanced_measurement";
            report();
            return;
        }
        if (tracker != null || !isSupported(type) || !connected) {
            status = "unsupported_or_busy";
            report();
            return;
        }
        if (context.checkSelfPermission(permissionFor(type)) != PackageManager.PERMISSION_GRANTED) {
            status = "sdk_permission_denied";
            report();
            return;
        }
        if (type == HealthTrackerType.BIA_ON_DEMAND && profile == null) {
            status = "bia_profile_and_contraindication_required";
            report();
            return;
        }
        mode = type;
        count = 0;
        finished = false;
        status = "starting";
        try {
            String signal = nameFor(type);
            store = new SamsungSignalStore(context, signal);
            v3Export.start(type, store.getRecordingId());
            if (type == HealthTrackerType.PPG_CONTINUOUS) {
                tracker = service.getHealthTracker(type, EnumSet.of(
                    PpgType.GREEN, PpgType.RED, PpgType.IR));
            } else if (type == HealthTrackerType.BIA_ON_DEMAND) {
                tracker = service.getHealthTracker(type, profile);
            } else {
                tracker = service.getHealthTracker(type);
            }
            tracker.setEventListener(new HealthTracker.TrackerEventListener() {
                @Override public void onDataReceived(List<DataPoint> points) {
                    main.post(() -> receive(points));
                }
                @Override public void onError(HealthTracker.TrackerError error) {
                    main.post(() -> stop("sdk_tracker_error"));
                }
                @Override public void onFlushCompleted() { }
            });
            status = "measuring";
            main.postDelayed(timeout, duration(type));
        } catch (Exception e) {
            status = "sdk_start_failed_" + e.getClass().getSimpleName();
            Log.w(TAG, "Samsung measurement failed to start: " + e.getClass().getSimpleName());
            stop(status);
        }
        report();
    }

    private void receive(List<DataPoint> points) {
        if (tracker == null || store == null || finished || points == null) return;
        Integer batchLead = mode == HealthTrackerType.ECG_ON_DEMAND && !points.isEmpty()
            ? maybe(points.get(0), ValueKey.EcgSet.LEAD_OFF) : null;
        for (DataPoint point : points) {
            JSONObject row = new JSONObject();
            try {
                long timestamp = point.getTimestamp();
                row.put("timestampMs", timestamp);
                boolean completed = false;
                if (mode == HealthTrackerType.PPG_CONTINUOUS) {
                    row.put("green", val(maybe(point, ValueKey.PpgSet.PPG_GREEN)));
                    row.put("red", val(maybe(point, ValueKey.PpgSet.PPG_RED)));
                    row.put("ir", val(maybe(point, ValueKey.PpgSet.PPG_IR)));
                    row.put("greenStatus", val(maybe(point, ValueKey.PpgSet.GREEN_STATUS)));
                    row.put("redStatus", val(maybe(point, ValueKey.PpgSet.RED_STATUS)));
                    row.put("irStatus", val(maybe(point, ValueKey.PpgSet.IR_STATUS)));
                } else if (mode == HealthTrackerType.HEART_RATE_CONTINUOUS) {
                    row.put("bpm", val(maybe(point, ValueKey.HeartRateSet.HEART_RATE)));
                    row.put("hrStatus", val(maybe(point, ValueKey.HeartRateSet.HEART_RATE_STATUS)));
                    row.put("ibiMs", val(maybe(point, ValueKey.HeartRateSet.IBI_LIST)));
                    row.put("ibiStatus", val(maybe(point, ValueKey.HeartRateSet.IBI_STATUS_LIST)));
                } else if (mode == HealthTrackerType.ECG_ON_DEMAND) {
                    row.put("ecgMv", val(maybe(point, ValueKey.EcgSet.ECG_MV)));
                    row.put("sequence", val(maybe(point, ValueKey.EcgSet.SEQUENCE)));
                    // LEAD_OFF belongs to the first sample of each SDK batch.
                    row.put("batchLeadOff", val(batchLead));
                } else if (mode == HealthTrackerType.SPO2_ON_DEMAND) {
                    Integer sdkStatus = maybe(point, ValueKey.SpO2Set.STATUS);
                    Integer spo2 = maybe(point, ValueKey.SpO2Set.SPO2);
                    row.put("status", val(sdkStatus));
                    row.put("spo2Percent", val(spo2));
                    row.put("bpm", val(maybe(point, ValueKey.SpO2Set.HEART_RATE)));
                    completed = sdkStatus != null && sdkStatus == 2 &&
                        spo2 != null && spo2 > 0 && spo2 <= 100;
                } else if (mode == HealthTrackerType.SKIN_TEMPERATURE_ON_DEMAND ||
                           mode == HealthTrackerType.SKIN_TEMPERATURE_CONTINUOUS) {
                    row.put("objectTempC", val(maybe(point,
                        ValueKey.SkinTemperatureSet.OBJECT_TEMPERATURE)));
                    row.put("ambientTempC", val(maybe(point,
                        ValueKey.SkinTemperatureSet.AMBIENT_TEMPERATURE)));
                    row.put("status", val(maybe(point, ValueKey.SkinTemperatureSet.STATUS)));
                } else if (mode == HealthTrackerType.BIA_ON_DEMAND) {
                    Integer sdkStatus = maybe(point, ValueKey.BiaSet.STATUS);
                    Float progress = maybe(point, ValueKey.BiaSet.PROGRESS);
                    Float fat = maybe(point, ValueKey.BiaSet.BODY_FAT_RATIO);
                    row.put("status", val(sdkStatus));
                    row.put("progress", val(progress));
                    row.put("bodyFatPercent", val(fat));
                    row.put("bodyFatMassKg", val(maybe(point, ValueKey.BiaSet.BODY_FAT_MASS)));
                    row.put("skeletalMuscleMassKg", val(maybe(point,
                        ValueKey.BiaSet.SKELETAL_MUSCLE_MASS)));
                    row.put("totalBodyWaterKg", val(maybe(point, ValueKey.BiaSet.TOTAL_BODY_WATER)));
                    row.put("impedanceOhm", val(maybe(point,
                        ValueKey.BiaSet.BODY_IMPEDANCE_MAGNITUDE)));
                    completed = sdkStatus != null && sdkStatus == 0 && progress != null &&
                        progress >= 0.999f && fat != null && Float.isFinite(fat) && fat > 0;
                    if (sdkStatus != null && sdkStatus == 18) {
                        stop("bia_sdk_status_18_invalid");
                        return;
                    }
                }
                if (!store.append(row, timestamp)) {
                    stop("local_storage_limit");
                    return;
                }
                count++;
                // Persist locally before exposing any optional SDK signal.
                v3Export.sample(mode, point, batchLead);
                // Save locally *first*. The optional Research-only uploader
                // separately requires wearer opt-in and an on-body session.
                emitValidScalar(point, completed);
                if (completed) {
                    stop("complete_sdk_confirmed");
                    return;
                }
            } catch (Exception e) {
                stop("sdk_parse_or_storage_error");
                return;
            }
        }
        report();
    }

    private void emitValidScalar(DataPoint point, boolean completed) {
        if (mode == HealthTrackerType.HEART_RATE_CONTINUOUS) {
            List<?> values = maybe(point, ValueKey.HeartRateSet.IBI_LIST);
            List<?> statuses = maybe(point, ValueKey.HeartRateSet.IBI_STATUS_LIST);
            if (values == null || statuses == null || values.size() != statuses.size()) return;
            // Samsung documented IBI_STATUS=0 as the valid beat state.
            // One scalar represents the latest validated interval, not HRV.
            for (int i = values.size() - 1; i >= 0; i--) {
                if (!(values.get(i) instanceof Number) || !(statuses.get(i) instanceof Number)) {
                    continue;
                }
                int status = ((Number) statuses.get(i)).intValue();
                double ibi = ((Number) values.get(i)).doubleValue();
                if (status == 0 && ibi >= 200 && ibi <= 5000 && Double.isFinite(ibi)) {
                    listener.onScalar("ibi_ms", ibi, point.getTimestamp());
                    break;
                }
            }
        } else if (mode == HealthTrackerType.SPO2_ON_DEMAND && completed) {
            Integer measured = maybe(point, ValueKey.SpO2Set.SPO2);
            if (measured != null && measured > 0 && measured <= 100) {
                listener.onScalar("spo2_pct", measured.doubleValue(), point.getTimestamp());
            }
        } else if (mode == HealthTrackerType.SKIN_TEMPERATURE_ON_DEMAND ||
                   mode == HealthTrackerType.SKIN_TEMPERATURE_CONTINUOUS) {
            Integer state = maybe(point, ValueKey.SkinTemperatureSet.STATUS);
            Float temperature = maybe(point, ValueKey.SkinTemperatureSet.OBJECT_TEMPERATURE);
            // Samsung temperature STATUS=0 means a normal sample.
            if (state != null && state == 0 && temperature != null &&
                Float.isFinite(temperature) && temperature >= -20 && temperature <= 60) {
                listener.onScalar("skin_temperature_c", temperature.doubleValue(), point.getTimestamp());
            }
        }
    }

    private static Object val(Object value) {
        if (value == null) return JSONObject.NULL;
        if (value instanceof Float && !Float.isFinite((Float) value)) return JSONObject.NULL;
        if (value instanceof Double && !Double.isFinite((Double) value)) return JSONObject.NULL;
        if (value instanceof List<?>) return new JSONArray((List<?>) value);
        return value;
    }

    private static <T> T maybe(DataPoint point, ValueKey<T> key) {
        try { return point.getValue(key); }
        catch (Exception ignored) { return null; }
    }

    private static String nameFor(HealthTrackerType type) {
        switch (type) {
            case PPG_CONTINUOUS: return "ppg";
            case HEART_RATE_CONTINUOUS: return "ibi";
            case ECG_ON_DEMAND: return "ecg";
            case SPO2_ON_DEMAND: return "spo2";
            case SKIN_TEMPERATURE_ON_DEMAND: return "skin_temperature";
            case SKIN_TEMPERATURE_CONTINUOUS: return "skin_temperature";
            case BIA_ON_DEMAND: return "bia";
            default: return "unsupported";
        }
    }

    private static long duration(HealthTrackerType type) {
        switch (type) {
            case PPG_CONTINUOUS: return 30_000;
            case HEART_RATE_CONTINUOUS: return 60_000;
            case SKIN_TEMPERATURE_CONTINUOUS: return 60_000;
            default: return 30_000;
        }
    }

    public void stop(String reason) {
        main.removeCallbacks(timeout);
        if (tracker != null) {
            try { tracker.unsetEventListener(); }
            catch (Exception e) { Log.w(TAG, "Tracker listener stop failed"); }
            tracker = null;
        }
        if (store != null && !finished) {
            finished = true;
            v3Export.finish();
            try {
                JSONObject manifest = store.finish(reason.replaceAll("[^A-Za-z0-9_:-]", "_"));
                // Only a completed local PPG recording can produce index
                // metadata. ECG, BIA and other records never leave the Watch.
                if (mode == HealthTrackerType.PPG_CONTINUOUS) {
                    listener.onManifest(manifest);
                }
            } catch (Exception e) { status = "local_persist_failed"; }
        }
        if (!status.equals("local_persist_failed")) {
            status = count == 0 && reason.startsWith("complete")
                ? "no_samples" : reason;
        }
        report();
    }

    public void close() {
        stop("user_closed");
        if (service != null) {
            try { service.disconnectService(); }
            catch (Exception ignored) { }
            service = null;
        }
        connected = false;
    }

    private void report() {
        listener.onStatus(status, store == null ? "" : store.getRecordingId(), count);
    }
}
