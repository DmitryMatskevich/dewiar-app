"""Пакет здоровья 22.10, №188 (Windows 10, 2 ядра): «нет связи с ядром». После обновления 0.38.95 → 0.38.99 ядро
не поднялось, 14 минут сторож ничего не делал, а потом показал «Ядро не запустилось: 08:39:17 INFO dewiar.skills:
Навыки: … explain-error …» — строку из прошлого запуска (слово error — в имени навыка). Помогла кнопка «Перезапустить».
Теперь (ui/app.py core_watch):
- ядра нет на связи, а его процесс завершился (или его нет вовсе) — запускаем ещё раз (до двух раз), потом объясняем;
- процесс жив, но за 150 с ни строки в журнале (завис при запуске) — тоже ещё раз;
- причина — только из строк этого запуска и core.fail этого запуска; «error» в имени навыка — не ошибка;
- ui.pid пишется до ожидания ядра (сторож обновления не запускает второй интерфейс);
- core/main.py: ядро, которое после «Останавливаюсь…» держит не-фоновый поток, через 5 с завершается само."""
import json, os, re, sys, tempfile, time, types
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


app = (ROOT / "ui/app.py").read_text(encoding="utf-8")
src = app[app.index("CORE_PROC = []"):app.index("    print(f\"Ядро не запустилось:")]
tmp = Path(tempfile.mkdtemp())
logs = tmp / "logs"; logs.mkdir()
OLD = ("08:39:16 INFO    dewiar: Dewiar 0.38.95 — запуск ядра\n"
       "08:39:17 INFO    dewiar.skills: Навыки: archive, code-review, debugging, explain-error, git\n"
       "08:40:29 INFO    dewiar: Останавливаюсь…\n")
(logs / "core.log").write_text(OLD, encoding="utf-8")
(tmp / "core.fail").write_text(json.dumps({"time": 1000, "error": "ImportError: старая ошибка"}), encoding="utf-8")


class Proc:
    def __init__(self, behave):
        self.behave, self.t0, self.killed = behave, time.monotonic(), False
    def poll(self):
        if self.killed or (self.behave == "die" and time.monotonic() - self.t0 > 0.05):
            return 1
        return None
    def kill(self):
        self.killed = True


world = {"alive": False, "plan": [], "started": 0, "clock": 0.0}


def start_core(root):
    world["started"] += 1
    b = world["plan"].pop(0) if world["plan"] else "die"
    if b == "ok":
        world["alive"] = True
        with open(logs / "core.log", "a", encoding="utf-8") as f:
            f.write("08:54:33 INFO    dewiar: Dewiar 0.39.1 — запуск ядра\n")
    if b == "crash":
        with open(logs / "core.log", "a", encoding="utf-8") as f:
            f.write("09:00:01 ERROR   dewiar: Ядро не запустилось\nTraceback (most recent call last):\n"
                    "OSError: [WinError 1114] Ошибка в подпрограмме инициализации DLL\n")
    return Proc("die" if b in ("die", "crash") else "hang" if b == "hang" else "ok")


ws = types.SimpleNamespace(log_dir=lambda: logs, start_core=start_core)
# быстрые часы: time.sleep двигает «время» вперёд, чтобы проверить 150 с и 240 с без ожидания
fake_time = types.SimpleNamespace(monotonic=lambda: world["clock"], time=time.time,
                                  sleep=lambda s: world.__setitem__("clock", world["clock"] + s))
ns = {"json": json, "re": re, "sys": sys, "time": fake_time, "winshell": ws, "ROOT": ROOT,
      "core_alive": lambda: world["alive"]}
exec(src + "\n    return error\n", ns)
settings = types.ModuleType("settings"); settings.STATE_DIR = tmp; sys.modules["settings"] = settings


# Proc.poll сравнивает с настоящими часами — завершение «сразу» должно читаться и по быстрым
class P2(Proc):
    def poll(self):
        if self.killed or self.behave == "die":
            return 1
        return None


ns["Proc"] = P2
_sc = start_core
ws.start_core = lambda r: P2(_sc(r).behave)


# 1) №188: ядро после обновления не поднялось (процесс завершился без строки) — сторож запустил ещё раз, и оно встало
world["plan"] = ["die"]
ns["start_core_win"]()
world["plan"] = ["ok"]; world["started"] = 0; world["clock"] = 0.0
r = ns["core_watch"]()
ok(r is True and world["started"] == 1, f"процесс ядра завершился — сторож сам запустил ещё раз, ядро на связи: {r}, запусков {world['started']}")

# 2) ядра не было вовсе (наш процесс не запускался: на порту отвечало старое ядро, потом ушло) — тоже запускаем
ns["CORE_PROC"][:] = []
world.update(alive=False, plan=["ok"], started=0, clock=0.0)
r = ns["core_watch"]()
ok(r is True and world["started"] == 1, "своего процесса ядра нет, на связи никого — запускаем, а не ждём 4 минуты")

# 3) завис при запуске: процесс жив, за 150 с ни строки — убить и ещё раз
world.update(alive=False, plan=["hang"], started=0, clock=0.0)
ns["start_core_win"]()
hung = ns["CORE_PROC"][0]
world.update(plan=["ok"], started=0, clock=0.0)
r = ns["core_watch"]()
ok(r is True and hung.killed and world["started"] == 1, f"ядро молчит 150 с — убрали зависшее и запустили снова: {r}, убит {hung.killed}")

# 4) не встаёт никак: две попытки, затем причина — из строк этого запуска, не из прошлого
world.update(alive=False, plan=["die"], started=0, clock=0.0)
ns["start_core_win"]()
world.update(plan=["die", "die"], started=0, clock=0.0)
err = ns["core_watch"]()
ok(world["started"] == 2, f"не встаёт — ещё две попытки, не больше: {world['started']}")
ok("explain-error" not in err and "старая ошибка" not in err, f"причина — не строка навыков прошлого запуска и не старый core.fail: {err!r}")
ok(ns["CORE_ERR_RX"].search("08:39:17 INFO    dewiar.skills: Навыки: explain-error, git") is None,
   "«explain-error» в списке навыков — не ошибка")

# 5) упало с ошибкой — она и показывается
world.update(alive=False, plan=["crash"], started=0, clock=0.0)
ns["start_core_win"]()
world.update(plan=["crash", "crash"], started=0, clock=0.0)
err = ns["core_watch"]()
ok("WinError 1114" in err, f"ошибка этого запуска — в объяснении: {err!r}")
(tmp / "core.fail").write_text(json.dumps({"time": time.time(), "error": "PermissionError: свежая"}), encoding="utf-8")
err = ns["core_fail_reason"](ns["core_log_new"](), tmp / "core.fail")
ok(err == "PermissionError: свежая", f"core.fail этого запуска — главнее журнала: {err!r}")

# 6) порядок запуска и «зависшее» ядро
mw = app[app.index("def main_win("):]
ok(mw.index('"ui.pid").write_text') < mw.index("ensure_core()"), "ui.pid пишется до ожидания ядра — сторож обновления не запустит второй интерфейс")
main = (ROOT / "core/main.py").read_text(encoding="utf-8")
tail = main[main.index("engine.shutdown()"):]
ok("os._exit(0)" in tail and "Timer(5" in tail, "ядро, которое держит не-фоновый поток после «Останавливаюсь…», через 5 с завершается само")
ok(app.count("winshell.start_core(ROOT)") == 1, "ядро на Windows запускается только через start_core_win (метка журнала)")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
