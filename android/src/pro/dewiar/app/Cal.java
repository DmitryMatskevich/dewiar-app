package pro.dewiar.app;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.ContentValues;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.CalendarContract;
import java.util.TimeZone;
import org.json.JSONArray;
import org.json.JSONObject;

/** Календарь телефона (CalendarContract): в нём Google-календарь и календари других аккаунтов человека — без OAuth и
 *  паролей. Читать — встречи за период (повторяющиеся тоже, через Instances); записать — новую встречу в основной
 *  календарь, с напоминанием за 15 минут. Чужие встречи не правим и не удаляем. Разрешения READ_CALENDAR /
 *  WRITE_CALENDAR проверяет и спрашивает Bridge; подтверждение «записать?» — на стороне app.js. */
public final class Cal {
    private Cal() { }

    /** Встречи с from по to (мс) из видимых календарей, по времени начала; не больше 200. */
    static JSONArray events(ContentResolver cr, long from, long to) throws Exception {
        Uri.Builder b = CalendarContract.Instances.CONTENT_URI.buildUpon();
        ContentUris.appendId(b, from);
        ContentUris.appendId(b, to);
        String[] proj = {CalendarContract.Instances.EVENT_ID, CalendarContract.Instances.TITLE, CalendarContract.Instances.BEGIN,
                CalendarContract.Instances.END, CalendarContract.Instances.ALL_DAY, CalendarContract.Instances.EVENT_LOCATION,
                CalendarContract.Instances.CALENDAR_DISPLAY_NAME};
        JSONArray out = new JSONArray();
        Cursor c = cr.query(b.build(), proj, CalendarContract.Instances.VISIBLE + "=1", null, CalendarContract.Instances.BEGIN + " ASC");
        if (c == null) return out;
        try {
            while (c.moveToNext() && out.length() < 200) {
                boolean allDay = c.getInt(4) == 1;
                long begin = c.getLong(2), end = c.getLong(3);
                if (allDay) {                         // весь день хранится в UTC-полночи — переводим в местную полночь
                    begin = utcDayToLocal(begin);
                    end = utcDayToLocal(end);
                }
                out.put(new JSONObject().put("id", c.getLong(0)).put("title", str(c.getString(1))).put("begin", begin).put("end", end)
                        .put("all_day", allDay).put("location", str(c.getString(5))).put("calendar", str(c.getString(6))));
            }
        } finally {
            c.close();
        }
        return out;
    }

    private static String str(String s) { return s == null ? "" : s; }

    private static long utcDayToLocal(long t) {
        return t - TimeZone.getDefault().getOffset(t);
    }

    private static long localDayToUtc(long t) {
        return t + TimeZone.getDefault().getOffset(t);
    }

    /** Календарь для записи: видимый, где можно писать; основной аккаунта → Google → свой (владелец) → любой. -1 — нет. */
    static long[] pick(ContentResolver cr, StringBuilder name) {
        String[] proj = {CalendarContract.Calendars._ID, CalendarContract.Calendars.CALENDAR_DISPLAY_NAME, CalendarContract.Calendars.ACCOUNT_TYPE,
                CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL, CalendarContract.Calendars.ACCOUNT_NAME, CalendarContract.Calendars.OWNER_ACCOUNT, "isPrimary"};
        Cursor c;
        try {
            c = cr.query(CalendarContract.Calendars.CONTENT_URI, proj, CalendarContract.Calendars.VISIBLE + "=1 AND "
                    + CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL + ">=" + CalendarContract.Calendars.CAL_ACCESS_CONTRIBUTOR, null, null);
        } catch (Throwable e) {                    // на старых системах нет столбца isPrimary — без него
            String[] p2 = new String[proj.length - 1];
            System.arraycopy(proj, 0, p2, 0, p2.length);
            c = cr.query(CalendarContract.Calendars.CONTENT_URI, p2, CalendarContract.Calendars.VISIBLE + "=1 AND "
                    + CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL + ">=" + CalendarContract.Calendars.CAL_ACCESS_CONTRIBUTOR, null, null);
        }
        long best = -1;
        int bestScore = -1;
        String bestName = "";
        if (c != null) {
            try {
                while (c.moveToNext()) {
                    int s = 0;
                    if (c.getColumnCount() > 6 && c.getInt(6) == 1) s += 8;
                    if ("com.google".equals(c.getString(2))) s += 4;
                    if (c.getInt(3) >= CalendarContract.Calendars.CAL_ACCESS_OWNER) s += 2;
                    String acc = str(c.getString(4));
                    if (!acc.isEmpty() && acc.equals(c.getString(5))) s += 1;
                    if (s > bestScore) { bestScore = s; best = c.getLong(0); bestName = str(c.getString(1)); }
                }
            } finally {
                c.close();
            }
        }
        name.append(bestName.isEmpty() ? "Календарь" : bestName);
        return new long[]{best};
    }

    /** Новая встреча: {title, begin, end (мс, местное время), all_day, location, note} → {id, calendar} или {error}. */
    static JSONObject add(ContentResolver cr, JSONObject e) throws Exception {
        StringBuilder cname = new StringBuilder();
        long cal = pick(cr, cname)[0];
        if (cal < 0) return new JSONObject().put("error", "no_calendar");
        boolean allDay = e.optBoolean("all_day");
        long begin = e.getLong("begin"), end = e.optLong("end", begin + 3600000L);
        ContentValues v = new ContentValues();
        v.put(CalendarContract.Events.CALENDAR_ID, cal);
        v.put(CalendarContract.Events.TITLE, e.optString("title", "Встреча"));
        if (!e.optString("location").isEmpty()) v.put(CalendarContract.Events.EVENT_LOCATION, e.optString("location"));
        if (!e.optString("note").isEmpty()) v.put(CalendarContract.Events.DESCRIPTION, e.optString("note"));
        if (allDay) {                                  // весь день — по правилам календаря: полночь UTC и часовой пояс UTC
            v.put(CalendarContract.Events.ALL_DAY, 1);
            v.put(CalendarContract.Events.DTSTART, localDayToUtc(begin));
            v.put(CalendarContract.Events.DTEND, localDayToUtc(Math.max(end, begin + 86400000L)));
            v.put(CalendarContract.Events.EVENT_TIMEZONE, "UTC");
        } else {
            v.put(CalendarContract.Events.DTSTART, begin);
            v.put(CalendarContract.Events.DTEND, Math.max(end, begin + 60000L));
            v.put(CalendarContract.Events.EVENT_TIMEZONE, TimeZone.getDefault().getID());
        }
        v.put(CalendarContract.Events.HAS_ALARM, allDay ? 0 : 1);
        Uri u = cr.insert(CalendarContract.Events.CONTENT_URI, v);
        if (u == null) return new JSONObject().put("error", "календарь не принял встречу");
        long id = ContentUris.parseId(u);
        if (!allDay) {
            try {
                ContentValues r = new ContentValues();
                r.put(CalendarContract.Reminders.EVENT_ID, id);
                r.put(CalendarContract.Reminders.MINUTES, 15);
                r.put(CalendarContract.Reminders.METHOD, CalendarContract.Reminders.METHOD_ALERT);
                cr.insert(CalendarContract.Reminders.CONTENT_URI, r);
            } catch (Throwable ignored) { }            // без напоминания встреча всё равно записана
        }
        return new JSONObject().put("id", id).put("calendar", cname.toString());
    }

    /** Окно новой встречи в приложении «Календарь» с заполненными полями — когда записать самим нельзя (нет доступа
     *  или нет календаря для записи): человек сохраняет кнопкой там. */
    static boolean openInsert(Activity a, JSONObject e) {
        try {
            Intent i = new Intent(Intent.ACTION_INSERT).setData(CalendarContract.Events.CONTENT_URI)
                    .putExtra(CalendarContract.Events.TITLE, e.optString("title"))
                    .putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, e.optLong("begin"))
                    .putExtra(CalendarContract.EXTRA_EVENT_END_TIME, e.optLong("end", e.optLong("begin") + 3600000L))
                    .putExtra(CalendarContract.EXTRA_EVENT_ALL_DAY, e.optBoolean("all_day"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            if (!e.optString("location").isEmpty()) i.putExtra(CalendarContract.Events.EVENT_LOCATION, e.optString("location"));
            a.startActivity(i);
            return true;
        } catch (ActivityNotFoundException ex) {
            return false;
        } catch (Throwable ex) {
            return false;
        }
    }
}
