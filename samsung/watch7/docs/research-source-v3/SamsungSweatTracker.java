package com.example.heartrateweartest;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import com.samsung.android.service.health.tracking.ConnectionListener;
import com.samsung.android.service.health.tracking.HealthTracker;
import com.samsung.android.service.health.tracking.HealthTrackerException;
import com.samsung.android.service.health.tracking.HealthTrackingService;
import com.samsung.android.service.health.tracking.data.DataPoint;
import com.samsung.android.service.health.tracking.data.DataType;
import com.samsung.android.service.health.tracking.data.ExerciseState;
import com.samsung.android.service.health.tracking.data.ExerciseType;
import com.samsung.android.service.health.tracking.data.HealthTrackerType;
import com.samsung.android.service.health.tracking.data.TrackerUserProfile;
import com.samsung.android.service.health.tracking.data.ValueKey;
import java.util.List;

/**
 * Explicit running-only Samsung Sweat Loss tracker.
 * No scalar is produced until the user ends the tracked run and Samsung's
 * final SDK status is available. Android Health Services supplies actual SPM.
 */
public final class SamsungSweatTracker {
    public interface Listener {
        void onReady();
        void onStatus(String state);
        void onResult(int sdkStatus, Double sweatLossMl);
    }

    private final Context context;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private HealthTrackingService service;
    private HealthTracker tracker;
    private boolean exerciseStarted = false;
    private boolean ending = false;
    private boolean resultSent = false;

    public SamsungSweatTracker(Context context, Listener listener) {
        this.context = context;
        this.listener = listener;
    }

    public boolean connect(TrackerUserProfile profile) {
        if (service != null || tracker != null || profile == null) return false;
        try {
            service = new HealthTrackingService(new ConnectionListener() {
                @Override public void onConnectionSuccess() {
                    main.post(() -> {
                        try {
                            if (!service.getTrackingCapability()
                                .getSupportHealthTrackerTypes()
                                .contains(HealthTrackerType.SWEAT_LOSS)) {
                                listener.onStatus("sweat_tracker_not_supported");
                                close();
                                return;
                            }
                            tracker = service.getHealthTracker(
                                HealthTrackerType.SWEAT_LOSS, profile, ExerciseType.RUNNING);
                            tracker.setEventListener(new HealthTracker.TrackerEventListener() {
                                @Override public void onDataReceived(List<DataPoint> points) {
                                    main.post(() -> receive(points));
                                }
                                @Override public void onError(HealthTracker.TrackerError error) {
                                    main.post(() -> listener.onStatus("samsung_tracker_error_" + error));
                                }
                                @Override public void onFlushCompleted() { }
                            });
                            listener.onReady();
                        } catch (Exception error) {
                            listener.onStatus("sweat_sdk_unavailable_" +
                                error.getClass().getSimpleName());
                            close();
                        }
                    });
                }
                @Override public void onConnectionEnded() {
                    main.post(() -> {
                        listener.onStatus("sweat_sdk_connection_ended");
                        close();
                    });
                }
                @Override public void onConnectionFailed(HealthTrackerException error) {
                    main.post(() -> {
                        listener.onStatus("sweat_sdk_connection_failed");
                        close();
                    });
                }
            }, context);
            service.connectService();
            return true;
        } catch (Exception ignored) {
            listener.onStatus("sweat_sdk_connect_failed");
            close();
            return false;
        }
    }

    public boolean beginExercise() {
        if (tracker == null || exerciseStarted) return false;
        try {
            tracker.setExerciseState(ExerciseState.START);
            exerciseStarted = true;
            listener.onStatus("sweat_running_exercise_active");
            return true;
        } catch (Exception ignored) {
            listener.onStatus("sweat_exercise_state_start_failed");
            return false;
        }
    }

    public boolean feedCadence(float spm, long utcTimestampMs) {
        if (!exerciseStarted || ending || tracker == null ||
            !Float.isFinite(spm) || spm < 0 || spm > 450 ||
            utcTimestampMs <= 0) return false;
        try {
            tracker.setExerciseData(DataType.STEP_PER_MINUTE,
                new float[]{spm}, new long[]{utcTimestampMs});
            return true;
        } catch (Exception ignored) {
            listener.onStatus("sweat_sdk_spm_feed_failed");
            return false;
        }
    }

    public boolean stopExercise() {
        if (!exerciseStarted || tracker == null || ending) return false;
        ending = true;
        try {
            tracker.setExerciseState(ExerciseState.STOP);
            listener.onStatus("sweat_waiting_for_sdk_result");
            return true;
        } catch (Exception ignored) {
            listener.onStatus("sweat_exercise_state_stop_failed");
            return false;
        }
    }

    private void receive(List<DataPoint> points) {
        if (!ending || resultSent || points == null) return;
        for (DataPoint point : points) {
            try {
                Integer status = point.getValue(ValueKey.SweatLossSet.STATUS);
                Float amount = point.getValue(ValueKey.SweatLossSet.SWEAT_LOSS);
                if (status == null) continue;
                Double valid = null;
                if (status == 0 && amount != null && Float.isFinite(amount)
                    && amount > 0f && amount <= 10000f) valid = amount.doubleValue();
                if (status == 0 && valid == null) {
                    listener.onStatus("sweat_sdk_result_missing");
                    continue;
                }
                resultSent = true;
                listener.onResult(status, valid);
                return;
            } catch (Exception ignored) {
                listener.onStatus("sweat_result_parse_failed");
            }
        }
    }

    public void close() {
        HealthTracker closingTracker = tracker;
        tracker = null;
        if (closingTracker != null) {
            if (exerciseStarted && !ending) {
                try { closingTracker.setExerciseState(ExerciseState.STOP); }
                catch (Exception ignored) { }
            }
            try { closingTracker.unsetEventListener(); } catch (Exception ignored) { }
        }
        HealthTrackingService closingService = service;
        service = null;
        if (closingService != null) {
            try { closingService.disconnectService(); } catch (Exception ignored) { }
        }
        exerciseStarted = false;
        ending = false;
    }
}
