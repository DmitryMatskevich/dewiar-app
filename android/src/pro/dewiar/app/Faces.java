package pro.dewiar.app;

import android.app.Notification;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.util.Base64;
import java.io.File;
import java.io.FileOutputStream;

/** Облик персонажа в уведомлениях: от кого напоминание или сообщение — Лира, Эва, Агент — видно по лицу и имени,
 *  в том числе на часах (Wear OS и браслеты показывают крупный значок и заголовок уведомления).
 *  Картинку рисует интерфейс (faceFor в app.js: фото или облик персонажа в круге) и отдаёт через Dewiar.setFace;
 *  здесь она лежит файлом files/faces/<id>.png, имя и цвет — в SharedPreferences "faces" (cur — текущий персонаж).
 *  AlarmReceiver срабатывает и без интерфейса (экран выключен, приложение выгружено) — поэтому файл, а не память. */
final class Faces {
    private static final String P = "faces";
    private Faces() { }

    private static SharedPreferences sp(Context c) { return c.getSharedPreferences(P, Context.MODE_PRIVATE); }

    /** Запомнить облик персонажа pid и сделать его текущим. png — base64 (без «data:…,»). */
    static void save(Context c, String pid, String name, String color, String png) {
        if (pid == null || !pid.matches("[A-Za-z0-9_-]{1,40}")) return;           // имя файла — только из безопасных знаков
        SharedPreferences.Editor e = sp(c).edit().putString("cur", pid).putString("name_" + pid, name == null ? "" : name)
                .putString("color_" + pid, color == null ? "" : color);
        if (png != null && !png.isEmpty()) {
            try {
                byte[] b = Base64.decode(png, Base64.DEFAULT);
                File d = new File(c.getFilesDir(), "faces");
                if (!d.isDirectory() && !d.mkdirs()) throw new Exception("нет папки");
                File tmp = new File(d, pid + ".tmp"), f = new File(d, pid + ".png");
                FileOutputStream o = new FileOutputStream(tmp);
                try { o.write(b); } finally { o.close(); }
                if (!tmp.renameTo(f)) { f.delete(); tmp.renameTo(f); }             // старый файл мешает переименованию
            } catch (Throwable ignored) { }
        }
        e.apply();
    }

    /** Чей облик показать: pid, если он известен, иначе текущий персонаж; "" — облика ещё нет. */
    static String pick(Context c, String pid) {
        SharedPreferences s = sp(c);
        return pid != null && !pid.isEmpty() && s.contains("name_" + pid) ? pid : s.getString("cur", "");
    }

    static String name(Context c, String pid) { return sp(c).getString("name_" + pick(c, pid), ""); }

    /** Картинка облика -> Bitmap или null. */
    static Bitmap bitmap(Context c, String pid) {
        pid = pick(c, pid);
        if (pid.isEmpty()) return null;
        try { return BitmapFactory.decodeFile(new File(new File(c.getFilesDir(), "faces"), pid + ".png").getPath()); }
        catch (Throwable e) { return null; }
    }

    /** Одеть уведомление в облик персонажа pid ("" — текущего): крупный значок, цвет акцента, значок Dewiar в строке
     *  состояния. -> имя персонажа или "" (облика ещё нет — уведомление остаётся обычным). */
    static String dress(Context c, Notification.Builder b, String pid) {
        b.setSmallIcon(R.drawable.ic_stat);
        SharedPreferences s = sp(c);
        pid = pick(c, pid);
        if (pid.isEmpty()) return "";
        Bitmap bm = bitmap(c, pid);
        if (bm != null) b.setLargeIcon(bm);
        // цвет знака — чёрно-белый логотип (Compat.BRAND), а не цвет персонажа: персонажа видно по лицу
        return s.getString("name_" + pid, "");
    }
}
