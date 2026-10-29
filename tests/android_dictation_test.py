"""Длинная диктовка на телефоне: распознаватель телефона сам обрывает примерно через минуту, запись для сервера — через
15 с. Оборвалось на лимите — сказанное копится и помощница слушает дальше; уходит всё вместе, когда человек замолчит
(или следующий отрезок пустой). Сам нажал «хватит» — не продолжает. Короткая фраза — как раньше, сразу."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


www = Path(tempfile.mkdtemp()) / "www"
shutil.copytree(ROOT / "android/assets/www", www)
for f in ("entities.js", "skins.css", "skins.js", "privacy.js"):
    shutil.copy(ROOT / "ui/assets" / f, www / f)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
src = (ROOT / "android/src/pro/dewiar/app/Speech.java").read_text(encoding="utf-8")
ok('put("cut", !hold && frames > 300)' in src, "запись для сервера сообщает, что оборвалась по лимиту (кроме удержания — там пишем до «отпустили»)")
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    pg = b.new_page(viewport={"width": 393, "height": 820}); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.route("https://**", lambda r: r.abort())
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(800)
    pg.evaluate("""() => { window._asked = []; ask = (t) => { _asked.push(t); setState('idle'); };
      window._listens = 0; D.recognizer = () => true; D.listen = (g) => { _listens++; }; D.listenServer = (g) => { _listens++; }; needPerm = async () => true; }""")
    ev = lambda js: (pg.evaluate(js), pg.wait_for_timeout(250))
    # телефонное распознавание: минута речи — оборвалось на лимите
    ev("async () => { await listen(); sttT0 = Date.now() - 58000; N.ev({ type: 'stt_partial', g: sttGen, text: 'первая часть длинной заметки' }); N.ev({ type: 'stt_result', g: sttGen, text: 'первая часть длинной заметки' }); }")
    r1 = pg.evaluate("[_asked.length, _listens, document.querySelector('#you').textContent]")
    ok(r1[0] == 0 and r1[1] == 2 and "первая часть" in r1[2], f"телефон оборвал через минуту — ничего не потеряно, слушаю дальше: {r1}")
    ev("() => { sttT0 = Date.now() - 6000; N.ev({ type: 'stt_result', g: sttGen, text: 'и вторая часть' }); }")
    r2 = pg.evaluate("_asked.slice()")
    ok(r2 == ["первая часть длинной заметки и вторая часть"], f"замолчал — всё вместе одной просьбой: {r2}")
    # следующий отрезок пустой — уходит накопленное
    ev("async () => { _asked = []; await listen(); sttT0 = Date.now() - 50000; N.ev({ type: 'stt_result', g: sttGen, text: 'только начало' }); }")
    ev("() => N.ev({ type: 'stt_error', g: sttGen, code: 6, msg: 'речи не было' })")
    ok(pg.evaluate("_asked.slice()") == ["только начало"], f"после обрыва тишина — уходит то, что сказано: {pg.evaluate('_asked.slice()')}")
    # сервер: запись 15 с оборвалась (cut)
    ev("async () => { _asked = []; S.stt = { engine: 'server', url: 'http://x/v1', key: '' }; await listen(); N.ev({ type: 'stt_result', g: sttGen, text: 'пятнадцать секунд', cut: true }); }")
    ev("() => N.ev({ type: 'stt_result', g: sttGen, text: 'и конец', cut: false })")
    ok(pg.evaluate("_asked.slice()") == ["пятнадцать секунд и конец"], f"сервер: запись по лимиту склеивается: {pg.evaluate('_asked.slice()')}")
    # сам нажал «хватит» — не продолжает, даже после долгой речи
    ev("async () => { _asked = []; S.stt = { engine: 'system' }; await listen(); sttT0 = Date.now() - 70000; D.stopListen = () => {}; document.querySelector('#mic').click(); N.ev({ type: 'stt_result', g: sttGen, text: 'хватит на этом' }); }")
    ok(pg.evaluate("_asked.slice()") == ["хватит на этом"], f"нажал «хватит» — сразу, без «слушаю дальше»: {pg.evaluate('_asked.slice()')}")
    ev("async () => { _asked = []; await listen(); sttT0 = Date.now() - 4000; N.ev({ type: 'stt_result', g: sttGen, text: 'который час' }); }")
    ok(pg.evaluate("_asked.slice()") == ["который час"], "короткая фраза — сразу, как раньше")
    b.close()
ok(not errs, f"без ошибок на странице: {errs[:3]}")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
