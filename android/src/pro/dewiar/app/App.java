package pro.dewiar.app;

import android.app.Application;
import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.io.StringWriter;

/** Приложение: ловим падения и создаём каналы уведомлений (Compat.channels) до любого экрана.
 *  Текст падения с версией и моделью телефона пишется в files/crash.txt; Bridge.info() отдаёт его интерфейсу, app.js
 *  показывает в настройках («Отправить» / «Очистить» → Dewiar.clearCrash) и, если включено, сам шлёт отчёт.
 *  После записи падение передаётся прежнему обработчику — система закрывает приложение как обычно. */
public class App extends Application {
    @Override public void onCreate() {
        super.onCreate();
        final Thread.UncaughtExceptionHandler prev = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler(new Thread.UncaughtExceptionHandler() {
            @Override public void uncaughtException(Thread t, Throwable e) {
                try {
                    StringWriter sw = new StringWriter();
                    e.printStackTrace(new PrintWriter(sw));
                    FileWriter w = new FileWriter(new File(getFilesDir(), "crash.txt"));
                    w.write("Dewiar " + BuildInfo.VERSION + " · Android " + android.os.Build.VERSION.RELEASE + " · "
                            + android.os.Build.MANUFACTURER + " " + android.os.Build.MODEL + "\n" + t.getName() + "\n" + sw);
                    w.close();
                } catch (Throwable ignored) { }
                if (prev != null) prev.uncaughtException(t, e);
            }
        });
        Compat.channels(this);
    }
}
