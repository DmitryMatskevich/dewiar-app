package pro.dewiar.app;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import java.io.File;
import java.io.FileNotFoundException;

/** Отдаёт системе только одно: свой звук напоминаний (files/alarm_custom), чтобы уведомление могло его сыграть —
 *  файл приложения системе напрямую недоступен. Только чтение, только этот файл; остальное — «не найдено». */
public class SoundProvider extends ContentProvider {
    static final String AUTH = "pro.dewiar.app.sound";

    @Override public boolean onCreate() { return true; }

    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"/custom".equals(uri.getPath()) || (mode != null && mode.contains("w"))) throw new FileNotFoundException(uri.toString());
        File f = Sounds.customFile(getContext());
        if (!f.isFile()) throw new FileNotFoundException(uri.toString());
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override public String getType(Uri uri) { return "audio/*"; }
    @Override public Cursor query(Uri u, String[] p, String s, String[] a, String o) { return null; }
    @Override public Uri insert(Uri u, ContentValues v) { return null; }
    @Override public int delete(Uri u, String s, String[] a) { return 0; }
    @Override public int update(Uri u, ContentValues v, String s, String[] a) { return 0; }
}
