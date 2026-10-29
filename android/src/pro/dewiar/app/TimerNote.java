package pro.dewiar.app;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import org.json.JSONObject;

/** Идущий таймер — в шторке с тикающим обратным отсчётом (как у часов): виден на заблокированном экране и на часах,
 *  тихий (свой канал без звука). Ставит Alarms.put для kind=timer, снимает Alarms.remove (отменили, сработал —
 *  дальше звонит AlarmReceiver). Отсчёт рисует сама система: setUsesChronometer + setChronometerCountDown (API 24;
 *  собираем против 23 — отражением). Нажатие — открыть приложение. */
public class TimerNote {
    static final String CH = "timer_run";

    static int nid(String id) { return ("t:" + id).hashCode(); }

    static void show(Context c, JSONObject x) {
        try {
            String id = x.getString("id");
            long at = x.getLong("at");
            if (at <= System.currentTimeMillis()) return;
            Notification.Builder b = Compat.builder(c, channel(c));
            String title = x.optString("title", "Таймер");
            Intent open = new Intent(c, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            b.setContentTitle("⏱ " + (title.isEmpty() ? "Таймер" : title))
                    .setContentText("сработает в " + new java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault()).format(new java.util.Date(at)))
                    .setWhen(at).setShowWhen(true).setUsesChronometer(true).setOngoing(true).setOnlyAlertOnce(true)
                    .setCategory(Notification.CATEGORY_ALARM)
                    .setContentIntent(PendingIntent.getActivity(c, nid(id), open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
            if (Build.VERSION.SDK_INT >= 24) {
                try { Notification.Builder.class.getMethod("setChronometerCountDown", boolean.class).invoke(b, true); } catch (Throwable ignored) { }
            }
            Intent cancel = new Intent(c, AlarmReceiver.class).setAction(AlarmReceiver.DONE).putExtra("id", id).putExtra("cancel", true);
            b.addAction(new Notification.Action.Builder(R.drawable.ic_stat, "Отменить",
                    PendingIntent.getBroadcast(c, nid(id) + 1, cancel, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE)).build());
            ((NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE)).notify(nid(id), b.build());
        } catch (Throwable ignored) { }
    }

    static void hide(Context c, String id) {
        try { ((NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE)).cancel(nid(id)); } catch (Throwable ignored) { }
    }

    static String channel(Context c) {
        if (Build.VERSION.SDK_INT < 26) return CH;
        try {
            NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
            if (NotificationManager.class.getMethod("getNotificationChannel", String.class).invoke(nm, CH) == null) {
                Class<?> chc = Class.forName("android.app.NotificationChannel");
                Object ch = chc.getConstructor(String.class, CharSequence.class, int.class).newInstance(CH, "Идущий таймер", 2 /* IMPORTANCE_LOW */);
                chc.getMethod("setSound", android.net.Uri.class, android.media.AudioAttributes.class).invoke(ch, null, null);
                chc.getMethod("enableVibration", boolean.class).invoke(ch, false);
                chc.getMethod("setShowBadge", boolean.class).invoke(ch, false);
                NotificationManager.class.getMethod("createNotificationChannel", chc).invoke(nm, ch);
            }
        } catch (Throwable ignored) { }
        return CH;
    }
}
