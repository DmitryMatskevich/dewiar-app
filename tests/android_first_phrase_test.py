"""Телефон: первая фраза ответа — в голос как можно раньше (голос сервера синтезирует фразу целиком; длинное первое
предложение задерживало первый звук на секунды). Как на компьютере (SentenceSplitter): первая — по запятой или тире
после 60 знаков; дальше режем только очень длинные (220+); короткие фразы («Отлично, рада слышать.» — у Лиры в женском роде) — целиком."""
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
LONG = ("Сегодня в Минске облачно с прояснениями, днём до двенадцати градусов, ветер северо-западный, к вечеру возможен "
        "небольшой дождь. Возьмите зонт. ")
CASES = {
    "long": LONG,
    "short": "Отлично, рад слышать. Чем займёмся? ",
}
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page()
    pg.on("pageerror", lambda x: errs.append(str(x)))
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(700)
    R = pg.evaluate("""(cases) => { const out = {}; const real = window.say;
        for (const [k, text] of Object.entries(cases)) {
          const said = []; say = (t) => said.push([t, seen.length]); let seen = "";
          const sk = sentenceSink();
          for (const w of text.match(/\\S+\\s*/g)) { seen += w; sk.push(w); }   // по словам, как поток модели
          sk.flush(); out[k] = said;
        }
        say = real; return out; }""", CASES)
    # ⏱ время до звука — одной строкой в журнале
    T = pg.evaluate("""async () => { D.ttsReady = () => true; turnT = { t0: Date.now() - 2000, word: 0, said: 0, logged: false };
        const sk = sentenceSink(); sk.push("Привет. "); await new Promise(r => setTimeout(r, 200));
        return PLOG.filter(x => x.includes("⏱")); }""")
    b.close()
ok(len(T) == 1 and "до звука" in T[0] and "модель начала отвечать за" in T[0] and "синтез и доставка" in T[0], f"журнал: куда ушло время до первого звука: {T}")
lg = R["long"]
ok(len(lg) >= 3 and lg[0][0].strip().endswith(",") and 25 < len(lg[0][0]) <= 100 and lg[0][1] < LONG.index("."),
   f"длинная первая фраза — в голос по запятой, до конца предложения: {[x[0] for x in lg]}")
ok("".join(x[0] for x in lg).replace(" ", "") == LONG.replace(" ", ""), "текст не потерян и не задвоен")
ok([x[0].strip() for x in R["short"]] == ["Отлично, рада слышать.", "Чем займёмся?"], f"короткие фразы — целиком: {R['short']}")
ok(not errs, "без ошибок JavaScript" + (f": {errs}" if errs else ""))
shutil.rmtree(tmp, ignore_errors=True)
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
