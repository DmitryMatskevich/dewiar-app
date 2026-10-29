"""Письмо пользователя 23.10 («при переключении персонажей они продолжали чужую тему; хочу, чтобы мой персонаж помнил
разговор со мной, Лира — с Лирой; передавать беседу — по моей просьбе»): на компьютере — с 0.39.6, теперь и на телефоне.
Разговор с другим персонажем в окно не попадает; передали слово явно («позови Патрика», call_persona) — последний ответ
помечен handoff, и новому персонажу этот разговор приходит справкой."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


tmp = Path(tempfile.mkdtemp()); www = tmp / "www"
shutil.copytree(ROOT / "android/assets/www", www)
shutil.copy(ROOT / "ui/assets/entities.js", www / "entities.js")
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
try:
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(900)
        pg.evaluate("""() => { window._asked = []; ask = (t, o) => { window._asked.push(t); return Promise.resolve(); };
            S.persona = 'patrik'; saveS(); applyPersona(); history.length = 0;
            history.push({ role: 'user', content: 'Обсудим ремонт ванной' }, { role: 'assistant', content: 'План: плитка, сантехника, бюджет.', who: 'patrik' }); saveH(); }""")
        # просто выбрали Лиру (без передачи слова) — разговор Патрика не её
        h = pg.evaluate("() => { S.persona = 'lira'; saveS(); applyPersona(); return chatHistory(history, 'Помоги с поздравлением маме'); }")
        ok(h == [{"role": "user", "content": "Помоги с поздравлением маме"}], f"выбрали Лиру сами — разговор с Патриком не в её окне: {h}")
        # «позови Лиру» у Патрика — передача слова
        pg.evaluate("() => { S.persona = 'patrik'; saveS(); applyPersona(); switchPersona('lira', 'Продолжи, пожалуйста'); }")
        ok(pg.evaluate("() => history[history.length - 1].handoff") == "lira" and pg.evaluate("() => persona().id") == "lira",
           "«позови Лиру» — последний ответ Патрика помечен передачей слова Лире")
        h = pg.evaluate("() => chatHistory(history, 'Продолжи, пожалуйста')")
        ok(len(h) == 1 and "Патрик передал(а) слово тебе" in h[0]["content"] and "плитка" in h[0]["content"] and h[0]["content"].endswith("Продолжи, пожалуйста"),
           f"Лире, которой передали слово, — разговор Патрика справкой: {h[0]['content'][:120]}")
        h = pg.evaluate("() => { S.persona = 'patrik'; return (applyPersona(), chatHistory(history, 'А что с бюджетом?')); }")
        ok([m["role"] for m in h] == ["user", "assistant", "user"] and "плитка" in h[1]["content"], "у Патрика его разговор остаётся")
        ok(not errs, f"без ошибок: {errs[:2]}")
        b.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if fails:
    raise SystemExit(1)
