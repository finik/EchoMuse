package com.echomuse.rookpanel;

import android.app.Activity;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * The Echo Spot's screen: a clock, plus a ring that lights on wake word.
 *
 * The ring is the point. biscuit (Echo Dot) signals turn state on a physical
 * 12-LED ring; rook has no ring at all — its only LED-class device is
 * lcd-backlight — so the display has to carry that job or the device gives the
 * user no feedback whatsoever that it heard them. Drawing it as a ring around
 * the edge of a 480x480 ROUND panel is the closest analogue there is.
 *
 * State arrives from the EchoMuse daemon over a Unix socket, the same shape
 * crown_launcher uses for its overlay strip: the Go daemon cannot draw Android
 * UI, so it forwards its existing led.Controller calls here instead. One line
 * of JSON per update, newline-delimited.
 */
public class PanelActivity extends Activity {

    private static final String TAG = "rookpanel";

    /**
     * ABSTRACT namespace socket name (no filesystem path). LocalServerSocket's
     * String constructor only ever binds abstract, whatever the name looks
     * like — crown found this the hard way via /proc/<pid>/net/unix after ls
     * found nothing. It is the better namespace here anyway: nothing stale to
     * clean up after a crash, and no filesystem permission question between
     * the app uid and a daemon running as root.
     */
    static final String SOCKET_NAME = "com.echomuse.rookpanel/state";

    /** How long a tap holds the day brightness. The root script reads this. */
    private static final long TAP_HOLD_MS = 20_000;

    private PanelView view;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        // Keep the panel awake and lit — it is a clock; a blank clock is useless.
        // A wall panel must never be hidden behind a lockscreen, and on rook the
        // mute button reports KEY_POWER — so anything that reaches Android as a
        // power keypress raises the keyguard over the clock and the device looks
        // dead. SHOW_WHEN_LOCKED draws us above it, DISMISS_KEYGUARD removes it
        // outright (there is no secure lock configured), TURN_SCREEN_ON brings
        // the display back if it did sleep.
        getWindow().addFlags(
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
          | WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
          | WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD
          | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON);
        view = new PanelView(this);
        view.setOnTouchListener(new View.OnTouchListener() {
            @Override public boolean onTouch(View v, MotionEvent e) {
                if (e.getAction() == MotionEvent.ACTION_DOWN) holdBrightness();
                return true;
            }
        });
        setContentView(view);
        new Thread(new StateServer(view), "state-server").start();
        new Thread(new WeatherPoll(view), "weather").start();
    }

    /**
     * A tap asks the root backlight script for day brightness for TAP_HOLD_MS.
     *
     * Night dim used to be a window attribute set on the UI thread. That call
     * blocked inside the window manager at midnight and froze the clock on
     * the last frame, 00:00. The panel must not touch window brightness.
     * The file is in this app's own directory, which root can read and the
     * app can write. A failed write must not take the clock down with it.
     */
    private void holdBrightness() {
        try {
            java.io.File f = new java.io.File(getFilesDir(), "bright_until");
            java.io.FileOutputStream out = new java.io.FileOutputStream(f);
            out.write(Long.toString(System.currentTimeMillis() + TAP_HOLD_MS).getBytes("UTF-8"));
            out.close();
        } catch (Exception ignored) {
        }
    }

    // ── the drawing ──────────────────────────────────────────────────────────

    static class PanelView extends View {
        private final Paint clockPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint datePaint  = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint wxPaint    = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint iconPaint  = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint ringPaint  = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final SimpleDateFormat timeFmt = new SimpleDateFormat("HH:mm", Locale.US);
        private final SimpleDateFormat dateFmt = new SimpleDateFormat("EEE d MMM", Locale.US);
        private final Handler ui = new Handler(Looper.getMainLooper());

        /**
         * The ring, as 12 packed 0xRRGGBB segments — biscuit's LED ring one for
         * one. All zero means idle and nothing is drawn.
         *
         * Holding the whole frame rather than one colour is what makes the
         * Dot's animations survive the port: the thinking spinner is a bright
         * head with a dim trail moving one position per frame, and the speaking
         * meter is the ring's brightness tracking speaker RMS. Both are shape,
         * not hue, and a single colour would erase them.
         */
        /** Outside temperature in Celsius, and which picture to draw.
         *  wxKind null means the controller has not said. */
        private volatile int wxTemp = 0;
        private volatile String wxKind = null;
        private volatile String weather = "";
        private volatile int[] ring = new int[12];
        /** The frame we are fading FROM, and when the current one arrived. */
        private volatile int[] ringPrev = new int[12];
        private volatile long ringAt = 0;
        /** Slightly longer than the daemon's ~90ms frame interval, so one fade
         *  runs into the next and rotation never visibly stops. */
        private static final int CROSSFADE_MS = 110;

        PanelView(android.content.Context c) {
            super(c);
            setBackgroundColor(Color.BLACK);
            clockPaint.setColor(Color.WHITE);
            clockPaint.setTypeface(Typeface.create(Typeface.SANS_SERIF, Typeface.NORMAL));
            clockPaint.setTextAlign(Paint.Align.CENTER);
            datePaint.setColor(0xFF8A8A8A);
            datePaint.setTextAlign(Paint.Align.CENTER);
            wxPaint.setColor(0xFFD0D0D0);
            wxPaint.setTextAlign(Paint.Align.CENTER);
            iconPaint.setStrokeCap(Paint.Cap.ROUND);
            iconPaint.setStrokeJoin(Paint.Join.ROUND);
            ringPaint.setStyle(Paint.Style.STROKE);
            ringPaint.setStrokeCap(Paint.Cap.ROUND);

            // Repaint every second for the clock; the ring repaints on demand.
            ui.post(new Runnable() {
                @Override public void run() {
                    invalidate();
                    ui.postDelayed(this, 1000);
                }
            });
        }

        void setRing(int[] px) {
            // Snapshot where the fade currently stands, so a frame arriving
            // mid-fade starts from what is on screen rather than from the older
            // frame — otherwise fast updates visibly jump backwards.
            long age = System.currentTimeMillis() - ringAt;
            float mix = Math.min(1f, age / (float) CROSSFADE_MS);
            int[] shown = new int[ring.length];
            for (int i = 0; i < ring.length; i++) {
                shown[i] = lerpColor(ringPrev.length == ring.length ? ringPrev[i] : 0, ring[i], mix);
            }
            this.ringPrev = shown;
            this.ringAt = System.currentTimeMillis();
            this.ring = px;
            ui.post(new Runnable() { @Override public void run() { invalidate(); } });
        }

        void setWeather(String line) {
            this.weather = line == null ? "" : line;
            this.wxKind = null;
            ui.post(new Runnable() { @Override public void run() { invalidate(); } });
        }

        void setConditions(int temp, String kind) {
            this.wxTemp = temp;
            this.wxKind = kind == null || kind.length() == 0 ? "cloud" : kind;
            this.weather = "";
            ui.post(new Runnable() { @Override public void run() { invalidate(); } });
        }

        void clearRing() { setRing(new int[12]); }

        void clearFace() {
            clearRing();
            setWeather("");
        }

        /** Icon, then the Celsius number, centred under the date. */
        private void drawConditions(Canvas c, float cx, float cy, float r) {
            float icon = r * 0.16f;
            String label = wxTemp + "\u00b0";
            wxPaint.setTextSize(r * 0.14f);
            float textW = wxPaint.measureText(label);
            float gap = r * 0.035f;
            float left = cx - (icon * 2f + gap + textW) / 2f;
            drawSky(c, left + icon, cy, icon, wxKind);
            Paint.FontMetrics fm = wxPaint.getFontMetrics();
            c.drawText(label, left + icon * 2f + gap + textW / 2f,
                cy - (fm.ascent + fm.descent) / 2f, wxPaint);
        }

        private void drawSky(Canvas c, float x, float y, float s, String kind) {
            if ("sun".equals(kind)) {
                sun(c, x, y, s * 0.42f, s);
            } else if ("fair".equals(kind)) {
                sun(c, x - s * 0.22f, y - s * 0.18f, s * 0.28f, s * 0.72f);
                cloud(c, x + s * 0.08f, y + s * 0.12f, s * 0.78f, 0xFFE4E4E4);
            } else if ("fog".equals(kind)) {
                cloud(c, x, y - s * 0.12f, s * 0.72f, 0xFFB0B0B0);
                bars(c, x, y + s * 0.42f, s * 0.7f);
            } else if ("rain".equals(kind)) {
                cloud(c, x, y - s * 0.16f, s * 0.78f, 0xFFD8D8D8);
                drops(c, x, y + s * 0.28f, s, false);
            } else if ("snow".equals(kind)) {
                cloud(c, x, y - s * 0.16f, s * 0.78f, 0xFFE8E8E8);
                drops(c, x, y + s * 0.28f, s, true);
            } else if ("storm".equals(kind)) {
                cloud(c, x, y - s * 0.18f, s * 0.78f, 0xFFC8C8C8);
                bolt(c, x + s * 0.02f, y + s * 0.18f, s * 0.42f);
            } else {
                cloud(c, x, y, s * 0.9f, 0xFFE0E0E0);
            }
        }

        private void sun(Canvas c, float x, float y, float rad, float ray) {
            iconPaint.setStyle(Paint.Style.FILL);
            iconPaint.setColor(0xFFFFD15C);
            c.drawCircle(x, y, rad, iconPaint);
            iconPaint.setStyle(Paint.Style.STROKE);
            iconPaint.setStrokeWidth(Math.max(2f, rad * 0.22f));
            for (int i = 0; i < 8; i++) {
                double a = i * Math.PI / 4.0;
                float x1 = x + (float) Math.cos(a) * (rad * 1.45f);
                float y1 = y + (float) Math.sin(a) * (rad * 1.45f);
                float x2 = x + (float) Math.cos(a) * ray;
                float y2 = y + (float) Math.sin(a) * ray;
                c.drawLine(x1, y1, x2, y2, iconPaint);
            }
        }

        private void cloud(Canvas c, float x, float y, float w, int color) {
            iconPaint.setStyle(Paint.Style.FILL);
            iconPaint.setColor(color);
            float h = w * 0.62f;
            c.drawCircle(x - w * 0.22f, y, h * 0.42f, iconPaint);
            c.drawCircle(x + w * 0.08f, y - h * 0.16f, h * 0.52f, iconPaint);
            c.drawCircle(x + w * 0.32f, y + h * 0.02f, h * 0.36f, iconPaint);
            c.drawRoundRect(new RectF(x - w * 0.48f, y - h * 0.05f, x + w * 0.5f, y + h * 0.38f),
                h * 0.2f, h * 0.2f, iconPaint);
        }

        private void drops(Canvas c, float x, float y, float s, boolean snow) {
            iconPaint.setStyle(snow ? Paint.Style.FILL : Paint.Style.STROKE);
            iconPaint.setColor(snow ? 0xFFFFFFFF : 0xFF8EC8FF);
            iconPaint.setStrokeWidth(Math.max(2f, s * 0.08f));
            float[] dx = {-0.28f, 0.02f, 0.30f};
            for (int i = 0; i < 3; i++) {
                float px = x + dx[i] * s;
                if (snow) {
                    c.drawCircle(px, y + (i == 1 ? s * 0.08f : 0), s * 0.07f, iconPaint);
                } else {
                    c.drawLine(px, y, px - s * 0.08f, y + s * 0.22f, iconPaint);
                }
            }
        }

        private void bars(Canvas c, float x, float y, float w) {
            iconPaint.setStyle(Paint.Style.STROKE);
            iconPaint.setColor(0xFFB0B0B0);
            iconPaint.setStrokeWidth(Math.max(2f, w * 0.08f));
            c.drawLine(x - w / 2f, y - w * 0.12f, x + w * 0.35f, y - w * 0.12f, iconPaint);
            c.drawLine(x - w * 0.35f, y + w * 0.08f, x + w / 2f, y + w * 0.08f, iconPaint);
        }

        private void bolt(Canvas c, float x, float y, float h) {
            iconPaint.setStyle(Paint.Style.FILL);
            iconPaint.setColor(0xFFFFD15C);
            Path p = new Path();
            p.moveTo(x + h * 0.12f, y);
            p.lineTo(x - h * 0.28f, y + h * 0.55f);
            p.lineTo(x + h * 0.02f, y + h * 0.55f);
            p.lineTo(x - h * 0.16f, y + h);
            p.lineTo(x + h * 0.36f, y + h * 0.38f);
            p.lineTo(x + h * 0.04f, y + h * 0.38f);
            p.close();
            c.drawPath(p, iconPaint);
        }

        @Override protected void onDraw(Canvas c) {
            int w = getWidth(), h = getHeight();
            int cx = w / 2, cy = h / 2;
            float r = Math.min(w, h) / 2f;

            c.drawColor(Color.BLACK);

            clockPaint.setTextSize(r * 0.62f);
            datePaint.setTextSize(r * 0.14f);
            String t = timeFmt.format(new Date());
            // Centre the digits optically rather than on the baseline.
            Paint.FontMetrics fm = clockPaint.getFontMetrics();
            float baseline = cy - (fm.ascent + fm.descent) / 2f;
            c.drawText(t, cx, baseline, clockPaint);
            c.drawText(dateFmt.format(new Date()), cx, baseline + r * 0.26f, datePaint);
            String kind = wxKind;
            if (kind != null) {
                drawConditions(c, cx, baseline + r * 0.46f, r);
            } else {
                String wx = weather;
                if (wx != null && wx.length() > 0) {
                    wxPaint.setTextSize(r * 0.11f);
                    c.drawText(wx, cx, baseline + r * 0.44f, wxPaint);
                }
            }

            int[] px = ring;
            int[] prev = ringPrev;
            boolean any = false;
            for (int v : px) if (v != 0) { any = true; break; }
            for (int v : prev) if (v != 0) { any = true; break; }
            if (!any) return;

            // Cross-fade from the previous frame to the current one. The daemon
            // sends a discrete frame roughly every 90ms (biscuit's animator
            // cadence, built for 12 physical LEDs that can only step); on a
            // display that reads as a stutter, so the position between frames is
            // interpolated in time as well as in space.
            long age = System.currentTimeMillis() - ringAt;
            float mix = Math.min(1f, age / (float) CROSSFADE_MS);

            float stroke = r * 0.075f;
            float inset = stroke / 2f + 3f;
            RectF box = new RectF(inset, inset, w - inset, h - inset);
            ringPaint.setStrokeWidth(stroke);
            ringPaint.setStyle(Paint.Style.STROKE);

            // One short arc per STEP degrees, coloured by sampling the ring at
            // that angle with linear interpolation between neighbouring LEDs.
            // 3 degrees is below what the eye resolves at this radius, so the
            // result reads as a continuous gradient rather than 120 pieces.
            final int STEP = 3;
            for (int a = 0; a < 360; a += STEP) {
                float posF = (a / 360f) * px.length;   // fractional LED index
                int col = lerpColor(sample(prev, posF), sample(px, posF), mix);
                if (col == 0) continue;
                ringPaint.setColor(0xFF000000 | col);
                // Overlap each arc slightly so there is no seam between them.
                c.drawArc(box, -90f + a, STEP + 0.9f, false, ringPaint);
            }

            if (mix < 1f) {
                ui.postDelayed(new Runnable() {
                    @Override public void run() { invalidate(); }
                }, 16);   // ~60fps while a fade is in flight
            }
        }

        /** Colour at a fractional LED position, blended between neighbours. */
        private static int sample(int[] px, float pos) {
            int n = px.length;
            int i0 = ((int) Math.floor(pos) % n + n) % n;
            int i1 = (i0 + 1) % n;
            return lerpColor(px[i0], px[i1], pos - (float) Math.floor(pos));
        }

        private static int lerpColor(int a, int b, float t) {
            int ar = (a >> 16) & 0xFF, ag = (a >> 8) & 0xFF, ab = a & 0xFF;
            int br = (b >> 16) & 0xFF, bg = (b >> 8) & 0xFF, bb = b & 0xFF;
            int r = (int) (ar + (br - ar) * t);
            int g = (int) (ag + (bg - ag) * t);
            int bl = (int) (ab + (bb - ab) * t);
            return (r << 16) | (g << 8) | bl;
        }
    }

    // ── the IPC ──────────────────────────────────────────────────────────────

    /**
     * Accepts one connection at a time from the daemon and reads newline-
     * delimited JSON. Deliberately tolerant: an unparseable line is logged and
     * skipped rather than dropping the connection, because the alternative is a
     * panel that goes blank the first time the two sides disagree about a field.
     */
    static class StateServer implements Runnable {
        private final PanelView view;
        StateServer(PanelView v) { this.view = v; }

        @Override public void run() {
            LocalServerSocket server;
            try {
                server = new LocalServerSocket(SOCKET_NAME);
                Log.i(TAG, "listening on abstract socket " + SOCKET_NAME);
            } catch (IOException e) {
                Log.e(TAG, "cannot bind " + SOCKET_NAME, e);
                return;
            }
            while (true) {
                LocalSocket s = null;
                try {
                    s = server.accept();
                    Log.i(TAG, "daemon connected");
                    BufferedReader in = new BufferedReader(
                        new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
                    String line;
                    while ((line = in.readLine()) != null) handle(line);
                    Log.i(TAG, "daemon disconnected");
                } catch (IOException e) {
                    Log.w(TAG, "socket error: " + e);
                } finally {
                    if (s != null) try { s.close(); } catch (IOException ignored) {}
                    // Daemon gone: clear the ring, or it sticks on whatever the
                    // last state was and lies about the device listening.
                    view.clearFace();
                }
            }
        }

        private void handle(String line) {
            try {
                org.json.JSONObject o = new org.json.JSONObject(line);
                boolean any = false;
                org.json.JSONArray a = o.optJSONArray("px");
                if (a != null) {
                    any = true;
                    int[] px = new int[a.length()];
                    for (int i = 0; i < a.length(); i++) px[i] = a.optInt(i, 0);
                    view.setRing(px);
                }
                if (!any) Log.w(TAG, "no px in: " + line);
            } catch (Exception e) {
                // Tolerant on purpose: a line the two sides disagree about is
                // skipped, never fatal, or the panel goes blank on first drift.
                Log.w(TAG, "bad line: " + line + " (" + e + ")");
            }
        }
    }

    /**
     * Outside conditions, fetched by this app.
     *
     * The panel owns the display and serves itself: the EchoMuse daemon's
     * socket carries ring frames and nothing else, because that socket exists
     * to emulate the Dot's 12-LED ring on a device that has a screen instead.
     * A clock decoration has no business crossing it.
     *
     * Open-Meteo and ipwho.is both need no key and no account. A failed
     * lookup keeps the last reading, so a blip leaves the clock alone rather
     * than blanking it, and the first failure is the only one logged per hour
     * — this runs forever on a device whose kernel log is the only crash
     * channel it has.
     */
    private static final class WeatherPoll implements Runnable {
        private static final long PERIOD_MS = 20 * 60 * 1000L;
        /** Doubling to PERIOD_MS, so a boot race costs seconds not minutes. */
        private static final long FIRST_RETRY_MS = 15 * 1000L;
        private final PanelView view;

        WeatherPoll(PanelView view) { this.view = view; }

        @Override public void run() {
            boolean quiet = false;
            long retry = FIRST_RETRY_MS;
            while (true) {
                long wait;
                try {
                    double[] at = place();
                    if (at != null) current(at[0], at[1]);
                    quiet = false;
                    retry = FIRST_RETRY_MS;
                    wait = PERIOD_MS;
                } catch (Exception e) {
                    // The app starts before wifi associates, so the FIRST
                    // attempt normally fails with UnknownHostException — the
                    // panel was up 22s before the daemon connected on the
                    // bench. Sleeping the full period there leaves the clock
                    // bare for 20 minutes over a race that resolves in
                    // seconds, which is what shipping without this did.
                    if (!quiet) Log.w(TAG, "weather: " + e);
                    quiet = true;
                    wait = retry;
                    retry = Math.min(retry * 2, PERIOD_MS);
                }
                try { Thread.sleep(wait); } catch (InterruptedException e) { return; }
            }
        }

        /** This network's own location, or null. */
        private double[] place() throws Exception {
            org.json.JSONObject o = get("https://ipwho.is/");
            if (!o.optBoolean("success", false)) return null;
            if (!o.has("latitude") || !o.has("longitude")) return null;
            return new double[] { o.getDouble("latitude"), o.getDouble("longitude") };
        }

        private void current(double lat, double lon) throws Exception {
            org.json.JSONObject o = get(
                "https://api.open-meteo.com/v1/forecast?latitude=" + lat
                + "&longitude=" + lon
                + "&current=temperature_2m,weather_code&temperature_unit=celsius");
            org.json.JSONObject cur = o.optJSONObject("current");
            if (cur == null || !cur.has("temperature_2m")) return;
            int temp = (int) Math.round(cur.getDouble("temperature_2m"));
            view.setConditions(temp, kind(cur.optInt("weather_code", 3)));
        }

        private org.json.JSONObject get(String url) throws Exception {
            HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
            c.setRequestProperty("User-Agent", "rook_panel");
            c.setConnectTimeout(8000);
            c.setReadTimeout(8000);
            try {
                if (c.getResponseCode() != 200) throw new IOException("HTTP " + c.getResponseCode());
                BufferedReader r = new BufferedReader(
                    new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8));
                StringBuilder b = new StringBuilder();
                for (String l = r.readLine(); l != null; l = r.readLine()) b.append(l);
                r.close();
                return new org.json.JSONObject(b.toString());
            } finally {
                c.disconnect();
            }
        }

        /**
         * WMO weather code to one of the seven pictures drawn by drawSky.
         * Codes come from Open-Meteo's documented table; anything unlisted
         * falls to cloud, which is the honest answer for "something, not
         * clear".
         */
        private static String kind(int code) {
            switch (code) {
                case 0:              return "sun";
                case 1:              return "fair";
                case 2: case 3:      return "cloud";
                case 45: case 48:    return "fog";
                case 71: case 73: case 75: case 77:
                case 85: case 86:    return "snow";
                case 95: case 96: case 99: return "storm";
                default:
                    if (code >= 51 && code <= 67) return "rain";
                    if (code >= 80 && code <= 82) return "rain";
                    return "cloud";
            }
        }
    }
}
