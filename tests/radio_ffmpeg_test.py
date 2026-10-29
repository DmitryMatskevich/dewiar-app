"""Радио, когда системный ffmpeg сломан (например, после сбоя обновления падает SIGSEGV на каждой станции:
«ffmpeg -11», все станции молчат). Dewiar сам проверяет ffmpeg и берёт встроенный (imageio-ffmpeg); упавший на деле
ffmpeg помечается и больше не используется; если молчат все станции — проверка говорит, где поломка."""
import http.server, os, shutil, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
sys.path.insert(0, str(ROOT / "core"))
try:
    import imageio_ffmpeg
    bundled = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # noqa: BLE001
    bundled = None
if os.name == "nt" or not bundled:
    print("нет встроенного ffmpeg (или Windows) — пропускаю"); print("ALL OK"); sys.exit(0)

tmp = Path(tempfile.mkdtemp())
fake = tmp / "bin"; fake.mkdir()
(fake / "ffmpeg").write_text("#!/bin/sh\nkill -SEGV $$\n"); (fake / "ffmpeg").chmod(0o755)
www = tmp / "www"; www.mkdir()
subprocess.run([bundled, "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=30", "-ac", "1", "-b:a", "64k",
                str(www / "jazz.mp3")], check=True)
s = socket.socket(); s.bind(("127.0.0.1", 0)); WP = s.getsockname()[1]; s.close()
class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", WP), lambda *a, **k: Quiet(*a, directory=str(www), **k))

threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["PATH"] = f"{fake}{os.pathsep}{os.environ['PATH']}"
os.environ["NO_PROXY"] = "127.0.0.1"

import eyes  # noqa: E402
from audio_io import Player  # noqa: E402
from radio import Radio, diagnose  # noqa: E402

ok(shutil.which("ffmpeg") == str(fake / "ffmpeg"), "в системе «сломанный» ffmpeg (падает сразу)")
ok(not eyes.ffmpeg_works(str(fake / "ffmpeg")) and eyes.ffmpeg_works(bundled), "проверка ffmpeg отличает сломанный от рабочего")
ok(eyes.ffmpeg_path() == bundled, "Dewiar сам взял встроенный ffmpeg")

# ffmpeg упал на деле → помечаем и дальше берём другой
eyes._FF.clear()
eyes._FF["path"] = str(fake / "ffmpeg")                         # как будто проверка прошла, а он упал при игре
r = Radio(lambda: Player(device="", null=True), eyes.ffmpeg_path)
url = f"http://127.0.0.1:{WP}/jazz.mp3"
r.play({"name": "Test", "url": url, "tags": ""})
time.sleep(2.5)
ok(not r.playing and "-11" in (r.failed or "") and str(fake / "ffmpeg") in eyes._FF.get("bad", set()),
   f"упавший ffmpeg замечен: {r.failed}")
r.play({"name": "Test", "url": url, "tags": ""})
for _ in range(40):
    time.sleep(0.25)
    if r.got > 44100 * 2:
        break
ok(r.playing and r.got > 44100 * 2, f"следующая станция играет встроенным ffmpeg ({r.got // 1024} КБ звука)")
r.stop()
where, fix = diagnose(str(fake / "ffmpeg"), url)
ok("не запускается ffmpeg" in where and "-11" in where and "reinstall ffmpeg" in fix, f"проверка «молчат все станции»: {where} → {fix}")
where, fix = diagnose(bundled, url)
ok("не выводится звук" in where, f"ffmpeg и сеть в порядке → дело в выводе звука: {where}")
where, fix = diagnose(bundled, f"http://127.0.0.1:{WP}/none.mp3")
ok("404" in where, f"станция ответила ошибкой: {where}")
srv.shutdown()
