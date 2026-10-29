"""Читаемость в светлых темах (отзыв 18.10: «В стиле "Небесный" много надписей практически не читаются» — роли
персонажей в студии; таймер и карточка напоминания в виджете: белые цифры и янтарная строка на светлом). Проверка —
по снимкам (tests/contrast_lib.py): цвет текста против настоящего фона под ним, контраст по WCAG.
Виджет: все светлые темы, все карточки (таймер, напоминание, заметка, расход, выключение) — не ниже 3.
Студия: каждый раздел с настоящим ядром, «Небесный», «Белоснежная» и тёмная «Фирменная» — не ниже 2.4 (второстепенные
подсказки в светлых темах нарочно мягкие, ~2.6, — их не трогаем; ловим то, что «практически не читается»).
Телефон: главный экран и «Мой день» — не ниже 2.4."""
import json, os, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
sys.path.insert(0, str(Path(__file__).resolve().parent))
from contrast_lib import audit  # noqa: E402
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


def show(bad):
    return "; ".join(f"{x['sel']} «{x['text'][:30]}» {x['ratio']} (текст {x['fg']}, фон {x['bg']})" for x in bad[:4])


LIGHT = ["neusky", "neu", "neumint", "snow", "glass"]
CARDS = {
    "таймер": "tmrSet([{id:'t1', kind:'timer', text:'Таймер', at: new Date(Date.now()+16000).toISOString(), created: new Date(Date.now()-4000).toISOString()}])",
    "напоминание": "core.emit('reminder_set', {item:{id:'r1', kind:'reminder', when:'сегодня в 15:00', text:'Позвонить в банк'}})",
    "заметка": "core.emit('note_saved', {kind:'note', title:'Идея для видео', text:'x', path:'a.md'})",
    "расход": "core.emit('money_saved', {id:'m1', amount: 25, kind:'expense', currency:'BYN', category:'Еда', note:'Кофе'})",
    "выключение": "core.emit('power', {seconds: 60, action:'shutdown'})",
    "сработало": "core.emit('reminder', {text:'Напоминание: позвонить в банк'})",
}


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


tmp = Path(tempfile.mkdtemp()); (tmp / "cfg").mkdir()
PORT, LLM = free(), free()
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "lira", "ui": {"skin": "neusky"},
    "llm": {"active": "t", "profiles": {"t": {"name": "Mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM}/v1", "disable_thinking": True}}},
    "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False, "follow_up_seconds": 0}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT),
           DEWIAR_ASSUME_AEC="1", DEWIAR_WORKSPACE_DIR=str(tmp / "ws"))
mock = subprocess.Popen([sys.executable, str(ROOT / "tests/mock_llm.py"), str(LLM)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(ROOT / "tests/silence90.wav"), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
from playwright.sync_api import sync_playwright  # noqa: E402
FAKE = "window.pywebview = { api: new Proxy({}, { get: () => () => Promise.resolve(true) }) };"
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        # ---------- виджет: карточки в светлых темах ----------
        for sk in LIGHT:
            pg = b.new_page(viewport={"width": 300, "height": 420}, device_scale_factor=2)
            pg.add_init_script(FAKE)
            pg.goto((ROOT / "ui/hud.html").as_uri()); pg.wait_for_timeout(600)
            pg.evaluate("(sk) => { document.body.classList.remove('mini'); core.emit('settings', { settings: { ui: { skin: sk } } }); }", sk)
            bad = []
            for name, js in CARDS.items():
                pg.evaluate("() => { " + js + " }"); pg.wait_for_timeout(450)
                bad += [dict(x, sel=f"{name}: {x['sel']}") for x in audit(pg, "body", 3.0)]
            ok(not bad, f"виджет, «{sk}»: таймер и карточки читаются (контраст ≥ 3): {show(bad)}")
            # №233 (24.10): «Ответ целиком» в «Небесном» — тёмные чернила на тёмном листе
            pg.evaluate("""() => { TL.you = 'Как устроена твоя память?'; TL.steps = [{ title: 'Ищу в заметках', status: 'done', detail: 'find_notes: память' }];
                TL.answer = '**Коротко.** Это блокнот с хорошим поиском: файл `Обо мне` и папка заметок.\\n\\n- первое\\n- второе'; openReader(); }""")
            pg.wait_for_timeout(300)
            bad = audit(pg, "#reader", 3.0)
            ok(pg.is_visible("#reader") and not bad, f"виджет, «{sk}»: «Ответ целиком» читается: {show(bad)}")
            if sk == "neusky":
                pg.screenshot(path="/tmp/claude-0/reader_neusky.png")
            pg.evaluate("() => $('reader').classList.remove('on')")
            pg.close()
        # ---------- студия: все разделы с настоящим ядром ----------
        for _ in range(150):
            time.sleep(0.2)
            if (tmp / "cfg/token").exists():
                break
        time.sleep(2)
        tok = (tmp / "cfg/token").read_text().strip()
        for sk in ("neusky", "snow", "brand"):
            pg = b.new_page(viewport={"width": 1280, "height": 1600})
            pg.add_init_script(FAKE + f" try {{ localStorage.setItem('studio_skin', '{sk}'); }} catch (e) {{}}")
            pg.goto((ROOT / "ui/settings.html").as_uri() + f"?port={PORT}&token={tok}"); pg.wait_for_timeout(2200)
            pg.evaluate("(sk) => { if (S) { S.ui = Object.assign(S.ui || {}, { skin: sk }); } core.emit('settings', { settings: Object.assign({}, S, { ui: Object.assign({}, (S || {}).ui, { skin: sk }) }) }); }", sk)
            pg.wait_for_timeout(400)
            bad = audit(pg, "dialog[open]", 2.4) if pg.evaluate("!!document.querySelector('dialog[open]')") else []
            pg.evaluate("() => { const d = document.querySelector('dialog[open]'); if (d) d.close(); }"); pg.wait_for_timeout(300)
            navs = pg.evaluate("() => $$('nav button[data-p]').map(b => b.dataset.p)")
            for n in navs:
                pg.evaluate("(n) => $(`nav button[data-p=\"${n}\"]`).click()", n); pg.wait_for_timeout(700)
                bad += [dict(x, sel=f"{n}: {x['sel']}") for x in audit(pg, "section.cur", 2.4)
                        if not x["sel"].startswith("b") or "calendar" != n]          # дни соседнего месяца в календаре — нарочно бледные
            cls = pg.evaluate("document.documentElement.className")
            ok(not bad and (("st-light" in cls) == (sk != "brand")), f"студия, «{sk}» ({cls}), {len(navs)} разделов: всё читается: {show(bad)}")
            pg.close()
        # ---------- телефон ----------
        www = tmp / "www"
        shutil.copytree(ROOT / "android/assets/www", www)
        for x in ("entities.js", "speechnorm.js", "skins.css", "skins.js"):
            shutil.copy(ROOT / "ui/assets" / x, www / x)
        shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)
        shutil.copytree(ROOT / "ui/assets/skins", www / "skins", dirs_exist_ok=True)
        (www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                        capture_output=True, text=True, check=True).stdout, encoding="utf-8")
        for sk in ("neusky", "snow"):
            pg = b.new_page(viewport={"width": 412, "height": 880})
            pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(1200)
            pg.evaluate("(sk) => { S.skin = sk; applyLook(); }", sk); pg.wait_for_timeout(500)
            bad = audit(pg, "body", 2.4)
            pg.evaluate("() => { markDone(['Позвонить в банк'], 'tap'); openDone(false); }"); pg.wait_for_timeout(700)
            bad += audit(pg, "body", 2.4)
            ok(not bad, f"телефон, «{sk}»: главный экран и «Мой день» читаются: {show(bad)}")
            pg.close()
        b.close()
finally:
    core.terminate(); mock.terminate()
    try:
        core.wait(10)
    except subprocess.TimeoutExpired:
        core.kill()
shutil.rmtree(tmp, ignore_errors=True)                # не оставлять копии страниц в /tmp
print("\nИТОГ:", "всё прошло" if not fails else f"не прошло {len(fails)}")
sys.exit(1 if fails else 0)
