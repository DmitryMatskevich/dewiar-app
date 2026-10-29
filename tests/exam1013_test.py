"""Экзамен 13.10 на планшете (телефон 0.6.20, 36 из 41):
— «Что я записывал про ремонт?» — вопрос, а не просьба записать: save_note не сохраняет найденную заметку второй раз;
— «Напиши Андрею» (его нет в «Близких») — SMS открыт, и модель знает, что сказать: Андрея нет в «Близких», можно пригласить;
— «посоветуй фильм», «плейлист» — конкретика только верная: правило разговора запрещает выдумывать названия и авторов."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
_d = tempfile.mkdtemp(); os.environ.update(DEWIAR_CONFIG_DIR=_d + "/cfg", DEWIAR_STATE_DIR=_d + "/st")
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


import personas as P  # noqa: E402
ok("лишь те, в которых уверен(а) полностью" in P.TALK_RULES and "Выдуманный фильм или чужая песня" in P.TALK_RULES, "правило: конкретика — только верная")
www = Path(tempfile.mkdtemp()) / "www"
shutil.copytree(ROOT / "android/assets/www", www)
for f in ("entities.js", "skins.css", "skins.js", "privacy.js"):
    shutil.copy(ROOT / "ui/assets" / f, www / f)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)], capture_output=True, text=True, check=True).stdout, encoding="utf-8")
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    pg = b.new_page(); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.route("https://**", lambda r: r.abort())
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(800)
    r = pg.evaluate("""async () => { const n0 = notes().length, out = {};
        for (const q of ["Что я записывал про ремонт?", "Запиши в заметки: купить краску", "Сохрани это заметкой", "Создай заметку про отпуск", "Допиши в заметку про ремонт: плитка"]) {
          turnAsk = q; out[q] = await runTool("save_note", { title: "Ремонт", text: "обои" }); }
        return out; }""")
    ok(r["Что я записывал про ремонт?"].startswith("Записывать не нужно"), f"вопрос «что я записывал» — не сохраняет: {r['Что я записывал про ремонт?'][:40]}")
    ok(all(v.startswith("Записано") for k, v in r.items() if k != "Что я записывал про ремонт?"), f"просьбы записать — сохраняют: {r}")
    h = pg.evaluate("""() => { const st = clLoad(); st.people = [{ pid: "p1", name: "Миша Исаев", devs: [] }]; clSave();
        return [notCloseHint("Андрей"), notCloseHint("Мише"), notCloseHint("+375291234567")]; }""")
    ok("«Андрей» нет в «Близких»" in h[0] and h[1] == "" and h[2] == "", f"SMS не близкому — подсказка сказать об этом; близкому и номеру — нет: {h}")
    # отчёт №50: вошёл логином и паролем, а на экране всё ещё «Подключить бесплатно»
    pg.evaluate("() => { localStorage.clear(); }"); pg.reload(); pg.wait_for_timeout(900)
    first = pg.evaluate("!!document.querySelector('#card .start')")
    pg.evaluate("""() => { S.account = { server: "https://dewiar.pro", login: "@superLeha", token: "t0k", user: {} };
        Object.assign(S.llm, { base_url: "https://dewiar.pro/dwrapp/cloud.php?p=", cloud: true, account: true }); saveS(); }""")
    pg.wait_for_timeout(200)
    g = pg.evaluate("[!!document.querySelector('#card .start'), document.querySelector('#bot').textContent]")
    ok(first and not g[0] and "Готово, всё подключено — аккаунт @superLeha" in g[1], f"вошли аккаунтом — «Подключить бесплатно» уходит, Лира говорит, что готова: {first} {g}")
    # отзыв 13.10: «открой Gmail и создай письмо» — «Открыла и создала», а ни приложения, ни письма
    c = pg.evaluate("""() => [claimedAction("Открыла Gmail и создала новое письмо.", new Set(), "открой gmail и создай письмо"),
        claimedAction("Открыла Gmail.", new Set(["open_app"]), "открой gmail"), claimedAction("Написала письмо маме.", new Set(["phone_type"]), "")].map(x => x && x.tool)""")
    ok(c[0] and "open_app" in c[0] and c[1] is None and c[2] is None, f"«открыла Gmail, создала письмо» без действий — заметно и требует сделать по-настоящему: {c}")
    ok("ждать ответа не умеешь" in pg.evaluate("systemPrompt()"), "«позвони, а если не ответит — напиши»: честно, что ждать ответа не умеет")
    ok(not errs, f"без ошибок: {errs[:2]}")
    b.close()
ex = (ROOT / "android/assets/www/exam.js").read_text(encoding="utf-8")
ok('notCloseHint(a.who)' in ex, "экзамен: заглушка SMS — с той же подсказкой")
ok('title: "«Напиши Андрею» — а его нет в «Близких»", seed: { close: ["Миша Исаев", "Людмила Исаева"] }' in ex,
   "экзамен: «Напиши Андрею» — свои «Близкие» сценария (у человека Андрей может правда быть в «Близких»)")
import tools as T  # noqa: E402
import workspace as W  # noqa: E402
class _WS:
    def remember(self, f): return "Это я уже знаю."
out = T.t_remember({"fact": "Бюджет — 65 тысяч"}, {"workspace": _WS()})
ok("ответь на него по памяти" in out and "не говори «запомнила»" in out, f"remember: уже известное — «ответь на вопрос», а не «запомнила»: {out[:60]}")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
