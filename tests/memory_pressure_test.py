"""Нехватка памяти прямо сейчас: компьютер мощный, но память заняли другие программы (браузер, Java, базы) — система
уходит в подкачку, окна открываются чёрными, голос запаздывает. Dewiar замечает это сам (две проверки подряд), один раз
объясняет, кто занимает память и что сделать, и показывает это в студии. Без настоящей нехватки: замер подменяется."""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


import device as D  # noqa: E402

ok(D._judge(10, 0.4, 92, None)["tight"], "свободно 0,4 ГБ из 10 и подкачка полна — тесно")
ok(D._judge(10, 0.4, None, None)["tight"], "свободно 0,4 ГБ, подкачки нет — тесно")
ok(not D._judge(10, 0.4, 20, None)["tight"], "свободно мало, но подкачка почти пуста — ещё не тесно")
ok(not D._judge(16, 6.0, 80, None)["tight"], "памяти много — не тесно, даже если подкачка занята")
ok(D._judge(16, 6.0, None, 25.0)["tight"], "программы ждут память (PSI 25 %) — тесно")
ok(not D._judge(0, 0, None, None)["tight"], "не узнать — не тревожим")
os.environ["DEWIAR_FAKE_PRESSURE"] = "0.4,10,92"
p = D.pressure()
ok(p["tight"] and p["free"] == 0.4 and p["swap"] == 92, f"замер: {p}")
t = D.pressure_text(p, [("Chrome", 2.1), ("Java", 1.8), ("Milvus", 1.2)])
ok("Свободно 0,4 ГБ памяти из 10" in t and "подкачка заполнена на 92%" in t and "Chrome (2,1 ГБ), Java (1,8 ГБ), Milvus (1,2 ГБ)" in t
   and "лёгкий режим" in t, f"совет: {t}")
ok("лёгкий режим" not in D.pressure_text(p, [], light=True), "лёгкий режим уже включён — его не советуем")
del os.environ["DEWIAR_FAKE_PRESSURE"]
real = D.pressure()
ok(real["total"] > 0 and "tight" in real, f"настоящий замер этого компьютера: {real}")

# кто занимает: сам Dewiar называется по имени, мелочь не показывается
hog = subprocess.Popen([sys.executable, "-c", "import time; x = bytearray(450 * 2**20); x[::4096] = b'1' * len(x[::4096]); time.sleep(20)",
                        "dewiar-memory-test"])
time.sleep(2.5)
# На рабочем Mac может быть больше восьми процессов крупнее тестового; здесь проверяется распознавание имени.
top = D.top_memory(1000)
hog.kill()
ok(any(k == "Dewiar" and v >= 0.4 for k, v in top), f"процесс Dewiar назван по имени: {top}")
ok(all(v >= 0.3 for _, v in top), "мелкие программы (меньше 0,3 ГБ) не перечисляются")
ok(D._nice("chrome.exe") == "Chrome" and D._nice("java") == "Java" and D._nice("Isolated Web Co") == "Firefox", "понятные названия программ")

# в ядре: две проверки подряд — одно уведомление; выключено — молчит
import engine as E  # noqa: E402


class S:
    def __init__(self, warn=True, auto=None):
        self.warn, self.dev = warn, {} if auto is None else {"auto_light": auto}

    def get(self, sec, key=None):
        if (sec, key) == ("device", "mem_warn"):
            return self.warn
        return self.dev.get(key) if sec == "device" else None

    def update(self, d):
        self.dev.update(d.get("device", {}))


def run(warn=True, fake="0.4,10,92", seconds=1.2, auto=None, then=None):
    os.environ.update(DEWIAR_FAKE_PRESSURE=fake, DEWIAR_MEM_WAIT="0.05", DEWIAR_MEM_EVERY="0.03" if then else "0.1")
    eng = E.Engine.__new__(E.Engine)
    eng.s = S(warn, auto)
    eng.stopping = threading.Event()
    eng.pushed, eng.lights, eng.statuses, eng.status = [], [], [], {}
    eng._push_quiet = lambda title, text: eng.pushed.append((title, text))
    eng.light = False
    eng.device_status = lambda: {"light": eng.light, "advice": ""}

    def set_light(on):
        eng.lights.append(on)
        eng.light = on
        return ["голос — лёгкий Piper"] if on else []
    eng.set_light = set_light
    eng.emit = lambda kind, **k: eng.statuses.append(dict(k.get("status") or {})) if kind == "status" else None
    if then:
        threading.Timer(seconds * 0.4, lambda: os.environ.update(DEWIAR_FAKE_PRESSURE=then)).start()
    th = threading.Thread(target=eng._memory_watch, daemon=True)
    th.start()
    time.sleep(seconds)
    eng.stopping.set()
    th.join(2)
    return eng


e = run()
ok(len(e.pushed) == 1 and e.pushed[0][0] == "Компьютеру не хватает памяти" and "Свободно 0,4 ГБ" in e.pushed[0][1],
   f"тесно — одно уведомление, не каждую минуту: {len(e.pushed)}")
ok(e.mem["tight"] and e.mem["streak"] >= 2 and e.mem.get("text"), "состояние для студии")
ok(e.lights == [True] and e.pushed[0][1].startswith("Перехожу в лёгкий режим: голос — лёгкий Piper")
   and e.s.dev.get("light_auto"), f"тесно — сама перешла в лёгкий режим и так и сказала: {e.lights} «{e.pushed[0][1][:70]}…»")
ok(any(x.get("tight") for x in e.statuses), "окнам сказано «мало памяти» — облики почти неподвижны")
e2 = run(auto=False)
ok(e2.lights == [] and len(e2.pushed) == 1 and "включите лёгкий режим" in e2.pushed[0][1],
   "лёгкий режим выключали сами — сам не включаю, только советую")
e3 = run(seconds=3.0, then="6,16,10")
ok(e3.lights == [True, False] and not e3.s.dev.get("light_auto") and e3.statuses and e3.statuses[-1].get("tight") is False,
   f"память освободилась надолго — лёгкий режим, включённый самой, выключаю: {e3.lights}")
ok(not run(warn=False).pushed, "предупреждение выключено — молчит")
ok(not run(fake="6,16,10").pushed, "памяти хватает — молчит")
# опасно (ещё чуть-чуть — и рабочий стол замрёт): разгружаемся с первой же проверки, не ждём вторую
ok(D._judge(10, 0.3, 95, None)["critical"] and D._judge(16, 5, 10, None, 45.0)["critical"]
   and not D._judge(16, 5, 10, None, 15.0)["critical"], "опасно: памяти почти нет или программы ждут её 40 % времени")
os.environ["DEWIAR_FAKE_PRESSURE"] = "0.3,10,95"
os.environ["DEWIAR_MEM_EVERY"] = "30"
e4 = E.Engine.__new__(E.Engine)
e4.s, e4.stopping, e4.pushed, e4.lights, e4.statuses, e4.status, e4.light = S(), threading.Event(), [], [], [], {}, False
e4._push_quiet = lambda title, text: e4.pushed.append((title, text))
e4.device_status = lambda: {"light": e4.light, "advice": ""}
e4.set_light = lambda on: (e4.lights.append(on), ["голос — лёгкий Piper"])[1]
e4.emit = lambda kind, **k: None
os.environ["DEWIAR_MEM_WAIT"] = "0.05"
th = threading.Thread(target=e4._memory_watch, daemon=True)
th.start()
time.sleep(1.0)
e4.stopping.set()
th.join(2)
ok(e4.lights == [True] and len(e4.pushed) == 1, f"опасно — лёгкий режим с первой проверки: {e4.lights}")
ok(isinstance(e4.mem.get("dewiar"), dict) and e4.mem["dewiar"]["core"] > 0, f"сколько занимает сам Dewiar: {e4.mem.get('dewiar')}")
for k in ("DEWIAR_FAKE_PRESSURE", "DEWIAR_MEM_WAIT", "DEWIAR_MEM_EVERY"):
    os.environ.pop(k, None)

# «Java 1,7 ГБ — это наш Dewiar?» (11.10) — нет: Dewiar на Python и так и называется; Java — по командной строке, какая именно
got = [D.java_name(c) for c in (b"/usr/lib/jvm/bin/java\0-cp\0/home/u/.gradle/wrapper/gradle-launcher-8.5.jar\0org.gradle.launcher.daemon.bootstrap.GradleDaemon\0",
                                b"/opt/idea-IC/jbr/bin/java\0com.intellij.idea.Main\0", b"java\0-jar\0/opt/x/server.jar\0", b"java\0Main\0")]
ok(got == ["Java (сборка Gradle)", "Java (IntelliJ IDEA)", "Java (server.jar)", "Java"], f"Java в совете — с именем программы: {got}")
print("\nИТОГ:", "всё хорошо" if not fails else f"ошибок {len(fails)}")
sys.exit(1 if fails else 0)
