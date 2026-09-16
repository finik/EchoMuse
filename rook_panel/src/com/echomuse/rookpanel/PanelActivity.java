package com.echomuse.rookpanel;

import android.app.Activity;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.View;
import android.view.WindowManager;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
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
        setContentView(view);
        new Thread(new StateServer(view), "state-server").start();
    }

    // ── the drawing ──────────────────────────────────────────────────────────

    static class PanelView extends View {
        private final Paint clockPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint datePaint  = new Paint(Paint.ANTI_ALIAS_FLAG);
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

        void clearRing() { setRing(new int[12]); }

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
                    view.clearRing();
                }
            }
        }

        private void handle(String line) {
            try {
                org.json.JSONObject o = new org.json.JSONObject(line);
                org.json.JSONArray a = o.optJSONArray("px");
                if (a == null) { Log.w(TAG, "no px in: " + line); return; }
                int[] px = new int[a.length()];
                for (int i = 0; i < a.length(); i++) px[i] = a.optInt(i, 0);
                view.setRing(px);
            } catch (Exception e) {
                // Tolerant on purpose: a line the two sides disagree about is
                // skipped, never fatal, or the panel goes blank on first drift.
                Log.w(TAG, "bad line: " + line + " (" + e + ")");
            }
        }
    }
}
