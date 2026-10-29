package pro.dewiar.app;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import java.io.File;
import java.io.FileNotFoundException;

/** Отдаёт установщику Android скачанное обновление (Update.java) — и только его: cache/update/Dewiar.apk, только
 *  чтение. Наружу не открыт: доступ получает лишь тот, кому выдали разрешение на этот адрес (FLAG_GRANT_READ_URI_PERMISSION). */
public class UpdateProvider extends ContentProvider {
    @Override public boolean onCreate() { return true; }

    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"/Dewiar.apk".equals(uri.getPath()) || (mode != null && mode.contains("w"))) throw new FileNotFoundException(uri.toString());
        File f = Update.apk(getContext());
        if (!f.isFile()) throw new FileNotFoundException(uri.toString());
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override public String getType(Uri uri) { return "application/vnd.android.package-archive"; }
    @Override public Cursor query(Uri u, String[] p, String s, String[] a, String o) { return null; }
    @Override public Uri insert(Uri u, ContentValues v) { return null; }
    @Override public int delete(Uri u, String s, String[] a) { return 0; }
    @Override public int update(Uri u, ContentValues v, String s, String[] a) { return 0; }
}
