"""Суфлёр: «Закончить встречу?» — своё окно внутри суфлёра, а не системное confirm() (отзыв 11.10: в Linux окно вопроса
шире суфлёра и обрезалось). ui/prompter.html в Chromium размером окна суфлёра, ядро — заглушка:
— крестик во время встречи открывает вопрос внутри окна, системного диалога нет; всё помещается в окно;
— «Продолжить» и Escape — встреча идёт дальше; «Закончить» — итог (prompter_stop summary) и окно закрывается."""
import os, shutil, sys, tempfile
from pathlib import Path
SRC = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


from playwright.sync_api import sync_playwright  # noqa: E402
tmp = Path(tempfile.mkdtemp()) / "ui"
shutil.copytree(SRC / "ui", tmp)
(tmp / "assets/dewiar.js").write_text("""
window.CALLS = []; window.CLOSED = 0;
window.pywebview = { api: { close_prompter() { window.CLOSED++; }, open_settings() {} } };
window.api = () => window.pywebview.api;
const ST = { on: true, brain: true, paused: false, status: 'слушаю микрофон', meeting: { title: 'Встреча', started: Date.now() / 1000 - 240 },
  recording: { enabled: true, audio_paused: false, audio_source: 'system' },
  hints: [{ title: 'Уточните детали: дату, время и ключевые пункты повестки.', points: ['Спросите: «Какие конкретные вопросы мы обсуждаем?»'] }],
  lines: [{ who: 'me', text: 'Ну, я не знаю, не помню точно.' }], files: [], sheet: [] };
window.core = { _on: {}, on(k, f) { (this._on[k] = this._on[k] || []).push(f); },
  async call(cmd, a) { window.CALLS.push([cmd, a || {}]); if (cmd === 'prompter_state') return ST; return {}; } };
setTimeout(() => (core._on.hello || []).forEach(f => f({})), 50);
""", encoding="utf-8")
with sync_playwright() as p:
    b = p.chromium.launch(); errs, dialogs = [], []
    pg = b.new_page(viewport={"width": 440, "height": 690})
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    pg.goto((tmp / "prompter.html").as_uri()); pg.wait_for_timeout(600)
    ok(pg.evaluate("view") == "live", "встреча идёт — экран встречи")
    ok(pg.is_visible("#capture-live-audio"), "во время записи видна отдельная кнопка паузы звука")
    pg.click("#capture-live-audio"); pg.wait_for_timeout(100)
    ok("prompter_audio_pause" in pg.evaluate("CALLS.map(c => c[0])"), "пауза звука не останавливает встречу")
    pg.click("#b-close"); pg.wait_for_timeout(300)
    box = pg.evaluate("(() => { const r = document.querySelector('#endask .card').getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom, innerWidth, innerHeight]; })()")
    ok(not dialogs and pg.is_visible("#endask") and box[0] >= 0 and box[1] <= box[4] and box[3] <= box[5],
       f"крестик — вопрос внутри окна, без системного диалога, помещается: {box} {dialogs}")
    pg.screenshot(path=str(Path(tempfile.gettempdir()) / "prompter_end.png"))
    ok(pg.evaluate("document.activeElement.id") == "endask-yes", "фокус — на «Закончить» (Enter закончит)")
    pg.keyboard.press("Escape"); pg.wait_for_timeout(100)
    ok(not pg.is_visible("#endask") and pg.evaluate("CLOSED") == 0, "Escape — встреча продолжается")
    pg.click("#b-close"); pg.click("#endask-no"); pg.wait_for_timeout(100)
    ok(not pg.is_visible("#endask") and pg.evaluate("CLOSED") == 0, "«Продолжить» — встреча продолжается")
    pg.click("#b-close"); pg.click("#endask-yes"); pg.wait_for_timeout(200)
    calls = pg.evaluate("CALLS.map(c => c[0])")
    ok("prompter_stop" in calls and pg.evaluate("CALLS.find(c => c[0] === 'prompter_stop')[1].summary") is True and pg.evaluate("CLOSED") == 1,
       f"«Закончить» — итог и окно закрывается: {calls}")
    b.close()
ok(not errs, f"без ошибок на странице: {errs[:3]}")
ok("confirm(" not in (SRC / "ui/prompter.html").read_text(encoding="utf-8"), "в суфлёре нет системных confirm()")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
