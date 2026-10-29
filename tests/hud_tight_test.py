"""Виджет на тесном экране (отзыв 11.10: карточка ответа, плашка «Ход работы» и карточка задачи наезжали друг на друга,
строка «…— готово» обрезалась посередине, а ответ сжимался в ноль). ui/hud.html в Chromium размером виджета, ядро —
заглушка:
— «Ход работы (3) и ответ» — строкой внутри карточки ответа, а не отдельной плашкой;
— тесно: сначала облик меньше; не хватает — вопрос в одну строку, «…— готово» прячется (о задаче говорит её карточка);
  ответ виден хотя бы двумя строками;
— ничего не обрезано посередине: содержимое карточки ответа помещается в неё; карточки не наезжают друг на друга и на панель;
— просторно — всё как раньше: вопрос в две строки и «…— готово» видно;
— готовая задача с кнопками и вопрос «Да/Нет» не уезжают на панель кнопок."""
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
window.api = () => null;
window.core = { _on: {}, on(k, f) { (this._on[k] = this._on[k] || []).push(f); return this; }, async call() { return {}; } };
""", encoding="utf-8")
SETUP = """() => { tlReset('На рабочем столе у меня есть файл «Неформальное общение». Это видео, сделай расшифровку');
  showCap('На рабочем столе у меня есть файл «Неформальное общение». Это видео, сделай расшифровку', '');
  for (const f of core._on.tool || []) f({ status: 'done', title: 'Фоновая задача' });
  TL.steps = [{}, {}, {}]; moreBtn();
  tasks.x = { id: 'x', status: 'running', title: 'Транскрипция видео «Неформальное общение»', progress: [2, 6], steps: 9, activity: 'расшифровываю запись…' };
  renderTask(); showCap(null, 'Запустила расшифровку в фоне — займёт несколько минут. Скажу, когда будет готово.'); }"""
GEOM = """() => { const r = (s) => document.querySelector(s).getBoundingClientRect(), cap = document.querySelector('#cap'), bot = document.querySelector('#bot');
  return { more_in_cap: !!document.querySelector('#cap > #more.on'), tight: cap.classList.contains('tight'), crowd: document.querySelector('#card').classList.contains('crowd'),
    you_lines: Math.round(document.querySelector('#you').getBoundingClientRect().height / 17.8), act: getComputedStyle(document.querySelector('#act')).display,
    fits: cap.scrollHeight <= cap.clientHeight + 1, bot_h: bot.clientHeight, cap_b: r('#cap').bottom, task_t: r('#task').top, task_b: r('#task').bottom, bar_t: r('#bar').top }; }"""
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    for h, tight in ((431, True), (600, False)):
        pg = b.new_page(viewport={"width": 331, "height": h}); pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto((tmp / "hud.html").as_uri()); pg.wait_for_timeout(500)
        pg.evaluate(SETUP); pg.wait_for_timeout(400)
        g = pg.evaluate(GEOM)
        ok(g["more_in_cap"], f"{h}: «Ход работы (3) и ответ» — внутри карточки ответа")
        ok((g["fits"] or g["bot_h"] == 0) and g["bot_h"] >= 17 and g["cap_b"] <= g["task_t"] and g["task_b"] <= g["bar_t"] + 1,
           f"{h}: ничего не обрезано, ответ виден, карточки не наезжают: {g}")
        if tight:
            ok(g["crowd"], f"{h}: тесно — облик меньше, карточки помещаются целиком: {g}")
        else:
            ok(not g["tight"] and not g["crowd"] and g["act"] != "none" and g["you_lines"] >= 2, f"{h}: просторно — как раньше: {g}")
        pg.close()
    # задача готова — карточка с кнопками «Посмотреть / HTML / PDF» не уезжает на панель; вопрос «Да/Нет» — тоже
    for h in (431, 470):
        pg = b.new_page(viewport={"width": 331, "height": h}); pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto((tmp / "hud.html").as_uri()); pg.wait_for_timeout(500)
        pg.evaluate("""() => { tasks.y = { id: 'y', status: 'done', title: 'Презентация по видео «Неформальное общение»',
            outputs: [{ path: 'outputs/slides.html', title: 'Неформальное общение — презентация' }] }; doneAt.y = Date.now(); renderTask(); }""")
        pg.wait_for_timeout(400)
        g = pg.evaluate("() => ({ task_b: document.querySelector('#task').getBoundingClientRect().bottom, bar_t: document.querySelector('#bar').getBoundingClientRect().top, btns: document.querySelectorAll('#task .tbtn button').length })")
        ok(g["btns"] == 3 and g["task_b"] <= g["bar_t"] + 1, f"{h}: готовая задача с кнопками — над панелью, не на ней: {g}")
        pg.evaluate("""() => { tasks.y.status = 'running'; tasks.y.progress = [1, 7]; tasks.y.activity = 'run_command'; renderTask();
            tlReset('Лира, а теперь на основании расшифровки сделай презентацию'); showCap('Лира, а теперь на основании расшифровки сделай презентацию', '');
            TL.steps = [{}, {}]; moreBtn(); for (const f of core._on.confirm || []) f({ text: 'Выполнить команду? Подробности — на экране.' }); }""")
        pg.wait_for_timeout(400)
        g = pg.evaluate(GEOM.replace("bar_t: r('#bar').top", "bar_t: r('#bar').top, yn_t: r('#yn').top, yn_b: r('#yn').bottom"))
        ok((g["fits"] or g["bot_h"] == 0) and g["cap_b"] <= g["yn_t"] and g["yn_b"] <= g["task_t"] and g["task_b"] <= g["bar_t"] + 1,
           f"{h}: «Да/Нет» с задачей — вопрос виден, ничего не наезжает: {g}")
        pg.close()
    b.close()
ok(not errs, f"без ошибок на странице: {errs[:3]}")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
