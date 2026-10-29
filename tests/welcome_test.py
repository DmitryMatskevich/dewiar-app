"""«Сам замечает и сам начинает»: отошёл от компьютера и вернулся — ассистент коротко говорит главное (готовые задачи,
задача ждёт ответа, скоро встреча); первый раз за день после долгой отлучки — сводка дня. Сказать нечего, ночь, недавно уже
здоровались, выключено, микрофон выключен, созвон, спрятали виджет — молчит. Без модели и без экрана."""
import datetime as dt
import os
import sys
import threading
import types
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


import presence as P  # noqa: E402

# --- ушёл / вернулся ---
a = P.Away(away_after=1200)
ok(a.feed(5, 1000) is None and a.feed(600, 1600) is None, "отошёл ненадолго — не отлучка")
ok(a.feed(1300, 2300) is None and a.since == 1000, "простой дольше 20 минут — ушёл (с момента последнего касания)")
ok(a.feed(1500, 2500) is None, "всё ещё нет — молчим")
g = a.feed(2, 2600)
ok(g == 1600 and a.since is None, f"вернулся — один раз, отлучка {g} с")
ok(a.feed(1, 2605) is None, "второй раз «вернулся» не срабатывает")
b = P.Away(away_after=1200)
b.feed(3, 1000)
ok(b.feed(1, 1000 + 5 * 3600) == 5 * 3600, "компьютер спал 5 часов — это тоже отлучка")
c = P.Away(away_after=1200)
c.feed(None, 1000)
ok(c.feed(None, 1005) is None and c.feed(None, 1005 + 7200) == 7200, "простой не узнать — после сна всё равно здороваемся")

# --- что сказать ---
now = dt.datetime(2026, 10, 6, 14, 0)
since = now - dt.timedelta(minutes=50)
tasks = [{"title": "Обзор новостей ИИ", "status": "done", "updated": (now - dt.timedelta(minutes=10)).isoformat(timespec="seconds")},
         {"title": "Старая задача", "status": "done", "updated": (now - dt.timedelta(days=1)).isoformat(timespec="seconds")},
         {"title": "План поездки", "status": "waiting", "updated": ""}]
p = P.plan(3000, since, tasks, [("14:30", "Созвон с Андреем")], now - dt.timedelta(hours=3), now=now)
ok(p and p["kind"] == "back" and "Обзор новостей ИИ" in " ".join(p["facts"]) and "Старая задача" not in " ".join(p["facts"])
   and "План поездки" in " ".join(p["facts"]) and "14:30" in " ".join(p["facts"]) and p["gone"] == "50 мин", f"вернулся — главное: {p}")
ok(P.plan(3000, since, [], [], now - dt.timedelta(hours=3), now=now) is None, "сказать нечего — молчим")
ok(P.plan(3000, since, tasks, [], now - dt.timedelta(minutes=20), now=now) is None, "недавно здоровались — не чаще раза в 40 минут")
ok(P.plan(3000, since, tasks, [], None, now=dt.datetime(2026, 10, 6, 23, 30)) is None, "ночью не заговаривает")
d = P.plan(9 * 3600, now - dt.timedelta(hours=9), [], [], now - dt.timedelta(days=1), now=dt.datetime(2026, 10, 6, 9, 0))
ok(d and d["kind"] == "day" and d["gone"] == "9 ч", f"утром, первый раз за день — сводка дня даже без новостей: {d}")
txt = P.prompt(d, "Сегодня 06.10.2026, вторник.")
ok("первый раз за день" in txt and "вторник" in txt and "без инструментов" in txt, "поручение модели — сводка дня")
t2 = P.prompt(p)
ok("вернулся" in t2 and "1–2" in t2 and "Обзор новостей ИИ" in t2, "поручение модели — коротко о главном")

# --- в ядре: когда молчим ---
import engine as E  # noqa: E402


class S:
    def __init__(self, d):
        self.d = d

    def get(self, sec, key=None):
        v = self.d.get(sec, {})
        return v.get(key) if key else v

    def active_llm(self):
        return {"kind": "openai"}


def make(**kw):
    eng = E.Engine.__new__(E.Engine)
    eng.s = S({"welcome": {"on": kw.get("on", True), "away_min": 20}})
    eng.muted = kw.get("muted", False); eng.meeting = kw.get("meeting", []); eng.alarm = None
    eng.turn = None; eng.state = "idle"; eng.last_turn_time = 0.0
    eng.stopping = threading.Event()
    eng.tasks = types.SimpleNamespace(list=lambda: tasks)
    eng._upcoming = lambda: []
    eng._brief_context = lambda: "Сегодня…"
    eng.asked = []
    eng.ask = lambda text, **k: eng.asked.append((text, k))
    return eng


real_now = dt.datetime.now()
day = 7 <= real_now.hour < 23                            # ночью plan молчит — проверку «заговорила» делаем днём
e = make()
r = e._welcome_back(3000, wait=0)
if day:
    ok(r and e.asked and e.asked[0][1].get("shown") == "👋 С возвращением" and e.asked[0][1].get("raw"), "вернулись — заговорила сама")
    ok(not e._welcome_back(3000, wait=0), "сразу второй раз — нет")
for name, kw in (("выключено в настройках", {"on": False}), ("микрофон выключен", {"muted": True}), ("созвон", {"meeting": ["zoom"]})):
    e = make(**kw)
    ok(not e._welcome_back(3000, wait=0) and not e.asked, f"молчит: {name}")
e = make()
e._tray_at = 100.0
e.last_turn_time = 50.0
ok(not e._welcome_back(3000, wait=0) and not e.asked, "спрятали виджет и с тех пор не звали — молчит")
e = make()
e.turn = object()
ok(not e._welcome_back(3000, wait=0), "идёт разговор — не перебивает")

print("\nИТОГ:", "всё хорошо" if not fails else f"ошибок {len(fails)}")
sys.exit(1 if fails else 0)
