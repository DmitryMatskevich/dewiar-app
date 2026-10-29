"""Лира и Эва говорят о себе в женском роде: правило в подсказке модели; быстрые ответы без модели («Выключил музыку.»,
«Положил в сейф…», «Нажал «играть/пауза»») — в женском у героинь; третье лицо («станция не ответила») не трогаем.
Телефон (app.js) переводит так же, как компьютер, — проверяем на одном списке фраз."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
from gender import feminine, prompt_rule  # noqa: E402

CASES = {
    "Выключил музыку.": "Выключила музыку.",
    "Отменил, компьютер не выключаю.": "Отменила, компьютер не выключаю.",
    "Положил в сейф: «вайфай». Это видно только вам.": "Положила в сейф: «вайфай». Это видно только вам.",
    "Нажал «играть/пауза»": "Нажала «играть/пауза»",
    "Открыл в приложении Яндекс Музыки поиск «Элла».": "Открыла в приложении Яндекс Музыки поиск «Элла».",
    "Не нашёл такой заметки.": "Не нашла такой заметки.",
    "Я не понял вопрос.": "Я не поняла вопрос.",
    "Хорошо, включил.": "Хорошо, включила.",
    "Передал в задачу.": "Передала в задачу.",
    "Готов. Сделал ярче: свет (спальня).": "Готова. Сделала ярче: свет (спальня).",
    "Не смог открыть: нет плеера.": "Не смогла открыть: нет плеера.",
    "Запустил сценарий «Спокойной ночи».": "Запустила сценарий «Спокойной ночи».",
    "Записал: молоко, хлеб.": "Записала: молоко, хлеб.",
    "Остановился.": "Остановилась.",
    "Лира на связи.\nСейчас свободен.": "Лира на связи.\nСейчас свободна.",
    # Лира (отзыв 14.10): «Отлично, рад слышать.»
    "Отлично, рад слышать.": "Отлично, рада слышать.",
    "Я очень рад за тебя!": "Я очень рада за тебя!",
    "Прекрасно, я готов. Конечно, понял.": "Прекрасно, я готова. Конечно, поняла.",
    "Он так рад, а ты рад?": "Он так рад, а ты рад?",
    # ответы модели у Эвы (отчёт 06.10): «Рад слышать!», «я на месте и готов…»
    "Рад слышать! Значит, можно спокойно работать.": "Рада слышать! Значит, можно спокойно работать.",
    "У меня всё отлично, я на месте и готов к новым приключениям.": "У меня всё отлично, я на месте и готова к новым приключениям.",
    "Я посмотрел и понял.": "Я посмотрела и поняла.",
    # третье лицо и чужие действия — без изменений
    "Я видел его, и он рад.": "Я видел его, и он рад.",
    "Ты пришёл и рад?": "Ты пришёл и рад?",
    "Станция не ответила, сервер ответил 404.": "Станция не ответила, сервер ответил 404.",
    "Заряд закончился.": "Заряд закончился.",
    "Он включил свет, а я выключил.": "Он включил свет, а я выключила.",
    "«Рекорд» не ответила — играет «Медляк».": "«Рекорд» не ответила — играет «Медляк».",
    "Включаю «Рекорд».": "Включаю «Рекорд».",
    "Программа открыл-закрыл": "Программа открыл-закрыл",
}
bad = {k: feminine(k) for k, v in CASES.items() if feminine(k) != v}
ok(not bad, "мужской род ассистента → женский, третье лицо не трогаем" + (f": {bad}" if bad else ""))
ok("женского рода" in prompt_rule("female") and "«я сделала»" in prompt_rule("female") and "мужском" in prompt_rule("male"),
   "правило рода для модели")
from personas import Personas  # noqa: E402
ps = Personas()
sp = {pid: ps.system_prompt(pid) for pid in ("lira", "eva", "patrik", "dewiar")}
ok("женского рода" in sp["lira"] and "женского рода" in sp["eva"] and "женского" not in sp["patrik"] and "мужском роде" in sp["dewiar"],
   "в подсказке Лиры и Эвы — женский род, у Патрика и Девиара — мужской")
ok(sp["lira"].index("женского рода") < sp["lira"].index("Ты говоришь голосом"), "правило рода — сразу после характера, до правил речи")

# ядро: быстрые ответы через emit/_say_quick/announce — в роде персонажа
import engine as E  # noqa: E402
sent = []
e = E.Engine.__new__(E.Engine)
e.bridge = SimpleNamespace(broadcast=lambda d: sent.append(d))
e.personas = ps
cur = {"persona": "lira"}
e.s = SimpleNamespace(get=lambda *k, default=None: cur.get(k[0], default) if len(k) == 1 else default)
E.Engine.emit(e, "answer", text="Выключил музыку.", done=True)
E.Engine.emit(e, "answer", text="Я включил свет.", done=True, model=True)          # ответ модели — не трогаем
cur["persona"] = "patrik"
E.Engine.emit(e, "answer", text="Выключил музыку.", done=True)
ok([d["text"] for d in sent] == ["Выключила музыку.", "Я включил свет.", "Выключил музыку."] and all("model" not in d for d in sent),
   f"ядро: у Лиры быстрый ответ — «Выключила», ответ модели не трогаем, у Патрика — как было: {[d['text'] for d in sent]}")

# телефон: те же правила
from playwright.sync_api import sync_playwright  # noqa: E402
tmp = Path(tempfile.mkdtemp()); www = tmp / "www"
shutil.copytree(ROOT / "android/assets/www", www)
shutil.copy(ROOT / "ui/assets/entities.js", www / "entities.js")
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)   # живое лицо Эвы
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(); errs = []
    pg.on("pageerror", lambda x: errs.append(str(x)))
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(700)
    js = pg.evaluate("(xs) => xs.map(feminine)", list(CASES))
    S = pg.evaluate("""() => { const out = {}; for (const id of ["lira", "patrik"]) { S.persona = id; out[id] = systemPrompt(); } return out; }""")
    b.close()
diff = {k: j for k, j in zip(CASES, js) if j != CASES[k]}
ok(not diff, "телефон переводит так же, как компьютер" + (f": {diff}" if diff else ""))
ok("женского рода" in S["lira"] and "женского" not in S["patrik"], "телефон: правило рода в подсказке модели")
ok(not errs, "без ошибок JavaScript" + (f": {errs}" if errs else ""))
