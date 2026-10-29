"""Телефон: голос сервера не ответил (служба голосов остановлена, сервер недоступен) — фраза звучит голосом телефона,
а не пропадает; следующие фразы две минуты сразу идут голосом телефона (не ждём отказа на каждой); предупреждение —
одно, а не на каждую фразу; через две минуты снова пробуем сервер. Нет русского голоса в телефоне — как раньше,
только сообщение об ошибке. Голосу сервера телефон передаёт персонажа (persona)."""
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
shutil.copy(ROOT / "ui/assets/entities.js", www / "entities.js")
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto((www / "index.html").as_uri())
    pg.wait_for_timeout(800)
    R = pg.evaluate("""() => {
        const spoken = [], remote = [], toasts = [];
        D.ttsReady = () => true;
        D.speak = (id, t) => spoken.push({ id, t });
        D.speakRemote = (id, url, key, body) => remote.push({ id, body: JSON.parse(body) });
        const was = window.toast; toast = (m) => toasts.push(m);
        S.persona = "lira"; S.textMode = false;
        S.tts = Object.assign({}, DEF.tts, { engine: "server", url: "http://10.0.0.5:8000/v1" });
        say("Первая фраза.");
        const r1 = remote.slice(), left1 = sayLeft;
        N.ev({ type: "tts_error", id: r1[0].id, error: "HTTP 502" });
        N.ev({ type: "tts_done", id: r1[0].id });
        const fb = spoken.slice(), left2 = sayLeft;
        say("Вторая фраза."); say("Третья фраза.");
        const afterDown = { remote: remote.length, spoken: spoken.length };
        N.ev({ type: "tts_error", id: "x", error: "HTTP 502" });
        srvVoiceDownAt = Date.now() - 121e3;                     // прошло две минуты
        say("Четвёртая фраза.");
        const retry = remote.length;
        const t1 = toasts.length;
        // нет голоса в телефоне — повторять нечем
        D.ttsReady = () => false; srvVoiceDownAt = 0;
        say("Пятая.");
        const id5 = remote[remote.length - 1].id;
        N.ev({ type: "tts_error", id: id5, error: "HTTP 502" });
        return { r1, left1, fb, left2, afterDown, retry, t1, toasts, spokenN: spoken.length };
    }""")
    b.close()

ok(R["r1"][0]["body"].get("persona") == "lira", f"запрос ушёл на свой сервер голоса с персонажем: {R['r1'][0]['body']}")
ok(R["fb"] and R["fb"][0]["t"] == "Первая фраза." and R["fb"][0]["id"].startswith("f"), f"не ответил — та же фраза голосом телефона: {R['fb']}")
ok(R["left2"] == 1, f"ход ждёт, пока договорит телефон (фраз в работе: {R['left2']})")
ok(R["afterDown"] == {"remote": 1, "spoken": 3}, f"следующие фразы сразу голосом телефона: {R['afterDown']}")
ok(R["retry"] == 2, "через две минуты снова пробуем сервер")
ok(R["t1"] == 1 and "голосом телефона" in R["toasts"][0], f"предупреждение одно: {R['toasts'][:2]}")
ok(R["spokenN"] == 3 and any("Голос сервера: HTTP 502" in t for t in R["toasts"]), "нет русского голоса в телефоне — только сообщение, как раньше")
ok(not errors, "без ошибок JavaScript" + (f": {errors}" if errors else ""))
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
