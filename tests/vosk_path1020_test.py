"""Отчёты №120–125 (19.10, Windows 10, имя пользователя кириллицей): «голосовой ассистент, но почему то глухой», «по пробелу
тоже не слышит». Kaldi (модель имён Vosk) в Windows открывает файлы «узкими» путями: C:\\Users\\Виталий\\… для него не
существует — «Failed to create a model»; исключение убивало поток слуха без следа в журнале: «Готовлю слух» навсегда, не
работала даже кнопка. Теперь:
— kaldi_path: путь не ASCII — короткое имя 8.3 или копия модели в ASCII-папку (один раз, с отметкой .complete);
— модель не открылась — распаковать заново один раз, не вышло — слух без модели имён (кнопка, удержание работают);
— распаковка — во временную папку и переименованием: обрыв посреди не оставляет «папку без половины файлов».
Не в Windows путь «как в Windows» включает DEWIAR_KALDI_NT=1, ASCII-папку задаёт DEWIAR_KALDI_BASE."""
import json, os, shutil, socket, subprocess, sys, tempfile, time, zipfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


tmp = Path(tempfile.mkdtemp())
os.environ["DEWIAR_KALDI_NT"] = "1"
os.environ["DEWIAR_KALDI_BASE"] = str(tmp / "ascii")
import listen as L  # noqa: E402

# ── kaldi_path ──
src = tmp / "Пользователи" / "Виталий" / "models" / "m1"
(src / "am").mkdir(parents=True); (src / "am/final.mdl").write_bytes(b"x" * 1000); (src / "conf").mkdir(); (src / "graph").mkdir()
p1 = L.kaldi_path(src)
ok(str(p1).isascii() and (p1 / "am/final.mdl").read_bytes() == b"x" * 1000 and (p1 / ".complete").exists(), f"путь кириллицей — копия в ASCII-папку: {p1}")
t1 = (p1 / ".complete").stat().st_mtime_ns
time.sleep(0.05)
ok(L.kaldi_path(src) == p1 and (p1 / ".complete").stat().st_mtime_ns == t1, "второй раз — без копирования")
asc = tmp / "plain" / "m2"; asc.mkdir(parents=True)
ok(L.kaldi_path(asc) == asc, "латинский путь — как есть")
os.environ["DEWIAR_KALDI_NT"] = "0"
ok(L.kaldi_path(src) == src, "не Windows — как есть")
os.environ["DEWIAR_KALDI_NT"] = "1"

# ── распаковка: недоделанная папка — заново, отметка .complete ──
zp = tmp / "z" / "m3.zip"; zp.parent.mkdir()
with zipfile.ZipFile(zp, "w") as z:
    for f in ("m3/am/final.mdl", "m3/conf/model.conf", "m3/graph/Gr.fst"):
        z.writestr(f, "ok")
dst = tmp / "z" / "out"
(dst / "m3" / "am").mkdir(parents=True)                       # обрыв посреди распаковки: папка есть, файлов нет
ok(not L.vosk_complete(dst / "m3"), "папка без главных файлов — не «готово»")
md = L.unpack_vosk(zp, dst)
ok(L.vosk_complete(md) and (md / ".complete").exists() and (md / "graph/Gr.fst").read_text() == "ok" and not list(dst.glob(".*unpack")),
   f"распаковано заново, целиком, временной папки не осталось: {sorted(x.name for x in dst.iterdir())}")

# ── настоящее ядро: папка состояния кириллицей ──
VOSK = "vosk-model-small-ru-0.22"


def run_core(state, extra_env, wait=60):
    cfg = state.parent / "cfg"; cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "settings.json").write_text(json.dumps({"persona": "lira", "stt": {"engine": "vosk"}, "tts": {"engine": "off"},
                                                   "audio": {"sounds": False}, "ui": {"welcome": "done", "intro": "done"}}))
    log = state.parent / "core.log"
    env = dict(os.environ, DEWIAR_CONFIG_DIR=str(cfg), DEWIAR_STATE_DIR=str(state), DEWIAR_PORT=str(free()), DEWIAR_ASSUME_AEC="1",
               DEWIAR_WORKSPACE_DIR=str(state.parent / "ws"), DEWIAR_FAKE_RAM_GB="16", DEWIAR_FAKE_CORES="8", **extra_env)
    pr = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(ROOT / "tests/silence90.wav"), "--null-audio"], env=env,
                          stdout=open(log, "w"), stderr=subprocess.STDOUT)
    end = time.time() + wait
    text = ""
    while time.time() < end:
        time.sleep(0.5)
        text = log.read_text(encoding="utf-8", errors="replace")
        if "слух готов" in text and ("Имена (мгновенно)" in text or "не загрузилась" in text):
            time.sleep(1.5); text = log.read_text(encoding="utf-8", errors="replace"); break
    pr.terminate()
    try:
        pr.wait(10)
    except subprocess.TimeoutExpired:
        pr.kill()
    return text


if not (ROOT / "data" / f"{VOSK}.zip").exists():         # открытая копия на GitHub — без моделей в data/
    print("нет data/" + VOSK + ".zip — проверку настоящим ядром пропускаю")
    print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
    sys.exit(1 if fails else 0)
base = tmp / "core1"
state = base / "Пользователи" / "Виталий" / "AppData" / "Dewiar"
(state / "models").mkdir(parents=True)
shutil.copy(ROOT / "data" / f"{VOSK}.zip", state / "models" / f"{VOSK}.zip")
os.environ["DEWIAR_KALDI_BASE"] = str(base / "ascii")
log = run_core(state, {})
copy = base / "ascii" / "Dewiar" / "models" / VOSK
ok("слух готов" in log and (copy / ".complete").exists() and "Kaldi его не откроет" in log and "не загрузилась" not in log,
   f"ядро: модель имён — из ASCII-копии, слух готов: {[l for l in log.splitlines() if 'слух' in l or 'Kaldi' in l or 'Vosk' in l][:4]}")
ok("Имена (мгновенно): лира" in log, "имена слышны (модель имён загружена)")

# испорченная копия, которую не починить: слух всё равно поднимается — без модели имён, кнопка работает
base = tmp / "core2"
state = base / "Пользователи" / "Виталий" / "AppData" / "Dewiar"
(state / "models").mkdir(parents=True)
shutil.copy(ROOT / "data" / f"{VOSK}.zip", state / "models" / f"{VOSK}.zip")
bad = base / "ascii" / "Dewiar" / "models" / VOSK
bad.mkdir(parents=True); (bad / ".complete").write_text("ok")          # «готово», а внутри пусто — Kaldi не откроет
os.environ["DEWIAR_KALDI_BASE"] = str(base / "ascii")
log = run_core(state, {})
warn = [l for l in log.splitlines() if "не загрузилась" in l]
ok(len(warn) == 2 and "слух готов" in log and "Traceback" not in log,
   f"модель не открылась — раз распаковали заново, второй раз — слух без модели имён, без падения: {warn[:2]}")
ok((state / "models" / VOSK / ".complete").exists(), "перед второй попыткой модель распакована заново")
shutil.rmtree(tmp, ignore_errors=True)
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
