package pro.dewiar.app;

import android.accessibilityservice.AccessibilityService;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.graphics.Rect;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.TextUtils;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

/** «Руки» ассистента в других приложениях — служба специальных возможностей Android. Только через неё одно приложение
 *  видит, что на экране у другого, и нажимает там: прочитать экран (элементы с номерами), нажать элемент или текст,
 *  ввести текст в поле, пролистать, «назад» / «домой» / «недавние» / шторка. Включает человек сам: Настройки Android →
 *  Специальные возможности → Dewiar (Bridge.openSettings("accessibility")). Пароли не читаем и не вводим.
 *  Модель ведёт это инструментами phone_screen / phone_tap / phone_type / phone_scroll / phone_nav (app.js), Bridge
 *  зовёт статические методы ниже из потока JavaBridge: узлы спец. возможностей можно трогать из любого потока. */
public final class Hands extends AccessibilityService {
    private static volatile Hands me;
    private static final List<AccessibilityNodeInfo> items = new ArrayList<>();   // номер элемента в последнем экране → узел
    private static volatile String lastPkg = "";
    private static final int MAX_ITEMS = 140;

    @Override protected void onServiceConnected() { me = this; }

    @Override public boolean onUnbind(Intent intent) { me = null; return super.onUnbind(intent); }

    @Override public void onDestroy() { me = null; super.onDestroy(); }

    @Override public void onAccessibilityEvent(AccessibilityEvent e) {
        if (e != null && e.getEventType() == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED && e.getPackageName() != null)
            lastPkg = e.getPackageName().toString();
    }

    @Override public void onInterrupt() { }

    /** Служба включена и подключена. Включена в настройках, но Android ещё не подключил — тоже false. */
    static boolean on() { return me != null; }

    /** Включена ли служба в настройках Android (подключение может прийти через секунду после включения). */
    static boolean enabledInSettings(Context c) {
        try {
            String s = Settings.Secure.getString(c.getContentResolver(), Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
            return s != null && s.contains(c.getPackageName() + "/");
        } catch (Throwable e) { return false; }
    }

    private static String clip(CharSequence s, int n) {
        if (s == null) return "";
        String t = s.toString().replaceAll("\\s+", " ").trim();
        return t.length() > n ? t.substring(0, n) + "…" : t;
    }

    private static String appLabel(Context c, String pkg) {
        try {
            PackageManager pm = c.getPackageManager();
            ApplicationInfo ai = pm.getApplicationInfo(pkg, 0);
            return String.valueOf(pm.getApplicationLabel(ai));
        } catch (Throwable e) { return pkg; }
    }

    /** Текст для модели: приложение и пронумерованные элементы экрана — что написано и что с ним можно сделать. */
    static synchronized String screen() {
        Hands h = me;
        if (h == null) return "OFF";
        AccessibilityNodeInfo root = h.getRootInActiveWindow();
        items.clear();
        if (root == null) return "Экран сейчас не читается (идёт переход или защищённое окно). Попробуй ещё раз через секунду.";
        String pkg = root.getPackageName() == null ? lastPkg : root.getPackageName().toString();
        StringBuilder sb = new StringBuilder();
        sb.append("Приложение: ").append(appLabel(h, pkg)).append(" (").append(pkg).append(")\n");
        walk(root, sb, 0, false);
        if (items.size() >= MAX_ITEMS) sb.append("… (на экране ещё элементы — пролистай или уточни)\n");
        if (items.isEmpty()) sb.append("(на экране нет элементов с текстом)\n");
        return sb.toString();
    }

    /** underClick — элемент внутри нажимаемого родителя (строка чата, карточка): признак идёт вниз при обходе, чтобы
     *  не подниматься по родителям для каждого текста (на слабом устройстве — тысячи обращений к системе). */
    private static void walk(AccessibilityNodeInfo n, StringBuilder sb, int depth, boolean underClick) {
        if (n == null || items.size() >= MAX_ITEMS || depth > 40) return;
        if (Build.VERSION.SDK_INT >= 16 && !n.isVisibleToUser()) return;
        CharSequence text = n.getText(), desc = n.getContentDescription();
        String hint = "";
        if (Build.VERSION.SDK_INT >= 26) {
            try { Object o = AccessibilityNodeInfo.class.getMethod("getHintText").invoke(n); hint = clip((CharSequence) o, 60); } catch (Throwable ignored) { }
        }
        boolean click = n.isClickable() || n.isLongClickable(), edit = n.isEditable(), scroll = n.isScrollable(),
                check = n.isCheckable();
        String t = clip(!TextUtils.isEmpty(text) ? text : desc, 90);
        if (n.isPassword()) t = "(пароль)";
        if (!t.isEmpty() || edit || scroll || (click && n.getChildCount() == 0) || check) {
            items.add(AccessibilityNodeInfo.obtain(n));
            String cls = n.getClassName() == null ? "" : n.getClassName().toString();
            String kind = edit ? "поле ввода" : cls.endsWith("Button") || cls.endsWith("ImageButton") ? "кнопка"
                    : check ? (cls.contains("Switch") ? "переключатель" : "флажок") : scroll ? "список" : click ? "нажимается" : "текст";
            sb.append('[').append(items.size()).append("] ").append(kind);
            if (!t.isEmpty()) sb.append(" «").append(t).append('»');
            if (edit && TextUtils.isEmpty(text) && !hint.isEmpty()) sb.append(" (подсказка: «").append(hint).append("»)");
            if (check) sb.append(n.isChecked() ? " — включено" : " — выключено");
            if (!n.isEnabled()) sb.append(" — недоступно");
            if (!t.isEmpty() && !click && !edit && !scroll && underClick) sb.append(" — нажимается");
            sb.append('\n');
        }
        for (int i = 0; i < n.getChildCount(); i++) {
            AccessibilityNodeInfo c = n.getChild(i);
            walk(c, sb, depth + 1, underClick || n.isClickable());
        }
    }

    private static AccessibilityNodeInfo clickableAncestor(AccessibilityNodeInfo n) {
        AccessibilityNodeInfo p = n;
        for (int i = 0; p != null && i < 8; i++) {
            if (p.isClickable()) return p;
            p = p.getParent();
        }
        return null;
    }

    private static AccessibilityNodeInfo item(int id) {
        synchronized (Hands.class) {
            if (id < 1 || id > items.size()) return null;
            AccessibilityNodeInfo n = items.get(id - 1);
            try { n.refresh(); } catch (Throwable ignored) { }
            return n;
        }
    }

    /** Нажать элемент номер id (или ближайший нажимаемый родитель; нет его — касание в центр элемента). */
    static String tap(int id, boolean longPress) {
        Hands h = me;
        if (h == null) return "OFF";
        AccessibilityNodeInfo n = item(id);
        if (n == null) return "Ошибка: нет элемента [" + id + "] — сначала прочитай экран (phone_screen).";
        return press(h, n, longPress);
    }

    private static String press(Hands h, AccessibilityNodeInfo n, boolean longPress) {
        String what = clip(!TextUtils.isEmpty(n.getText()) ? n.getText() : n.getContentDescription(), 60);
        AccessibilityNodeInfo c = clickableAncestor(n);
        int act = longPress ? AccessibilityNodeInfo.ACTION_LONG_CLICK : AccessibilityNodeInfo.ACTION_CLICK;
        if (c != null && c.performAction(act)) return "Нажато" + (what.isEmpty() ? "." : ": «" + what + "».");
        Rect r = new Rect();
        n.getBoundsInScreen(r);
        if (!r.isEmpty() && gesture(h, r.centerX(), r.centerY(), longPress ? 800 : 60))
            return "Коснулся" + (what.isEmpty() ? "." : ": «" + what + "».");
        return "Ошибка: элемент не нажимается.";
    }

    /** Нажать элемент по тексту на экране (первое видимое совпадение). */
    static String tapText(String text) {
        Hands h = me;
        if (h == null) return "OFF";
        AccessibilityNodeInfo root = h.getRootInActiveWindow();
        if (root == null) return "Ошибка: экран сейчас не читается.";
        List<AccessibilityNodeInfo> found = root.findAccessibilityNodeInfosByText(text);
        for (AccessibilityNodeInfo n : found) {
            if (Build.VERSION.SDK_INT >= 16 && !n.isVisibleToUser()) continue;
            return press(h, n, false);
        }
        return "Ошибка: на экране нет «" + text + "». Прочитай экран (phone_screen) — может, надпись другая.";
    }

    /** Ввести текст в поле id (0 — в поле, где сейчас курсор, или в первое поле на экране). submit — «Ввод» на клавиатуре. */
    static String type(int id, String text, boolean submit) {
        Hands h = me;
        if (h == null) return "OFF";
        AccessibilityNodeInfo n = id > 0 ? item(id) : null;
        if (n == null && id <= 0) {
            AccessibilityNodeInfo root = h.getRootInActiveWindow();
            if (root != null) n = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
            if (n == null && root != null) n = firstEditable(root, 0);
        }
        if (n == null) return "Ошибка: поле ввода не найдено — прочитай экран и укажи номер поля.";
        if (n.isPassword()) return "Пароли я не ввожу — пусть пользователь введёт сам.";
        if (!n.isEditable()) return "Ошибка: [" + id + "] — не поле ввода.";
        n.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
        n.performAction(AccessibilityNodeInfo.ACTION_CLICK);
        Bundle b = new Bundle();
        b.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        if (!n.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, b)) return "Ошибка: поле не принимает текст.";
        if (submit) {
            // ACTION_IME_ENTER — Android 11+: «Ввод» на клавиатуре (поиск, отправка в некоторых чатах); номер — из системы
            boolean sent = false;
            if (Build.VERSION.SDK_INT >= 30) {
                try { sent = n.performAction(android.R.id.class.getField("accessibilityActionImeEnter").getInt(null)); } catch (Throwable ignored) { }
            }
            return "Введено" + (sent ? " и нажат «Ввод»." : ". «Ввод» не нажался — нажми кнопку отправки или поиска на экране.");
        }
        return "Введено.";
    }

    private static AccessibilityNodeInfo firstEditable(AccessibilityNodeInfo n, int depth) {
        if (n == null || depth > 40) return null;
        if (n.isEditable() && (Build.VERSION.SDK_INT < 16 || n.isVisibleToUser())) return n;
        for (int i = 0; i < n.getChildCount(); i++) {
            AccessibilityNodeInfo r = firstEditable(n.getChild(i), depth + 1);
            if (r != null) return r;
        }
        return null;
    }

    /** Пролистать: dir down|up (вперёд/назад), id — список (0 — первый прокручиваемый на экране). */
    static String scroll(int id, String dir) {
        Hands h = me;
        if (h == null) return "OFF";
        AccessibilityNodeInfo n = id > 0 ? item(id) : null;
        if (n == null) {
            AccessibilityNodeInfo root = h.getRootInActiveWindow();
            n = firstScrollable(root, 0);
        }
        boolean back = "up".equals(dir) || "left".equals(dir) || "back".equals(dir);
        if (n != null && n.performAction(back ? AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD : AccessibilityNodeInfo.ACTION_SCROLL_FORWARD))
            return "Пролистано.";
        // нет прокручиваемого узла (игры, карты, веб-страницы) — жест пальцем по экрану
        android.util.DisplayMetrics dm = h.getResources().getDisplayMetrics();
        int x = dm.widthPixels / 2, y1 = dm.heightPixels * 3 / 4, y2 = dm.heightPixels / 4;
        if (swipe(h, x, back ? y2 : y1, x, back ? y1 : y2)) return "Пролистано жестом.";
        return "Дальше не листается.";
    }

    private static AccessibilityNodeInfo firstScrollable(AccessibilityNodeInfo n, int depth) {
        if (n == null || depth > 40) return null;
        if (n.isScrollable() && (Build.VERSION.SDK_INT < 16 || n.isVisibleToUser())) return n;
        for (int i = 0; i < n.getChildCount(); i++) {
            AccessibilityNodeInfo r = firstScrollable(n.getChild(i), depth + 1);
            if (r != null) return r;
        }
        return null;
    }

    /** back, home, recents, notifications, quick_settings — как кнопки системы. */
    static String nav(String what) {
        Hands h = me;
        if (h == null) return "OFF";
        int a;
        switch (what) {
            case "back": a = GLOBAL_ACTION_BACK; break;
            case "home": a = GLOBAL_ACTION_HOME; break;
            case "recents": a = GLOBAL_ACTION_RECENTS; break;
            case "notifications": a = GLOBAL_ACTION_NOTIFICATIONS; break;
            case "quick_settings": a = GLOBAL_ACTION_QUICK_SETTINGS; break;
            default: return "Ошибка: неизвестное действие " + what;
        }
        return h.performGlobalAction(a) ? "Готово." : "Ошибка: система не выполнила «" + what + "».";
    }

    // ---------- жесты (Android 7+): API 24 — отражением, сборка идёт против android.jar API 23 ----------

    private static boolean gesture(Hands h, int x, int y, long ms) { return stroke(h, x, y, x, y, ms); }

    private static boolean swipe(Hands h, int x1, int y1, int x2, int y2) { return stroke(h, x1, y1, x2, y2, 350); }

    private static boolean stroke(Hands h, int x1, int y1, int x2, int y2, long ms) {
        if (Build.VERSION.SDK_INT < 24) return false;
        try {
            android.graphics.Path p = new android.graphics.Path();
            p.moveTo(x1, y1);
            if (x1 != x2 || y1 != y2) p.lineTo(x2, y2);
            Class<?> sd = Class.forName("android.accessibilityservice.GestureDescription$StrokeDescription");
            Constructor<?> k = sd.getConstructor(android.graphics.Path.class, long.class, long.class);
            Object stroke = k.newInstance(p, 0L, ms);
            Class<?> bc = Class.forName("android.accessibilityservice.GestureDescription$Builder");
            Object b = bc.getConstructor().newInstance();
            bc.getMethod("addStroke", sd).invoke(b, stroke);
            Object gd = bc.getMethod("build").invoke(b);
            Class<?> gdc = Class.forName("android.accessibilityservice.GestureDescription");
            Class<?> cb = Class.forName("android.accessibilityservice.AccessibilityService$GestureResultCallback");
            Method d = AccessibilityService.class.getMethod("dispatchGesture", gdc, cb, android.os.Handler.class);
            return (Boolean) d.invoke(h, gd, null, null);
        } catch (Throwable e) { return false; }
    }
}
