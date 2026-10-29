"""Пакет здоровья 22.10: «Сбой аудиопотока — PortAudioError: Error opening RawInputStream: … 'A device ID has been used
that is out of range for your system.' [MME error 2]» (и MME 1 «Undefined external error», -9986) — 5 отчётов; №165
«микрофон подключён после появления Лиры — не распознаёт». PortAudio читает список устройств один раз: номер
отключённого микрофона устаревает, новый не виден. Теперь (core/audio_io.py): не открылся — список перечитывается
(sd_refresh) и та же попытка снова; выбранный так и не открылся — микрофон по умолчанию; список в студии — свежий;
пока открыт хоть один поток, список не трогаем (Pa_Terminate оборвал бы его). Ядро (engine_audio.NO_MIC_ERR) считает
эти ошибки «микрофона нет» — ждёт и переподключается, без отчёта о сбое."""
import os, sys, types
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


# поддельный sounddevice: список устройств «как в Windows», первый RawInputStream с устаревшим номером падает
class PortAudioError(Exception):
    pass


state = {"devs": [{"name": "Микрофон (USB)", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0},
                  {"name": "Динамики", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2}],
         "refresh": 0, "opened": [], "fail_idx": set()}


class Stream:
    def __init__(self, device=None, **k):
        if device in state["fail_idx"]:
            raise PortAudioError("Error opening RawInputStream: Unanticipated host error [PaErrorCode -9999]: "
                                 "'A device ID has been used that is out of range for your system.' [MME error 2]")
        state["opened"].append(device)
    def start(self): pass
    def stop(self): pass
    def close(self): pass
    def abort(self): pass


sd = types.ModuleType("sounddevice")
sd.PortAudioError = PortAudioError
sd.RawInputStream = Stream
sd.RawOutputStream = Stream
sd.query_devices = lambda: list(state["devs"])
sd.default = types.SimpleNamespace(device=[0, 1])
sd._terminate = lambda: None
sd._initialize = lambda: state.__setitem__("refresh", state["refresh"] + 1)
sys.modules["sounddevice"] = sd
os.environ["DEWIAR_AUDIO_BACKEND"] = "sounddevice"
import audio_io as A  # noqa: E402

# 1) номер устарел: после перечитывания — тот же микрофон
state["fail_idx"] = {0}
c = A.Capture(device="Микрофон (USB)")
orig_init = sd._initialize
sd._initialize = lambda: (orig_init(), state["fail_idx"].clear())
got = c.open()
ok(state["refresh"] == 1 and state["opened"][-1] == 0 and got == "Микрофон (USB)", f"не открылся — перечитали список и открыли тот же: {got}, {state}")
ok(A._SD_OPEN[0] == 1, "поток открыт — счётчик 1")
ok(A.sd_refresh(force=True) is False, "пока поток открыт, список не перечитываем (оборвали бы его)")
c.close()
ok(A._SD_OPEN[0] == 0, "закрыли — счётчик 0")

# 2) выбранный так и не открылся — микрофон по умолчанию
sd._initialize = orig_init
state["fail_idx"] = {0}
A._SD_AT[0] = 0
c = A.Capture(device="Микрофон (USB)")
got = c.open()
ok(state["opened"][-1] is None and "по умолчанию" in got, f"выбранный недоступен — микрофон по умолчанию: {got}")
c.close()

# 3) подключили новый микрофон после запуска — студия видит его
state["devs"].append({"name": "Микрофон камеры", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0})
A._SD_AT[0] = 0
A.WIN = True                                         # список «как в Windows» (на Linux — PulseAudio)
names = [d["name"] for d in A.list_devices() if d["kind"] == "source"]
ok("Микрофон камеры" in names and state["refresh"] >= 2, f"список устройств — свежий: {names}")

# 4) голос: поток вывода тоже считается
p = A.Player(device="")
p.start(24000)
ok(A._SD_OPEN[0] == 1, "голос играет — список не трогаем")
p._kill()
ok(A._SD_OPEN[0] == 0, "голос закончил — можно перечитывать")

# 5) ядро: такие ошибки — «микрофона нет», а не сбой с отчётом
import engine_audio as EA  # noqa: E402
msgs = ["Error opening RawInputStream: Unanticipated host error [PaErrorCode -9999]: 'A device ID has been used that is out of range for your system.' [MME error 2]",
        "Error opening RawInputStream: Unanticipated host error [PaErrorCode -9999]: 'Undefined external error.' [MME error 1]",
        "Error starting stream: Internal PortAudio error [PaErrorCode -9986]"]
ok(all(any(s in m for s in EA.NO_MIC_ERR) for m in msgs), "ядро: эти ошибки — «микрофона нет»: ждать и переподключаться, без отчёта")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
