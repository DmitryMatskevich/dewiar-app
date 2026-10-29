"""Пакет здоровья 20.10.
№137: «По будням с 9:00 по 18:00 проверяй почту и сообщай о важных письмах» — Лира поставила человеку напоминание «проверь
почту» каждый час. Теперь: окно «с 9 до 18» в повторе (when), «периодически / ежечасно / время от времени» — каждый час,
условие «если придёт важное» остаётся в поручении (jobs), у модели — инструмент schedule_job (дело, которое делает она сама),
а set_reminder говорит, что напоминание — человеку.
№131: «время говорит на 3 часа позднее» — на «сколько времени в Омске» модель вычла 3 часа вместо прибавления. Теперь — по
часовому поясу: быстрый ответ без модели и инструмент world_time (компьютер и телефон)."""
import os, re, subprocess, sys, tempfile, types
from datetime import datetime
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
tmp = Path(tempfile.mkdtemp())
os.environ["DEWIAR_STATE_DIR"] = str(tmp / "st")
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


import when as W  # noqa: E402
import jobs as J  # noqa: E402
import tools as T  # noqa: E402

# ── окно в повторе ──
now = datetime(2026, 10, 20, 10, 38)                  # вторник
r = W.parse("напоминай по будням с 9:00 по 18:00 проверяй почту", now)
ok(r and r["repeat"] == {"kind": "interval", "minutes": 60, "from": "09:00", "to": "18:00", "days": [0, 1, 2, 3, 4]} and r["at"] == datetime(2026, 10, 20, 11, 38)
   and r["text"] == "Проверяй почту", f"«по будням с 9 по 18» — каждый час в окне, первый раз через час: {r}")
ok(W.describe_repeat(r["repeat"]) == "каждый час по будням с 9:00 до 18:00", f"словами: {W.describe_repeat(r['repeat'])}")
ok(W.next_time(r["repeat"], datetime(2026, 10, 23, 17, 30)) == datetime(2026, 10, 26, 9, 0), "пятница 17:30 → понедельник 9:00 (не ночью и не в выходные)")
ok(W.next_time(r["repeat"], datetime(2026, 10, 21, 7, 0)) == datetime(2026, 10, 21, 9, 0), "до начала окна — в 9:00")
r2 = W.parse("напоминай каждые 2 часа с 10 до 20 пить воду", now)
ok(r2 and r2["repeat"]["minutes"] == 120 and r2["repeat"]["from"] == "10:00" and "days" not in r2["repeat"], f"«каждые 2 часа с 10 до 20»: {r2 and r2['repeat']}")
ok(W.parse("напомни завтра в 9 позвонить маме", now)["repeat"] is None, "обычное напоминание — как раньше")

# ── дела: «периодически», условие ──
j = J.parse("Периодически проверяй почту и сообщай мне, если придёт что-то важное")
ok(j and j["repeat"] == {"kind": "interval", "minutes": 60} and "если придет что-то важное" in j["task"].lower(),
   f"«периодически» — каждый час; «если придёт важное» — в поручении: {j}")
j2 = J.parse("По будням с 9:00 по 18:00 проверяй почту и сообщай о важных письмах")
ok(j2 and j2["repeat"].get("from") == "09:00" and j2["repeat"].get("days") == [0, 1, 2, 3, 4], f"«Дела» в студии: окно и будни: {j2 and j2['repeat']}")
ok(J.parse("ежечасно проверяй курс доллара")["repeat"]["minutes"] == 60, "«ежечасно» — каждый час")

# ── инструмент schedule_job ──
fired = []
rem = T.Reminders(fired.append)
out = T.t_schedule_job({"task": "проверить почту и сообщить о важных письмах", "when": "по будням с 9 до 18 каждый час"}, {"reminders": rem})
it = rem.items[-1]
ok(it["kind"] == "job" and it["repeat"].get("from") == "09:00" and "сам" in out and "голосом" in out, f"schedule_job — дело, которое делает она сама: {out}")
try:
    T.t_schedule_job({"task": "проверить почту", "when": "завтра в 9"}, {"reminders": rem}); bad = False
except T.ToolError as e:
    bad = "повтор" in str(e)
ok(bad, "разовое время — не дело по расписанию: понятная ошибка")
d = T.TOOLS["set_reminder"][0]
ok("ЧЕЛОВЕКУ" in d and "schedule_job" in d and "schedule_job" in T.TOOLS and "проверяй" in T.TOOLS["schedule_job"][0],
   "описания: напоминание — человеку, «проверяй / сообщай» — schedule_job")

# ── время в городе ──
from zoneinfo import ZoneInfo  # noqa: E402
n0 = datetime(2026, 10, 20, 9, 39, tzinfo=ZoneInfo("Europe/Moscow"))
ok(T.world_time("Омске", n0, geocode=False, prep="в") == "В Омске сейчас 12:39 — на 3 часа больше, чем у вас.", f"Омск: {T.world_time('Омске', n0, geocode=False, prep='в')}")
ok(T.world_time("Владивостоке", n0, geocode=False, prep="во").startswith("Во Владивостоке сейчас 16:39 — на 7 часов больше"), "Владивосток: +7")
ok("меньше" in T.world_time("Калининград", n0, geocode=False) and "08:39" in T.world_time("Калининград", n0, geocode=False), "Калининград: на час меньше")
ok("там ещё вчера" in T.world_time("Нью-Йорк", datetime(2026, 10, 20, 2, 0, tzinfo=ZoneInfo("Europe/Moscow")), geocode=False), "Нью-Йорк ночью: «там ещё вчера»")
import engine_tools as ET  # noqa: E402
said = []
me = types.SimpleNamespace(_say_quick=lambda s, t, v: said.append(s))
ok(ET.ToolsMixin._clock_cmd(me, "Лира, сколько сейчас времени в Омске?") and said[-1].startswith("В Омске сейчас") and re.search(r"на \d+ час\w* больше, чем у вас|так же, как у вас|чем у вас", said[-1]) is not None,   # разница — от пояса этой машины (на GitHub — UTC)
   f"компьютер: «сколько времени в Омске» — сразу, по поясу: {said[-1:]}")
ok("world_time" in T.TOOLS, "у модели — world_time")

# ── телефон ──
www = tmp / "www"
subprocess.run(["bash", str(ROOT / "android/tools/www_assemble.sh"), str(www)], check=True, capture_output=True)
from playwright.sync_api import sync_playwright  # noqa: E402
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 400, "height": 820}); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(1000)
    r = pg.evaluate("""async () => { const n = new Date(Date.UTC(2026, 9, 20, 6, 39));
        return [await worldTime("Омске", "в", n), await worldTime("Владивостоке", "во", n), (await quick("Сколько сейчас времени в Омске?")) || {}]; }""")
    off = pg.evaluate("-new Date().getTimezoneOffset() / 60")
    ok(r[0].startswith("В Омске сейчас 12:39") and r[1].startswith("Во Владивостоке сейчас 16:39"), f"телефон: время в городе по поясу (UTC{off:+g}): {r[:2]}")
    ok("В Омске сейчас" in (r[2].get("say") or ""), f"телефон: быстрый ответ без модели: {r[2]}")
    # №130: синонимы камеры
    cam = pg.evaluate("""() => ["сними меня", "сделай фотографию", "расскажи, что перед тобой", "опиши, что вокруг", "что у меня в руке", "оцени мой наряд",
        "как дела", "напомни купить хлеб"].map(t => [PHOTO_NOW_RX.test(" " + t), PHOTO_ASK_RX.test(t), CAM_ASK_RX.test(t)])""")
    ok(cam[0][0] and cam[1][0] and all(c[1] and c[2] for c in cam[2:6]) and not any(any(c) for c in cam[6:]),
       f"синонимы камеры: «сними меня», «расскажи, что перед тобой», «что у меня в руке» — снимок; «как дела» — нет: {cam}")
    # сбой связи — один на вопрос: повторы внутри вопроса в пульс не идут
    nf = pg.evaluate("""() => { const h0 = hLoad().net_fail || 0; netIncident({url: "https://dewiar.pro/x", raw: "timeout"}, false);
        netIncident({url: "https://dewiar.pro/x", raw: "timeout"}, false); const h1 = hLoad().net_fail || 0;
        netIncident({url: "https://dewiar.pro/x", raw: "timeout"}); return [h1 - h0, (hLoad().net_fail || 0) - h0, JSON.parse(prefGet("net_inc")).length]; }""")
    ok(nf[0] == 0 and nf[1] == 1 and nf[2] >= 3, f"сбой связи — один на вопрос (повторы — только в журнал для отчёта): {nf}")
    src = (ROOT / "android/assets/www/app.js").read_text(encoding="utf-8")
    ok('name: "world_time"' in src and 'case "world_time"' in src, "телефон: у модели — world_time")
    ok(not errs, f"без ошибок JavaScript: {errs[:2]}")
    b.close()
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
