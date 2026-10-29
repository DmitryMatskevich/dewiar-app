package pro.dewiar.app;

import android.app.Notification;
import android.app.NotificationManager;
import android.content.Context;
import android.media.AudioAttributes;
import android.net.Uri;
import android.os.Build;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;

/** Новые API Android (26+, 31+) — через отражение: tools/build_apk.sh собирает против android.jar API 23, а работать
 *  надо на любых. Здесь каналы уведомлений, Notification.Builder с каналом и проверка права на точные будильники.
 *  Ошибка отражения не роняет приложение — откатываемся к старому поведению. */
public final class Compat {
    // каналы: будильники и напоминания — alarm_<звук> (важный, звук — выбранный, см. Sounds; прежний общий «alarms»
    // удаляется), info — сообщения ассистента (обычный). Звук и важность канала Android запоминает при создании:
    // поменять их можно только новым id
    public static final String CH_ALARM = "alarms", CH_INFO = "info";
    private Compat() { }

    public static void channels(Context c) {
        if (Build.VERSION.SDK_INT < 26) return;
        try {
            NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
            Class<?> chc = Class.forName("android.app.NotificationChannel");
            Constructor<?> k = chc.getConstructor(String.class, CharSequence.class, int.class);
            Method create = NotificationManager.class.getMethod("createNotificationChannel", chc);
            Sounds.channel(c);                              // будильники — канал под выбранный звук (Sounds)
            Object b = k.newInstance(CH_INFO, "Сообщения ассистента", 3 /* IMPORTANCE_DEFAULT */);
            create.invoke(nm, b);
        } catch (Throwable ignored) { }
    }

    @SuppressWarnings("deprecation")
    public static Notification.Builder builder(Context c, String channel) {
        Notification.Builder b = null;
        if (Build.VERSION.SDK_INT >= 26) {
            try {
                b = (Notification.Builder) Notification.Builder.class.getConstructor(Context.class, String.class).newInstance(c, channel);
            } catch (Throwable ignored) { }
        }
        if (b == null) b = new Notification.Builder(c);
        // знак Dewiar в уведомлениях — чёрно-белый, как логотип (без цвета система красила его в синий акцент темы)
        b.setColor(BRAND);
        return b;
    }

    /** Цвет знака в уведомлениях: почти чёрный (белый знак на нём — как оригинальный логотип). */
    static final int BRAND = 0xFF111111;

    /** Можно ли ставить точные будильники (Android 12+ спрашивает разрешение; USE_EXACT_ALARM из манифеста даёт его
     *  с Android 13 сразу). Не удалось спросить — считаем, что можно. */
    public static boolean canExact(android.app.AlarmManager am) {
        if (Build.VERSION.SDK_INT < 31) return true;
        try { return (Boolean) android.app.AlarmManager.class.getMethod("canScheduleExactAlarms").invoke(am); }
        catch (Throwable e) { return true; }
    }
}
