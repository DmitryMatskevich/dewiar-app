"""Телефон: календарь Android (Cal.java + app.js) — без паролей и OAuth, в нём Google-календарь человека:
— «что у меня завтра»: встречи по дням, поиск по слову, пусто — так и сказано; не выдумывать — в подсказке модели;
— записать — только после «да»: calendar_add без confirmed ничего не пишет, говорит, о чём спросить, и предупреждает о
  встречах в то же время; с confirmed — записано в нужный день и час, на весь день — без времени; прошедшее время — ошибка;
— доступа не дали — читать: честное «разрешите доступ»; записать: открывается окно новой встречи в «Календаре»; нет
  календаря для записи — тоже окно;
— «записала в календарь» без настоящей записи — ловится (CLAIMS, calendar_write), после записи — нет;
— в экзамене — песочница EXAM.calendar: настоящий календарь не трогается; сценарии cal_tomorrow и cal_add;
— Android: разрешения READ_CALENDAR / WRITE_CALENDAR в манифесте, методы моста calEvents / calAdd / calOpenInsert."""
import os, re, shutil, subprocess, sys, tempfile
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
    R = pg.evaluate("""async () => {
        const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
        const at = (days, h, m = 0) => { const d = new Date(); d.setDate(d.getDate() + days); d.setHours(h, m, 0, 0); return d; };
        const o = {};
        o.empty = await runTool("calendar_list", {});
        D._cal.push({ id: 1, title: "Стоматолог", begin: +at(1, 10), end: +at(1, 11), all_day: false, location: "Клиника на Ленина" },
                    { id: 2, title: "Встреча с Игорем", begin: +at(1, 15), end: +at(1, 16), all_day: false, location: "" },
                    { id: 3, title: "Йога", begin: +at(2, 19), end: +at(2, 20), all_day: false, location: "" });
        o.tomorrow = await runTool("calendar_list", { from: iso(at(1, 0)) });
        o.week = await runTool("calendar_list", { days: 7 });
        o.igor = await runTool("calendar_list", { days: 7, query: "Игорь" });
        const n0 = D._cal.length;
        o.ask = await runTool("calendar_add", { title: "Созвон с Аней", start: iso(at(1, 0)) + "T15:30", minutes: 30 });
        o.askN = D._cal.length - n0;
        o.yes = await runTool("calendar_add", { title: "Созвон с Аней", start: iso(at(1, 0)) + "T15:30", minutes: 30, confirmed: true });
        const added = D._cal[D._cal.length - 1];
        o.added = { title: added.title, h: new Date(added.begin).getHours(), m: new Date(added.begin).getMinutes(), dur: (added.end - added.begin) / 6e4,
                    day: new Date(added.begin).toDateString() === at(1, 0).toDateString(), allDay: added.all_day };
        o.allday = await runTool("calendar_add", { title: "День рождения мамы", start: iso(at(5, 0)), confirmed: true });
        o.alldayE = D._cal[D._cal.length - 1].all_day;
        o.past = await runTool("calendar_add", { title: "Прошлое", start: iso(at(-1, 0)) + "T09:00", confirmed: true });
        o.bad = await runTool("calendar_add", { title: "Без даты", start: "завтра в три" });
        // доступа не дали
        const was = { calEvents: D.calEvents, calAdd: D.calAdd, has: D.has, perm: D.perm };
        D.has = () => false; D.perm = (n) => setTimeout(() => N.ev({ type: "perm", name: n, granted: false }), 0);
        D.calEvents = () => JSON.stringify({ need: "android.permission.READ_CALENDAR" });
        D.calAdd = () => JSON.stringify({ need: "android.permission.WRITE_CALENDAR" });
        window._calInsert = null;
        o.noRead = await runTool("calendar_list", {});
        o.noWrite = await runTool("calendar_add", { title: "Обед", start: iso(at(1, 0)) + "T13:00", confirmed: true });
        o.insert = window._calInsert;
        D.has = was.has; D.perm = was.perm; D.calEvents = was.calEvents;
        D.calAdd = () => JSON.stringify({ error: "no_calendar" }); window._calInsert = null;
        o.noCal = await runTool("calendar_add", { title: "Обед", start: iso(at(1, 0)) + "T13:00", confirmed: true });
        o.insert2 = !!window._calInsert;
        D.calAdd = was.calAdd;
        // честность
        o.claim = !!claimedAction("Записала встречу с Аней в календарь на завтра.", new Set(["calendar_add"]), "запиши");
        o.claimOk = !claimedAction("Записала встречу с Аней в календарь на завтра.", new Set(["calendar_add", "calendar_write"]), "запиши");
        o.claimOther = !claimedAction("Записала: молоко, хлеб.", new Set(), "запиши список");
        // экзамен: песочница
        const realN = D._cal.length;
        EXAM = { tools: [], alarms: [], calendar: [], stop: false };
        const ids = examSeed({ calendar: [{ title: "Стоматолог", day: 1, at: "10:00" }] });
        o.exList = await runTool("calendar_list", { from: iso(at(1, 0)) });
        const t0 = Date.now();
        await runTool("calendar_add", { title: "Встреча с Игорем", start: iso(at(2, 0)) + "T15:00", confirmed: true });
        o.exReal = D._cal.length === realN;
        o.exCheck = examChecks({ calendar: { any: ["игор"], day: 2, hour: 15 } }, "", [], t0);
        o.exCheckBad = examChecks({ calendar: { any: ["игор"], day: 2, hour: 16 } }, "", [], t0);
        examUnseed(ids);
        o.exLeft = EXAM.calendar.map(x => x.title);
        o.scen = EXAM_PHONE.filter(s => /^cal_/.test(s.id)).map(s => s.id);
        EXAM = null;
        o.specs = toolSpecs().map(t => t.name).filter(n => /calendar/.test(n));
        o.prompt = /calendar_list/.test(systemPrompt()) && /calendar_add/.test(systemPrompt());
        return o;
    }""")
    b.close()

ok("ничего нет" in R["empty"], f"пустой календарь — так и сказано: {R['empty']!r}")
t = R["tomorrow"]
ok("Стоматолог" in t and "10:00–11:00" in t and "Клиника на Ленина" in t and "Встреча с Игорем" in t and "Йога" not in t and "(2)" in t,
   f"завтра — обе встречи с временем и местом, послезавтрашней нет: {t!r}")
ok("Йога" in R["week"] and "не добавляй встреч" in R["week"], "неделя — все, и «не выдумывай»")
ok("Встреча с Игорем" in R["igor"] and "Стоматолог" not in R["igor"], f"поиск по слову «Игорь»: {R['igor']!r}")
ok(R["ask"].startswith("Ещё не записано") and "Записать в календарь" in R["ask"] and "15:30–16:00" in R["ask"] and R["askN"] == 0
   and "Встреча с Игорем" in R["ask"], f"без «да» ничего не записано, и сказано про встречу в то же время: {R['ask']!r}")
ok(R["yes"].startswith("Записано в календарь «Мой календарь»") and "напомнит за 15 минут" in R["yes"], f"после «да» записано: {R['yes']!r}")
ok(R["added"] == {"title": "Созвон с Аней", "h": 15, "m": 30, "dur": 30, "day": True, "allDay": False}, f"в нужный день и час, на 30 минут: {R['added']}")
ok("весь день" in R["allday"] and R["alldayE"] is True and "напомнит" not in R["allday"], f"только дата — на весь день: {R['allday']!r}")
ok(R["past"].startswith("Ошибка") and "прошло" in R["past"], "прошедшее время — ошибка, уточнить дату")
ok(R["bad"].startswith("Ошибка") and "ГГГГ-ММ-ДД" in R["bad"], "дата не в формате — ошибка с подсказкой")
ok("разрешите доступ" in R["noRead"] and "Не выдумывай" in R["noRead"], f"читать не разрешили — честно: {R['noRead']!r}")
ok("открыла окно новой встречи" in R["noWrite"] and R["insert"] and R["insert"]["title"] == "Обед", f"записать не разрешили — окно новой встречи: {R['noWrite']!r}")
ok("нет календаря для записи" in R["noCal"] and R["insert2"], "нет календаря для записи — окно новой встречи")
ok(R["claim"] and R["claimOk"] and R["claimOther"], "«записала в календарь» без записи — ловится; после записи и про списки — нет")
ok("Стоматолог" in R["exList"] and R["exReal"], "экзамен: читает и пишет песочницу, настоящий календарь не тронут")
ok(R["exCheck"][0]["ok"] and not R["exCheckBad"][0]["ok"], f"проверка экзамена: день и час встречи: {R['exCheck'][0]}")
ok(R["exLeft"] == ["Встреча с Игорем"], f"после сценария подложенные встречи убраны: {R['exLeft']}")
ok(R["scen"] == ["cal_tomorrow", "cal_add"], f"сценарии экзамена: {R['scen']}")
ok(R["specs"] == ["calendar_list", "calendar_add"] and R["prompt"], "инструменты и подсказка модели")
src = (ROOT / "android/assets/www/app.js").read_text(encoding="utf-8")
ok('if (c.name === "calendar_add" && /^Записано/.test(String(res))) called.add("calendar_write")' in src, "настоящая запись отмечается для проверки честности")
man = (ROOT / "android/AndroidManifest.xml").read_text(encoding="utf-8")
br = (ROOT / "android/src/pro/dewiar/app/Bridge.java").read_text(encoding="utf-8")
cal = (ROOT / "android/src/pro/dewiar/app/Cal.java").read_text(encoding="utf-8")
ok("READ_CALENDAR" in man and "WRITE_CALENDAR" in man and all(f"public String {m}(" in br or f"public boolean {m}(" in br for m in ("calEvents", "calAdd", "calOpenInsert")),
   "Android: разрешения и мост")
ok("Instances.CONTENT_URI" in cal and "CAL_ACCESS_CONTRIBUTOR" in cal and "Reminders.MINUTES, 15" in cal and "delete(" not in cal,
   "Cal.java: повторяющиеся встречи (Instances), только календари с правом записи, напоминание за 15 минут, ничего не удаляет")
ok(not errors, "без ошибок JavaScript" + (f": {errors}" if errors else ""))
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
