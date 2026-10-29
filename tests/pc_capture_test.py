"""Компьютер: «Закинуть идею» одной клавишей (идея 16.10). Окно ui/capture.html по Ctrl+Alt+N: картинка из буфера
приложена сама, Enter — сохранить без модели и без голоса; «напомни завтра в 10 …» — напоминание (с картинками — в их
заметке), остальное — заметка «Заметки/Идеи». Сочетание (ui/hotkey.py): GNOME — своё сочетание клавиатуры с командой
«… app.py --capture», чужие сочетания не трогаем и занятую клавишу не отбираем; Windows — RegisterHotKey."""
import base64, io, json, os, socket, stat, subprocess, sys, tempfile, threading, time
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
TESTS = Path(__file__).resolve().parent
PY = os.environ.get("DEWIAR_PY") or sys.executable
tmp = Path(tempfile.mkdtemp())
sys.path.insert(0, str(ROOT / "ui"))
fails = []
SHOT = os.environ.get("CAP_SHOT")


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


# ---------- ui/hotkey.py ----------
import hotkey as H  # noqa: E402
ok(H.to_gnome("Ctrl+Alt+N") == "<Control><Alt>n" and H.to_gnome("Super+Shift+Space") == "<Super><Shift>space" and H.to_gnome("N") == "",
   "сочетание → вид GNOME")
ok(H.to_win("Ctrl+Alt+N") == (0x4000 | 0x2 | 0x1, ord("N")) and H.to_win("ctrl + F5") == (0x4002, 0x74) and H.to_win("Alt") is None, "сочетание → RegisterHotKey")
# поддельный gsettings: значения в JSON, как текст GVariant
bin_ = tmp / "bin"; bin_.mkdir()
db = tmp / "gs.json"
db.write_text(json.dumps({f"{H.GN_SCHEMA}|custom-keybindings": "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/']",
                          f"{H.GN_ONE}:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/|name": "'Терминал'",
                          f"{H.GN_ONE}:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/|binding": "'<Control><Alt>t'",
                          f"{H.GN_ONE}:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/|command": "'gnome-terminal'"}))
fake = bin_ / "gsettings"
fake.write_text(f"""#!{sys.executable}
import json, sys
db = {str(db)!r}; d = json.load(open(db)); a = sys.argv[1:]
if a[0] == "list-relocatable-schemas": print({H.GN_ONE!r}); sys.exit(0)
if a[0] == "list-schemas": print({H.GN_SCHEMA!r}); sys.exit(0)
k = a[1] + "|" + a[2]
if a[0] == "get": print(d.get(k, "@as []" if a[2] == "custom-keybindings" else "''")); sys.exit(0)
if a[0] == "set":
    v = a[3]
    if not (v.startswith("'") or v.startswith("[") or v.startswith("@")): print("bad value: " + v, file=sys.stderr); sys.exit(1)
    d[k] = v
if a[0] == "reset": d.pop(k, None)
json.dump(d, open(db, "w"))
""")
fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
os.environ["PATH"] = str(bin_) + os.pathsep + os.environ["PATH"]
ok(H.gnome_available(), "GNOME: схема своих сочетаний найдена")
cmd = H.command(ROOT)
r = H.gnome_set(True, "Ctrl+Alt+N", cmd)
d = json.loads(db.read_text())
lst = d[f"{H.GN_SCHEMA}|custom-keybindings"]
ok(r[0] and H.GN_PATH in lst and "custom0" in lst and d[f"{H.GN_ONE}:{H.GN_PATH}|binding"] == "'<Control><Alt>n'",
   f"GNOME: наше сочетание добавлено, чужое (Терминал) на месте: {r} {lst}")
st = H.gnome_state("Ctrl+Alt+N")
ok(st["on"] and st["binding"] == "<Control><Alt>n" and st["command"] == cmd and cmd.endswith("app.py --capture"), f"GNOME: состояние читается: {st}")
r2 = H.gnome_set(True, "Ctrl+Alt+T", cmd)
ok(not r2[0] and "Терминал" in r2[1], f"занятую клавишу не отбираем: {r2}")
r3 = H.gnome_set(False, "Ctrl+Alt+N", cmd)
d = json.loads(db.read_text())
ok(r3[0] and H.GN_PATH not in d[f"{H.GN_SCHEMA}|custom-keybindings"] and "custom0" in d[f"{H.GN_SCHEMA}|custom-keybindings"]
   and f"{H.GN_ONE}:{H.GN_PATH}|binding" not in d, "выключили — убрано только наше")

# ---------- ядро: capture_parse / capture_save ----------
from websockets.sync.client import connect  # noqa: E402
P = free()
cfg = tmp / "cfg"; cfg.mkdir()
(cfg / "settings.json").write_text(json.dumps({"persona": "dewiar", "audio": {"sounds": False}, "ui": {"news_seen": "99.0.0", "welcome": "done"}, "welcome": {"on": False}}))
WS = tmp / "ws"
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(cfg), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(P), DEWIAR_ASSUME_AEC="1", DEWIAR_WORKSPACE_DIR=str(WS))
env.pop("DEWIAR_WORKSPACE", None)
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio"], env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def png():
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (320, 200), (70, 120, 220)).save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


IMG = png()
try:
    for _ in range(150):
        time.sleep(0.2)
        if (cfg / "token").exists():
            break
    time.sleep(2)
    tok = (cfg / "token").read_text().strip()
    ws = connect(f"ws://127.0.0.1:{P}", open_timeout=5)
    ws.send(json.dumps({"cmd": "hello", "token": tok})); ws.recv(timeout=10)
    n = [0]

    def call(cmd, args):
        n[0] += 1; ws.send(json.dumps({"id": n[0], "cmd": cmd, "args": args}))
        while True:
            m = json.loads(ws.recv(timeout=20))
            if m.get("type") == "reply" and m.get("id") == n[0]:
                if not m.get("ok", True):
                    raise RuntimeError(m.get("error"))
                return m.get("data")
    ok(call("capture_parse", {"text": "Сделать лендинг для пекарни"}) == {"kind": "note", "folder": "Идеи"}, "мысль — в «Идеи»")
    pr = call("capture_parse", {"text": "напомни завтра в 10 обсудить макет"})
    ok(pr["kind"] == "reminder" and pr["text"] == "Обсудить макет" and pr["when"].startswith("завтра"), f"«напомни…» — напоминание: {pr}")
    r = call("capture_save", {"text": "Сделать лендинг для пекарни: тёплые цвета, фото хлеба\nи меню на день", "images": [IMG]})
    note = WS / r["path"]
    body = note.read_text(encoding="utf-8")
    ok(r["kind"] == "note" and r["path"].startswith("Заметки/Идеи/Сделать лендинг для пекарни") and r["att"] == 1 and "меню на день" in body and "![" in body,
       f"идея с картинкой — заметка «Идеи» с ней: {r}")
    r = call("capture_save", {"text": "напомни завтра в 10 обсудить макет", "images": [IMG]})
    rem = next((x for x in call("reminders", {}) if x["id"] == r["id"]), None)
    ok(r["kind"] == "reminder" and rem and rem.get("att") == 1 and rem["text"] == "Обсудить макет", f"«напомни…» с картинкой — напоминание с ней: {r}")
    try:
        call("capture_save", {"text": "  "}); ok(False, "пустое — ошибка")
    except RuntimeError as e:
        ok("пусто" in str(e), f"пустое не сохраняем: {e}")
    # окно
    with sync_playwright() as p:
        b = p.chromium.launch(); errs = []
        pg = b.new_page(viewport={"width": 600, "height": 440}); pg.on("pageerror", lambda x: errs.append(str(x)))
        pg.add_init_script("""window.pywebview = { api: { clipboard_image: async () => window._clip || '', close_capture: () => { window._closed = (window._closed || 0) + 1; } } };
            window._clip = '""" + IMG + "';")
        pg.goto((ROOT / "ui/capture.html").as_uri() + f"?token={tok}&port={P}"); time.sleep(1.5)
        g = pg.evaluate("() => [document.activeElement.id, document.querySelectorAll('#atts .att').length, (document.querySelector('#atts small') || {}).textContent]")
        ok(g == ["t", 1, "из буфера"], f"окно: курсор в поле, снимок из буфера уже приложен: {g}")
        pg.keyboard.type("напомни в пятницу в 15:00 показать Саше")
        time.sleep(0.6)
        w = pg.evaluate("document.getElementById('what').textContent")
        # в пятницу до 15:00 «в пятницу» — это сегодня, и подпись честно говорит «через N мин»
        ok("Напомню" in w and ("в 15:00" in w or "через" in w) and "Показать Саше" in w, f"под полем видно, что получится: {w!r}")
        if SHOT:
            pg.screenshot(path=str(Path(SHOT) / "capture.png"))
        pg.keyboard.press("Enter"); time.sleep(0.8)
        d = pg.evaluate("() => [document.getElementById('done').classList.contains('on'), document.getElementById('d-h').textContent]")
        ok(d[0] and d[1].startswith("Напомню в пятницу") or d[1].startswith("Напомню сегодня") or d[1].startswith("Напомню"), f"Enter — сохранено: {d}")
        if SHOT:
            pg.screenshot(path=str(Path(SHOT) / "capture-done.png"))
        time.sleep(1.2)
        ok(pg.evaluate("window._closed") == 1, "сохранили — окно ушло само")
        # второй раз: тот же снимок в буфере уже не прикладываем сам; Esc — набранное остаётся
        pg.evaluate("() => capOpen()"); time.sleep(0.5)
        ok(pg.evaluate("document.querySelectorAll('#atts .att').length") == 0, "тот же снимок второй раз сам не прикладывается")
        pg.keyboard.type("Мысль на потом"); pg.keyboard.press("Escape"); time.sleep(0.2)
        pg.reload(); time.sleep(1.2)
        ok(pg.evaluate("document.getElementById('t').value") == "Мысль на потом", "Esc — набранное не пропало")
        ok(not errs, f"окно без ошибок: {errs[:3]}")
        b.close()
    ideas = sorted(x.name for x in (WS / "Заметки/Идеи").glob("*.md"))
    ok(len(ideas) == 1, f"в «Идеи» — одна заметка (напоминания туда не идут): {ideas}")
finally:
    core.terminate()
    try:
        core.wait(10)
    except subprocess.TimeoutExpired:
        core.kill()
src = (ROOT / "ui/app.py").read_text(encoding="utf-8")
ok('"--capture"' in src and 'what == "capture"' in src and "Закинуть идею" in src, "app.py: --capture, сигнал и пункт в трее")
ok("RegisterHotKey" in (ROOT / "ui/winshell.py").read_text(encoding="utf-8"), "Windows: сочетание — RegisterHotKey")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
