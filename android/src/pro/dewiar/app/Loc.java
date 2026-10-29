package pro.dewiar.app;

import android.content.Context;
import android.content.pm.PackageManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONObject;

/** Где телефон — для радара «Близкие» → «Рядом». Только по просьбе экрана (вкладка открыта или человек сам показывает
 *  себя близким, пока Dewiar открыт); в фоне за телефоном не следим. Без сервисов Google: LocationManager — сначала
 *  последнее известное место, если оно свежее maxAge; иначе одно новое (сеть, GPS, «fused» — что включено), не дольше
 *  20 с; не успели — последнее известное с его временем. Ответ — событием loc {lat, lon, acc, ts} или {error}. */
final class Loc {
    private Loc() { }

    static boolean allowed(Context c) {
        return c.checkSelfPermission("android.permission.ACCESS_FINE_LOCATION") == PackageManager.PERMISSION_GRANTED
                || c.checkSelfPermission("android.permission.ACCESS_COARSE_LOCATION") == PackageManager.PERMISSION_GRANTED;
    }

    private static JSONObject of(Location l) {
        try {
            return new JSONObject().put("lat", l.getLatitude()).put("lon", l.getLongitude())
                    .put("acc", l.hasAccuracy() ? Math.round(l.getAccuracy()) : 0).put("ts", l.getTime());
        } catch (Exception e) { return new JSONObject(); }
    }

    private static void fail(Net.Sink s, String why) {
        try { s.event("loc", new JSONObject().put("error", why)); } catch (Exception ignored) { }
    }

    static void once(final Context c, final long maxAgeMs, final Net.Sink s) {
        if (!allowed(c)) { fail(s, "нет разрешения на местоположение"); return; }
        final LocationManager lm = (LocationManager) c.getSystemService(Context.LOCATION_SERVICE);
        if (lm == null) { fail(s, "в телефоне нет определения места"); return; }
        final boolean fine = c.checkSelfPermission("android.permission.ACCESS_FINE_LOCATION") == PackageManager.PERMISSION_GRANTED;
        final List<String> on = new ArrayList<>();
        try {
            for (String p : new String[]{"fused", LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER}) {
                if (LocationManager.GPS_PROVIDER.equals(p) && !fine) continue;
                if (lm.getAllProviders().contains(p) && lm.isProviderEnabled(p)) on.add(p);
            }
        } catch (Exception ignored) { }
        Location best = null;
        for (String p : new String[]{"fused", LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER, LocationManager.PASSIVE_PROVIDER}) {
            try {
                Location l = lm.getLastKnownLocation(p);
                if (l != null && (best == null || l.getTime() > best.getTime())) best = l;
            } catch (Exception ignored) { }
        }
        if (on.isEmpty()) {
            if (best != null) { s.event("loc", of(best)); return; }
            fail(s, "местоположение выключено в телефоне");
            return;
        }
        if (best != null && System.currentTimeMillis() - best.getTime() <= maxAgeMs) { s.event("loc", of(best)); return; }
        final Location last = best;
        final Handler h = new Handler(Looper.getMainLooper());
        h.post(new Runnable() {
            @Override public void run() {
                final boolean[] done = {false};
                final List<LocationListener> ls = new ArrayList<>();
                final Runnable stop = new Runnable() {
                    @Override public void run() { for (LocationListener l : ls) { try { lm.removeUpdates(l); } catch (Exception ignored) { } } }
                };
                for (String p : on) {
                    LocationListener l = new LocationListener() {
                        @Override public void onLocationChanged(Location loc) {
                            if (done[0] || loc == null) return;
                            done[0] = true; stop.run(); s.event("loc", of(loc));
                        }
                        @Override public void onStatusChanged(String p2, int st, Bundle b) { }
                        @Override public void onProviderEnabled(String p2) { }
                        @Override public void onProviderDisabled(String p2) { }
                    };
                    try { lm.requestSingleUpdate(p, l, Looper.getMainLooper()); ls.add(l); } catch (Exception ignored) { }
                }
                h.postDelayed(new Runnable() {
                    @Override public void run() {
                        if (done[0]) return;
                        done[0] = true; stop.run();
                        if (last != null) s.event("loc", of(last)); else fail(s, "не удалось определить место — выйдите ближе к окну или включите GPS");
                    }
                }, ls.isEmpty() ? 0 : 20000);
            }
        });
    }
}
