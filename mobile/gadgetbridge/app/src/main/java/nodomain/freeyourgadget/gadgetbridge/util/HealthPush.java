/*  Copyright (C) 2026  Gadgetbridge contributors

    This file is part of Gadgetbridge.

    Gadgetbridge is free software: you can redistribute it and/or modify
    it under the terms of the GNU Affero General Public License as published
    by the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    Gadgetbridge is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Affero General Public License for more details.

    You should have received a copy of the GNU Affero General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>. */
package nodomain.freeyourgadget.gadgetbridge.util;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;

import androidx.annotation.Nullable;
import androidx.localbroadcastmanager.content.LocalBroadcastManager;

import nodomain.freeyourgadget.gadgetbridge.GBApplication;
import nodomain.freeyourgadget.gadgetbridge.entities.XiaomiActivitySample;
import nodomain.freeyourgadget.gadgetbridge.impl.GBDevice;
import nodomain.freeyourgadget.gadgetbridge.model.ActivityKind;
import nodomain.freeyourgadget.gadgetbridge.model.BatteryState;

import org.json.JSONArray;
import org.json.JSONObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Pushes health readings to a user-configured HTTP endpoint as JSON.
 *
 * <p>Gadgetbridge can already export its database periodically, but the smallest
 * export interval is one hour — far too slow for reacting to a live reading.
 * This pushes readings the moment they arrive instead.
 *
 * <p>Where each metric comes from:
 * <ul>
 *   <li>{@code hr} — streamed live by the band, hooked in {@code XiaomiHealthService}</li>
 *   <li>{@code stress}, {@code spo2}, {@code steps}, {@code sleep} — written to the
 *       database, hooked in {@code XiaomiSampleProvider}. Nothing but HR is a live
 *       stream: stress and SpO2 arrive from on-demand measurements or periodic
 *       syncs, sleep only after a sync.</li>
 * </ul>
 *
 * <p>Design notes:
 * <ul>
 *   <li>Everything happens on a single background thread. The BLE callback that
 *       calls {@link #push} must never block on the network.</li>
 *   <li>All errors are swallowed and logged. A dead endpoint, no network or a
 *       wrong URL must never crash the device service.</li>
 *   <li>Repeated identical values are dropped, and there is a minimum interval
 *       between pushes, so a batch sync of a day's samples cannot flood the
 *       endpoint.</li>
 *   <li>The pending queue is bounded: if the endpoint is slow or dead we drop
 *       new work rather than grow an unbounded backlog.</li>
 * </ul>
 */
public final class HealthPush {
    private static final Logger LOG = LoggerFactory.getLogger(HealthPush.class);

    public static final String PREF_ENABLED = "health_push_enabled";
    public static final String PREF_URL = "health_push_url";
    public static final String PREF_TOKEN = "health_push_token";
    public static final String PREF_MIN_INTERVAL_S = "health_push_min_interval";

    private static final int CONNECT_TIMEOUT_MS = 3000;
    private static final int READ_TIMEOUT_MS = 3000;

    /** Drop new readings once this many are already waiting to be sent. */
    private static final int MAX_PENDING = 32;

    private static final ExecutorService POOL = Executors.newSingleThreadExecutor(r -> {
        final Thread t = new Thread(r, "HealthPush");
        t.setDaemon(true);
        return t;
    });

    /** metric name -> {last value, last push wall clock} */
    private static final Map<String, long[]> LAST = new ConcurrentHashMap<>();

    private static final AtomicInteger PENDING = new AtomicInteger();
    private static final AtomicInteger PUSHED = new AtomicInteger();
    private static final AtomicInteger FAILED = new AtomicInteger();
    private static final AtomicInteger DROPPED = new AtomicInteger();

    // ── real device connection status (never inferred from health data) ──────
    private static final AtomicBoolean RECEIVER_REGISTERED = new AtomicBoolean(false);
    private static final AtomicBoolean HEARTBEAT_STARTED = new AtomicBoolean(false);
    private static final ScheduledExecutorService HEARTBEAT =
            Executors.newSingleThreadScheduledExecutor(r -> {
                final Thread t = new Thread(r, "HealthPushHeartbeat");
                t.setDaemon(true);
                return t;
            });
    private static volatile JSONObject lastDevice = null;
    private static volatile String lastStatus = null;
    private static volatile boolean lastConnected = false;
    private static volatile long lastStatusAt = 0L;
    private static final Object DEVICE_LOCK = new Object();

    /** How often an idle but connected band still refreshes its status. */
    private static final long HEARTBEAT_SECONDS = 120L;

    private HealthPush() {
    }

    /**
     * Register a single LocalBroadcastManager receiver for GBDevice connection /
     * battery changes. Called from the existing Xiaomi service hook and lazily
     * from {@link #push}; it never blocks and never requests new permissions.
     */
    public static void ensureDeviceStatusReceiver() {
        if (RECEIVER_REGISTERED.get()) {
            return;
        }
        try {
            final Context context = GBApplication.getContext();
            if (context == null || !GBApplication.getPrefs().getBoolean(PREF_ENABLED, false)) {
                return;
            }
            final BroadcastReceiver receiver = new BroadcastReceiver() {
                @Override
                public void onReceive(final Context ctx, final Intent intent) {
                    if (intent == null || !GBDevice.ACTION_DEVICE_CHANGED.equals(intent.getAction())) {
                        return;
                    }
                    final GBDevice device = intent.getParcelableExtra(GBDevice.EXTRA_DEVICE);
                    reportDevice(device);
                }
            };
            LocalBroadcastManager.getInstance(context).registerReceiver(
                    receiver, new IntentFilter(GBDevice.ACTION_DEVICE_CHANGED));
            RECEIVER_REGISTERED.set(true);
        } catch (final Exception e) {
            LOG.debug("Health push: could not register device status receiver", e);
        }
    }

    /** Report a real GBDevice state change (connected/disconnected + battery). */
    public static void reportDevice(@Nullable final GBDevice device) {
        if (device == null) {
            return;
        }
        try {
            final boolean connected = device.isConnected();
            final String name = device.getAliasOrName();
            final String model = device.getModel();
            int battery = GBDevice.BATTERY_UNKNOWN;
            Boolean charging = null;
            try {
                battery = device.getBatteryLevel(0);
                final BatteryState state = device.getBatteryState(0);
                if (state == BatteryState.BATTERY_CHARGING
                        || state == BatteryState.BATTERY_CHARGING_FULL) {
                    charging = Boolean.TRUE;
                } else if (state == BatteryState.UNKNOWN) {
                    charging = null;
                } else {
                    charging = Boolean.FALSE;
                }
            } catch (final Exception ignored) {
                // fields unavailable on this device: leave null / unknown
            }
            pushDeviceStatus(name, model, connected, battery, charging,
                    System.currentTimeMillis() / 1000L);
        } catch (final Exception e) {
            LOG.debug("Health push: could not report device state", e);
        }
    }

    /**
     * Push an explicit device_status packet. The backend treats device status
     * separately from health data, so a recent heart rate never implies BLE is
     * connected. Name/model are user-visible strings; the MAC is never sent.
     */
    public static void pushDeviceStatus(@Nullable final String name,
                                        @Nullable final String model,
                                        final boolean connected,
                                        final int battery,
                                        @Nullable final Boolean charging,
                                        final long ts) {
        try {
            final Prefs prefs = GBApplication.getPrefs();
            if (prefs == null || !prefs.getBoolean(PREF_ENABLED, false)) {
                return;
            }
            final String url = prefs.getString(PREF_URL, "");
            if (url == null || url.trim().isEmpty()) {
                return;
            }
            final JSONObject body = buildStatusBody(name, model, connected, battery,
                    charging, ts);
            if (body == null) {
                return;
            }
            synchronized (DEVICE_LOCK) {
                lastStatus = connected ? "connected" : "disconnected";
                lastConnected = connected;
                lastStatusAt = ts;
                final JSONObject device = body.optJSONObject("device");
                if (device != null) {
                    lastDevice = device;
                }
            }
            enqueueBody(body, "device_status");
            startHeartbeat();
        } catch (final Exception e) {
            LOG.debug("Health push: device status push failed", e);
        }
    }

    private static JSONObject buildStatusBody(@Nullable final String name,
                                              @Nullable final String model,
                                              final boolean connected,
                                              final int battery,
                                              @Nullable final Boolean charging,
                                              final long ts) {
        try {
            final JSONObject body = new JSONObject();
            body.put("metric", "device_status");
            body.put("value", connected ? 1 : 0);
            body.put("text", connected ? "connected" : "disconnected");
            body.put("ts", ts);
            body.put("source", "gadgetbridge");
            final JSONObject device = new JSONObject();
            if (name != null && !name.isEmpty()) {
                device.put("name", name);
            }
            if (model != null && !model.isEmpty()) {
                device.put("model", model);
            }
            if (battery >= 0 && battery <= 100) {
                device.put("battery", battery);
            }
            if (charging != null) {
                device.put("charging", charging.booleanValue());
            }
            body.put("device", device);
            final JSONObject status = new JSONObject();
            status.put("status", connected ? "connected" : "disconnected");
            status.put("connected", connected);
            status.put("ts", ts);
            body.put("device_status", status);
            return body;
        } catch (final Exception e) {
            return null;
        }
    }

    private static void startHeartbeat() {
        if (!HEARTBEAT_STARTED.compareAndSet(false, true)) {
            return;
        }
        try {
            HEARTBEAT.scheduleAtFixedRate(() -> {
                try {
                    final Prefs prefs = GBApplication.getPrefs();
                    if (prefs == null || !prefs.getBoolean(PREF_ENABLED, false)) {
                        return;
                    }
                    final String url = prefs.getString(PREF_URL, "");
                    if (url == null || url.trim().isEmpty()) {
                        return;
                    }
                    final String status;
                    final boolean connected;
                    final JSONObject device;
                    final long ts;
                    synchronized (DEVICE_LOCK) {
                        status = lastStatus;
                        connected = lastConnected;
                        device = lastDevice;
                        ts = System.currentTimeMillis() / 1000L;
                    }
                    if (status == null) {
                        return;
                    }
                    // Heartbeat refreshes device status only; it must not create
                    // health samples, so data staleness is still visible.
                    final JSONObject body = new JSONObject();
                    body.put("metric", "device_status");
                    body.put("value", connected ? 1 : 0);
                    body.put("text", status);
                    body.put("ts", ts);
                    body.put("source", "gadgetbridge");
                    if (device != null) {
                        body.put("device", device);
                    }
                    final JSONObject statusObj = new JSONObject();
                    statusObj.put("status", status);
                    statusObj.put("connected", connected);
                    statusObj.put("ts", ts);
                    body.put("device_status", statusObj);
                    enqueueBody(body, "device_heartbeat");
                } catch (final Exception e) {
                    LOG.debug("Health push: heartbeat failed", e);
                }
            }, HEARTBEAT_SECONDS, HEARTBEAT_SECONDS, TimeUnit.SECONDS);
        } catch (final Exception e) {
            LOG.debug("Health push: could not start heartbeat", e);
        }
    }

    private static void enqueueBody(final JSONObject body, final String label) {
        try {
            final Prefs prefs = GBApplication.getPrefs();
            if (prefs == null || !prefs.getBoolean(PREF_ENABLED, false)) {
                return;
            }
            final String url = prefs.getString(PREF_URL, "");
            if (url == null || url.trim().isEmpty()) {
                return;
            }
            final String token = prefs.getString(PREF_TOKEN, "");
            if (PENDING.get() >= MAX_PENDING) {
                DROPPED.incrementAndGet();
                return;
            }
            PENDING.incrementAndGet();
            final String json = body.toString();
            POOL.execute(() -> {
                try {
                    doPostBody(url.trim(), token == null ? "" : token.trim(), json, label);
                } finally {
                    PENDING.decrementAndGet();
                }
            });
        } catch (final Exception e) {
            LOG.debug("Health push: could not queue {}", label, e);
        }
    }

    public static int getPushedCount() {
        return PUSHED.get();
    }

    public static int getFailedCount() {
        return FAILED.get();
    }

    public static int getDroppedCount() {
        return DROPPED.get();
    }

    /**
     * Queue a reading for delivery. Safe to call from any thread; returns
     * immediately.
     *
     * @param metric short metric name, e.g. {@code hr}, {@code stress}, {@code spo2}
     * @param value  the reading; non-positive values are ignored
     */
    public static void push(final String metric, final int value) {
        push(metric, value, null);
    }

    public static void push(final String metric, final int value, @Nullable final String text) {
        if (metric == null || value <= 0) {
            return;
        }
        final Prefs prefs;
        try {
            prefs = GBApplication.getPrefs();
        } catch (final Exception e) {
            return;                     // app not initialised yet
        }
        if (prefs == null || !prefs.getBoolean(PREF_ENABLED, false)) {
            return;
        }
        final String url = prefs.getString(PREF_URL, "");
        if (url == null || url.trim().isEmpty()) {
            return;
        }
        ensureDeviceStatusReceiver();
        final String token = prefs.getString(PREF_TOKEN, "");
        int minInterval = 10;
        try {
            minInterval = Integer.parseInt(prefs.getString(PREF_MIN_INTERVAL_S, "10").trim());
        } catch (final Exception ignored) {
        }
        // 去重键要带上 text：否则"跑步开始"和"跑步结束"的 value 都是 1，
        // 第二条会被当成重复值给丢掉。
        final String key = (text == null || text.isEmpty()) ? metric : metric + "|" + text;
        if (!shouldSend(key, value, minInterval)) {
            return;
        }
        if (PENDING.get() >= MAX_PENDING) {
            DROPPED.incrementAndGet();
            return;
        }
        PENDING.incrementAndGet();
        POOL.execute(() -> {
            try {
                doPost(url.trim(), token == null ? "" : token.trim(), metric, value, text);
            } finally {
                PENDING.decrementAndGet();
            }
        });
    }

    /**
     * Push a daily-summary row: resting heart rate, heart-rate range, average,
     * max and min stress and SpO2, training load, and so on.
     *
     * <p>These are the numbers that never appear in the real-time stream and are
     * not part of any individual activity sample — the band aggregates them per
     * day and Gadgetbridge stores them in {@code XiaomiDailySummarySample}.
     */
    public static void pushDailySummary(final String text) {
        push("daily_summary", 1, text);
    }

    /**
     * Push one manual measurement. {@code XiaomiManualSample} is type + value:
     * the band stores whatever the user measured on demand (temperature, SpO2,
     * stress, ...).
     */
    public static void pushManualSample(final int type, final int value, final long ts) {
        push("manual", value, "type=" + type + ",ts=" + ts);
    }

    /**
     * Push a whole batch of samples as one compact JSON array.
     *
     * <p>This is the "dumb pipe" mode: the phone stops deciding what matters and
     * simply forwards everything it has, and all thresholds, scenarios and
     * wording live on the receiving side. That way adding a new reaction — or
     * retuning one — never needs a new APK.
     *
     * <p>An earlier version forwarded only the newest sample of a batch, which
     * silently threw away an entire day of history; the per-sample path also
     * rate-limits, so most of a sync was lost.
     *
     * <p>Roughly 50 bytes per sample: a full day is about 70 KB, sent once per
     * sync.
     */
    public static void pushSampleBatch(@Nullable final List<XiaomiActivitySample> samples) {
        if (samples == null || samples.isEmpty()) {
            return;
        }
        try {
            final JSONArray arr = new JSONArray();
            for (final XiaomiActivitySample s : samples) {
                if (s == null) {
                    continue;
                }
                final JSONObject o = new JSONObject();
                o.put("t", s.getTimestamp());
                o.put("hr", s.getHeartRate());
                o.put("st", s.getStress());
                o.put("sp", s.getSpo2());
                o.put("step", s.getSteps());
                // 顺手一起发 —— 反正都在同一个样本里，现在不发以后就得为它们重编一次 APK
                o.put("cal", s.getActiveCalories());
                o.put("dist", s.getDistanceCm());
                o.put("int", Math.round(s.getIntensity()));
                o.put("en", s.getEnergy());
                final ActivityKind kind = s.getKind();
                o.put("k", kind != null ? kind.name() : "");
                arr.put(o);
            }
            if (arr.length() == 0) {
                return;
            }
            push("samples", arr.length(), arr.toString());
        } catch (final Exception e) {
            LOG.warn("Health push: could not build sample batch", e);
        }
    }

    /**
     * Push an event that carries no meaningful numeric value, such as a workout
     * starting or finishing. The {@code text} is what distinguishes events, and
     * is also what they de-duplicate on.
     */
    public static void pushEvent(final String metric, @Nullable final String text) {
        push(metric, 1, text);
    }

    /**
     * Push everything interesting a Xiaomi sample carries. Unset values (0, or
     * "not measured") are skipped by {@link #push}.
     *
     * @param includeSteps pass false for batch syncs, where the step counter
     *                     changes on every sample and is not worth reporting
     */
    public static void pushSample(@Nullable final XiaomiActivitySample sample,
                                  final boolean includeSteps) {
        if (sample == null) {
            return;
        }
        push("hr", sample.getHeartRate());
        push("stress", sample.getStress());
        push("spo2", sample.getSpo2());
        if (includeSteps) {
            push("steps", sample.getSteps());
        }
        final ActivityKind kind = sample.getKind();
        if (kind == null || kind == ActivityKind.UNKNOWN || kind == ActivityKind.NOT_MEASURED) {
            return;
        }
        // Only report sleep/wake, not every sport
        if (kind == ActivityKind.LIGHT_SLEEP || kind == ActivityKind.DEEP_SLEEP
                || kind == ActivityKind.REM_SLEEP || kind == ActivityKind.AWAKE_SLEEP) {
            push("sleep", kind.getCode(), kind.name());
        } else if (kind == ActivityKind.ACTIVITY || kind == ActivityKind.WALKING) {
            push("awake", kind.getCode(), kind.name());
        }
    }

    /**
     * Push one night's sleep as a single summary.
     *
     * <p>Sleep cannot be sent sample by sample: the stages only arrive with a
     * batch sync, which would mean thousands of pushes for one night, and the
     * per-sample hook deliberately only forwards the newest entry. Everything
     * needed for a sleep-quality judgement is already summed up by
     * {@code SleepStagesParser} though — total, deep, light, REM and the
     * minutes spent awake — so send that instead.
     *
     * @param totalMin  total sleep in minutes (also the metric value)
     * @param bedTime   epoch seconds the band considers real sleep to start
     * @param wakeupTime epoch seconds sleep ended
     */
    public static void pushSleepSummary(final int totalMin, final int deepMin, final int lightMin,
                                        final int remMin, final int awakeMin,
                                        final long bedTime, final long wakeupTime) {
        if (totalMin <= 0) {
            return;
        }
        final String text = String.format(java.util.Locale.US,
                "total=%d,deep=%d,light=%d,rem=%d,awake=%d,bed=%d,wake=%d",
                totalMin, deepMin, lightMin, remMin, awakeMin, bedTime, wakeupTime);
        push("sleep_summary", totalMin, text);
    }

    /**
     * Rate limit. Anything arriving sooner than {@code minInterval} seconds
     * after the last accepted reading for this metric is dropped, whatever its
     * value.
     *
     * <p>The check order matters: an earlier version pushed whenever the value
     * *changed*, and only applied the interval to repeated values. Heart rate
     * changes every single second, so that turned into one HTTP POST per second
     * — tens of thousands a day, and a pointless drain on the band and the
     * phone's radio.
     *
     * @return true if this reading should be sent
     */
    private static boolean shouldSend(final String metric, final int value, final int minInterval) {
        final long now = System.currentTimeMillis();
        final long[] prev = LAST.get(metric);
        if (prev == null) {
            LAST.put(metric, new long[]{value, now});
            return true;
        }
        if (now - prev[1] < minInterval * 1000L) {
            return false;
        }
        LAST.put(metric, new long[]{value, now});
        return true;
    }

    private static void doPost(final String url, final String token, final String metric,
                               final int value, @Nullable final String text) {
        try {
            final JSONObject body = new JSONObject();
            body.put("metric", metric);
            body.put("value", value);
            if (text != null && !text.isEmpty()) {
                body.put("text", text);
            }
            body.put("ts", System.currentTimeMillis() / 1000L);
            body.put("source", "gadgetbridge");
            ensureDeviceStatusReceiver();
            if (lastDevice != null) {
                // Attach the latest real device metadata (name/model/battery only).
                body.put("device", lastDevice);
            }
            doPostBody(url, token, body.toString(), metric);
        } catch (final Exception e) {
            FAILED.incrementAndGet();
            LOG.debug("Health push failed for {}: {}", metric, e.getMessage());
        }
    }

    private static void doPostBody(final String url, final String token,
                                   final String json, final String label) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(url).openConnection();
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(CONNECT_TIMEOUT_MS);
            conn.setReadTimeout(READ_TIMEOUT_MS);
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            if (!token.isEmpty()) {
                conn.setRequestProperty("X-Token", token);
            }
            final byte[] payload = json.getBytes(StandardCharsets.UTF_8);
            conn.setFixedLengthStreamingMode(payload.length);
            try (OutputStream os = conn.getOutputStream()) {
                os.write(payload);
            }
            final int code = conn.getResponseCode();
            if (code >= 200 && code < 300) {
                PUSHED.incrementAndGet();
            } else {
                FAILED.incrementAndGet();
                LOG.warn("Health push got HTTP {} for {}", code, label);
            }
            try (java.io.InputStream is = code >= 400 ? conn.getErrorStream() : conn.getInputStream()) {
                if (is != null) {
                    final byte[] buf = new byte[256];
                    //noinspection StatementWithEmptyBody
                    while (is.read(buf) > 0) {
                        // discard
                    }
                }
            } catch (final Exception ignored) {
            }
        } catch (final Exception e) {
            FAILED.incrementAndGet();
            LOG.debug("Health push failed for {}: {}", label, e.getMessage());
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }
}
