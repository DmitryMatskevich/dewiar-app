"""Телефон: «сам замечает» — открыли приложение после перерыва, и ассистент коротко говорит главное (как на компьютере).
Первый раз за день после 3 часов — сводка дня; после 30 минут — только если есть что сказать (готовая с тех пор задача,
задача ждёт ответа, скоро напоминание). Ночью, чаще раза в 40 минут, если выключено, если человек уже сам заговорил — молчит.
Переключатель — «Настройки» → «Слух и голос»."""
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
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 393, "height": 852}); errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(800)
    R = pg.evaluate("""async () => {
      const H = 3600000, M = 60000, R = {};
      const day = (h, m) => { const d = new Date(); d.setHours(h, m || 0, 0, 0); return d.getTime(); };
      const iso = (t) => new Date(t).toISOString().slice(0, 19);
      const now = day(14, 0);
      const tasks = [{ title: "Обзор новостей", status: "done", updated: iso(now - 10 * M) },
                     { title: "Старая", status: "done", updated: iso(now - 30 * H) }, { title: "План поездки", status: "waiting" }];
      const alarms = [{ title: "Созвон с Андреем", at: now + 30 * M, kind: "reminder" }, { title: "Таймер", at: now + 5 * M, kind: "timer" },
                      { title: "Тренировка", at: now + 5 * H, kind: "reminder" }];
      R.back = welcomePlan(now, now - 50 * M, now - 3 * H, tasks, alarms);
      R.nothing = welcomePlan(now, now - 50 * M, now - 3 * H, [], []);
      R.short = welcomePlan(now, now - 10 * M, null, tasks, alarms);
      R.recent = welcomePlan(now, now - 50 * M, now - 20 * M, tasks, alarms);
      R.night = welcomePlan(day(23, 30), day(23, 30) - 5 * H, null, tasks, alarms);
      const morning = day(8, 0);
      R.day = welcomePlan(morning, morning - 9 * H, morning - 14 * H, [], [{ title: "Позвонить маме", at: day(10, 0), kind: "reminder" }]);
      R.dayPrompt = R.day && welcomePrompt(R.day);
      R.never = welcomePlan(now, 0, null, tasks, alarms);
      // в приложении: ушли на час, вернулись — приветствие уходит модели скрытым вопросом
      const asked = []; window.ask = async (t, o) => { asked.push([t, o]); };
      window.ensureBrain = async () => {}; S.llm.base_url = "http://127.0.0.1:9/v1";
      window.mind = () => ({ tasks: { a: tasks[0] } });
      D.alarms = () => JSON.stringify([{ title: "Созвон", at: Date.now() + 20 * M, kind: "reminder" }]);
      const h = new Date().getHours(), dayTime = h >= 7 && h < 23;
      S.welcome = { on: true, seen: Date.now() - 2 * H, at: 0 };
      R.fired = await welcomeCheck(Date.now() - 100);
      R.asked = asked.slice(); R.hidden = asked[0] && asked[0][1] && asked[0][1].hidden; R.dayTime = dayTime;
      R.again = await welcomeCheck(Date.now() - 100);
      S.welcome = { on: false, seen: Date.now() - 2 * H, at: 0 };
      R.off = await welcomeCheck(Date.now() - 100);
      S.welcome = { on: true, seen: Date.now() - 2 * H, at: 0 }; lastUserAct = Date.now();
      R.talked = await welcomeCheck(Date.now() - 1000);
      // переключатель в настройках
      openSettings("voice"); await new Promise(r => setTimeout(r, 300));
      const sw = document.getElementById("wl-on"); R.sw = !!sw && sw.checked;
      if (sw) { sw.checked = false; sw.dispatchEvent(new Event("change")); }
      R.swSaved = JSON.parse(D.pref("settings")).welcome.on;
      // ушли с экрана — запомнили, когда
      S.welcome.seen = 0; welcomeSeen(); R.seen = S.welcome.seen > 0;
      return R;
    }""")
    b.close()

back = R["back"] or {}
facts = " ".join(back.get("facts", []))
ok(back.get("kind") == "back" and "Обзор новостей" in facts and "Старая" not in facts and "План поездки" in facts and "Созвон с Андреем" in facts
   and "Таймер" not in facts and "Тренировка" not in facts and back.get("gone") == "50 мин", f"вернулись через 50 мин — главное: {back}")
ok(R["nothing"] is None, "сказать нечего — молчит")
ok(R["short"] is None, "отлучка меньше 30 минут — молчит")
ok(R["recent"] is None, "здоровались 20 минут назад — молчит")
ok(R["night"] is None, "ночью — молчит")
ok(R["never"] is None, "первый запуск (ещё не видели) — без приветствия")
ok(R["day"] and R["day"]["kind"] == "day" and "Позвонить маме" in " ".join(R["day"]["facts"]) and R["day"]["gone"] == "9 ч", f"утром — сводка дня: {R['day']}")
ok("первый раз за день" in (R["dayPrompt"] or "") and "без инструментов" in R["dayPrompt"], "поручение модели — сводка дня")
if R["dayTime"]:
    ok(R["fired"] and len(R["asked"]) == 1 and R["hidden"] and "Созвон" in R["asked"][0][0], "в приложении: вернулись — заговорил сам (скрытый вопрос модели)")
    ok(not R["again"], "сразу второй раз — нет")
ok(not R["off"], "выключено в настройках — молчит")
ok(not R["talked"], "человек уже сам заговорил — не перебивает")
ok(R["sw"] and R["swSaved"] is False, "переключатель «Сам замечает» в «Слух и голос» сохраняется")
ok(R["seen"], "ушли с экрана — помнит, когда")
ok(not errors, f"без ошибок JavaScript: {errors}")
print("\nИТОГ:", "всё хорошо" if not fails else f"ошибок {len(fails)}")
sys.exit(1 if fails else 0)
