"""Мощность этого компьютера: хватит ли её на распознавание речи и голос «у себя».

Думает модель на сервере (облако Dewiar или свой сервер), а на компьютере — слух (GigaAM ≈ 0,5–0,8 ГБ памяти),
голос (Vosk TTS ≈ 0,3–0,6 ГБ) и окна. На слабом компьютере (мало памяти или 2 ядра) включаем «лёгкий режим»:
распознавание — на сервере Dewiar (если есть вход или ключ) или лёгкой моделью Vosk, голос — лёгкий Piper.
Решение и его применение — у вызывающего кода; здесь только замер (measure) и план (light_plan). Чтобы не включать
режим снова после того, как его выключили, запоминается sig — «железо», для которого уже решали (settings device.sig).
Для проверки: DEWIAR_FAKE_RAM_GB, DEWIAR_FAKE_CORES.
"""

import os
import re
import subprocess
import time
from pathlib import Path
from platform_info import MAC

WEAK_RAM_GB = 7.0        # «8 ГБ» Windows показывает как 7,8 — это уже нормально; 6 ГБ и меньше — тяжело
VERY_WEAK_RAM_GB = 3.6   # 4 ГБ и меньше — даже лёгкий режим впритык
WEAK_CORES = 2


def ram_gb():
    """(всего, свободно) в ГБ или (0, 0), если узнать нельзя (на macOS «свободно» — 0)."""
    fake = os.environ.get("DEWIAR_FAKE_RAM_GB")
    if fake:
        return float(fake), float(fake) / 2
    if MAC:
        try:
            import psutil
            mem = psutil.virtual_memory()
            return mem.total / 2**30, mem.available / 2**30
        except (ImportError, OSError):
            pass
    if os.name == "nt":
        try:
            import ctypes

            class M(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
            m = M()
            m.len = ctypes.sizeof(M)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return m.total / 2**30, m.avail / 2**30
        except Exception:  # noqa: BLE001
            pass
        return 0.0, 0.0
    try:
        mem = dict(re.findall(r"(\w+):\s+(\d+)", Path("/proc/meminfo").read_text()))
        return int(mem["MemTotal"]) / 1048576, int(mem.get("MemAvailable", 0)) / 1048576
    except (OSError, KeyError, ValueError):
        pass
    try:                                              # macOS
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3).stdout
        return int(out.strip()) / 2**30, 0.0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0, 0.0


def cores():
    """Логических ядер (с учётом гиперпоточности); 0 — неизвестно."""
    fake = os.environ.get("DEWIAR_FAKE_CORES")
    if fake:
        return int(fake)
    return os.cpu_count() or 0


def measure():
    """{"ram", "free", "cores", "level": ok|weak|very_weak, "why": "…", "sig"} — sig меняется, если поменяли «железо».
    weak — меньше WEAK_RAM_GB памяти или не больше 2 ядер; very_weak — к тому же памяти меньше VERY_WEAK_RAM_GB.
    Неизвестные значения (0) слабостью не считаются."""
    total, free = ram_gb()
    n = cores()
    why = []
    if total and total < WEAK_RAM_GB:
        why.append(f"{total:.0f} ГБ памяти" if total >= 1 else f"{total * 1024:.0f} МБ памяти")
    if n and n <= WEAK_CORES:
        why.append(f"{n} {'ядро' if n == 1 else 'ядра'} процессора")
    level = "ok"
    if why:
        level = "very_weak" if total and total < VERY_WEAK_RAM_GB else "weak"
    return {"ram": round(total, 1), "free": round(free, 1), "cores": n, "level": level, "why": ", ".join(why),
            "sig": f"{round(total)}/{n}"}


def light_plan(stt, tts, cloud_stt=False, account_stt=""):
    """Что поменять для лёгкого режима -> (план, [что сделано словами]). План — не патч настроек, а выбор:
    {"stt": "cloud" (ключ Облака) | "account" (вход в аккаунт) | "vosk", "tts": "piper"}; применяет вызывающий код.
    Трогаем только то, что стоит «по умолчанию» (GigaAM, Vosk TTS): свой сервер распознавания или голоса — не трогаем."""
    patch, done = {}, []
    if (stt or {}).get("engine") == "gigaam":
        if cloud_stt:
            patch["stt"] = "cloud"
            done.append("речь распознаёт сервер Dewiar")
        elif account_stt:
            patch["stt"] = "account"
            done.append("речь распознаёт сервер Dewiar")
        else:
            patch["stt"] = "vosk"
            done.append("распознавание — лёгкой моделью (чуть менее точно)")
    if (tts or {}).get("engine") == "vosk":
        patch["tts"] = "piper"
        done.append("голос — лёгкий Piper")
    return patch, done


# ---------- нехватка памяти прямо сейчас (не «железо», а что открыто) ----------
# Компьютер может быть и мощным, но память занята другими программами (браузер, Java, базы данных): тогда система
# уходит в подкачку, окна Dewiar открываются чёрными и долго, голос запаздывает. pressure() — замер, top_memory() —
# кто больше всех занимает, pressure_text() — понятный совет. Для проверки: DEWIAR_FAKE_PRESSURE="свободно,всего,подкачка%".

NAMES = {"chrome": "Chrome", "chromium": "Chromium", "chromium-browser": "Chromium", "msedge": "Edge", "firefox": "Firefox",
         "isolated web co": "Firefox", "web content": "Firefox", "java": "Java", "javaw": "Java", "milvus": "Milvus", "code": "VS Code",
         "telegram": "Telegram", "telegram-deskto": "Telegram", "slack": "Slack", "discord": "Discord", "zoom": "Zoom",
         "teams": "Teams", "ms-teams": "Teams", "docker": "Docker", "dockerd": "Docker", "containerd": "Docker",
         "postgres": "PostgreSQL", "mysqld": "MySQL", "ollama": "Ollama", "steam": "Steam", "obs": "OBS",
         "gnome-shell": "рабочий стол GNOME", "plasmashell": "рабочий стол KDE", "explorer": "Проводник"}

# Processes that must never be offered to the user for termination.  The memory
# recovery sheet is deliberately conservative: it only shows processes owned by
# the current user and never closes Dewiar or core macOS services.
_MEMORY_PROTECTED = {
    "launchd", "kernel_task", "WindowServer", "loginwindow", "Finder", "Dock", "SystemUIServer",
    "cfprefsd", "usernoted", "notifyd", "runningboardd", "coreservicesd", "distnoted", "opendirectoryd",
    "Dewiar", "python", "python3", "strata", "strata-desktop", "node", "coreaudiod",
}


def _memory_is_protected(proc, name, label):
    """Exclude system-owned helpers even when macOS gives them a user UID."""
    protected = {x.lower() for x in _MEMORY_PROTECTED}
    if str(name).lower() in protected or str(label).lower() in protected or str(label).lower() == "dewiar":
        return True
    # Virtualization.framework and other Apple XPC helpers can run under the
    # logged-in user.  They are services, not applications a user should kill
    # from a recovery sheet.
    if name.startswith("com.apple."):
        return True
    try:
        command = " ".join(proc.cmdline())
    except Exception:  # noqa: BLE001
        command = ""
    return any(command.startswith(prefix) for prefix in ("/System/", "/usr/libexec/", "/usr/sbin/"))


def _memory_label(proc):
    """Friendly label for a process row, without exposing a full command line."""
    name = (proc.info.get("name") or "Процесс").strip()
    label = _nice(name)
    if label == "Java":
        try:
            label = java_name(" ".join(proc.cmdline()))
        except Exception:  # noqa: BLE001
            pass
    try:
        cmd = " ".join(proc.cmdline()).lower()
        if "dewiar" in cmd:
            return "Dewiar"
        if "google chrome helper" in cmd or "chrome helper" in cmd:
            return "Chrome"
        if "strata-desktop" in cmd or "strata.app" in cmd:
            return "Strata"
    except Exception:  # noqa: BLE001
        pass
    return label or name


def _memory_bundle(proc):
    """Return (bundle path, application name) for a macOS process.

    Electron applications expose most of their RSS as helper processes.  The
    executable path is the reliable way to join those helpers back to the
    owning ``.app``; process names such as ``Codex (Renderer)`` are not.
    """
    if not MAC:
        return "", ""
    try:
        parts = Path(proc.exe()).parts
    except (OSError, AttributeError, TypeError, ValueError):
        return "", ""
    for i, part in enumerate(parts):
        if part.lower().endswith(".app"):
            bundle = Path(*parts[:i + 1])
            return str(bundle), part[:-4] or part
    return "", ""


def _is_dewiar_app_process(proc):
    """Whether a process belongs to the current bundled Dewiar application."""
    try:
        exe = str(proc.exe() or "").lower()
    except Exception:  # noqa: BLE001
        exe = ""
    try:
        name = str(proc.info.get("name") or "").lower()
    except Exception:  # noqa: BLE001
        name = ""
    if name == "dewiar" or "/dewiar.app/" in exe:
        return True
    return False


def memory_processes(limit=24, skip=(), include_dewiar=False):
    """Return large, user-owned processes that may be closed safely by choice.

    This is an advisory list, not an automatic cleaner.  A row contains a PID so
    the UI can ask for explicit confirmation and close exactly what was selected.
    The dedicated RAM window may opt in to showing the current Dewiar bundle;
    other callers keep the application protected from accidental termination.
    """
    try:
        import psutil
    except ImportError:
        return []
    ancestors = {os.getpid(), os.getppid(), *[p.pid for p in psutil.Process(os.getpid()).parents()]}
    # The core is a child of the bundled Dewiar host on macOS.  Normally those
    # processes are omitted so Strata's recovery list cannot offer Dewiar for
    # termination; the dedicated «Память → Оперативная память» window opts in
    # and presents them as one closable .app group.
    own_dewiar = set()
    if include_dewiar:
        for pid in ancestors:
            try:
                proc = psutil.Process(pid)
                if _is_dewiar_app_process(proc):
                    own_dewiar.add(pid)
            except (psutil.Error, OSError):
                continue
    skip = (ancestors - own_dewiar) | {int(x) for x in (skip or ())}
    user = None
    try:
        user = psutil.Process(os.getpid()).username()
    except Exception:  # noqa: BLE001
        pass
    groups = {}
    for proc in psutil.process_iter(["pid", "name", "username", "memory_info"]):
        try:
            pid = int(proc.info["pid"])
            mem = proc.info.get("memory_info")
            rss = int(mem.get("rss", 0) if isinstance(mem, dict) else getattr(mem, "rss", 0))
            name = (proc.info.get("name") or "").strip()
            label = _memory_label(proc)
            if pid in skip or rss < 64 * 2**20 or (user and proc.info.get("username") not in (user, None)):
                continue
            bundle, bundle_name = _memory_bundle(proc)
            # A macOS app is the unit the user can actually close.  Keep the
            # existing process-level fallback for daemons and command-line
            # tools that have no bundle.
            if bundle:
                label = bundle_name
            protected_dewiar = include_dewiar and bundle and bundle_name.lower() == "dewiar"
            if _memory_is_protected(proc, name, label) and not protected_dewiar:
                continue
            try:
                command = " ".join(proc.cmdline())[:180]
            except Exception:  # noqa: BLE001
                command = name
            key = ("app", bundle) if bundle else ("process", pid)
            row = groups.get(key)
            if row is None:
                row = groups[key] = {"pid": pid, "pids": [], "name": label, "rss": 0,
                                     "command": (bundle if bundle else command),
                                     "app_path": bundle, "closable": True, "leader": pid,
                                     "self": bool(protected_dewiar)}
            row["pids"].append(pid)
            row["rss"] += rss
            row["self"] = row["self"] or bool(protected_dewiar)
            # Prefer the real app executable over renderer/helper processes.
            try:
                exe = str(proc.exe())
                if bundle and (exe.endswith(f"/Contents/MacOS/{bundle_name}") or proc.ppid() == 1):
                    row["leader"] = pid
            except (psutil.Error, OSError):
                pass
        except (psutil.Error, OSError, ValueError, TypeError):
            continue
    rows = []
    for row in groups.values():
        pids = sorted(set(row.pop("pids")))
        rows.append({"pid": int(row.pop("leader") or pids[0]), "pids": pids, "name": row["name"],
                     "rss_gib": round(row.pop("rss") / 2**30, 2), "command": row["command"],
                     "app_path": row.pop("app_path"), "closable": row["closable"], "self": row.pop("self", False)})
    rows.sort(key=lambda x: (-x["rss_gib"], x["name"].lower(), x["pid"]))
    return rows[:max(1, min(int(limit or 24), 64))]


def memory_snapshot(required_gib=0, limit=24, include_dewiar=False):
    """Current free memory and candidate rows for the Strata recovery sheet."""
    try:
        import psutil
        mem = psutil.virtual_memory()
        total = mem.total / 2**30
        free = mem.available / 2**30
        used = max(0.0, total - free)
    except (ImportError, OSError):
        total = free = used = 0.0
    required = max(0.0, float(required_gib or 0))
    out = {"free_gib": round(free, 1), "available_gib": round(free, 1),
            "ram_total_gib": round(total, 1), "ram_used_gib": round(used, 1),
            "required_gib": round(required, 1),
            "shortfall_gib": round(max(0.0, required - free), 1),
            "items": memory_processes(limit, include_dewiar=include_dewiar)}
    if include_dewiar:
        try:
            from memcap import dewiar_memory
            own = dewiar_memory()
            core = float(own.get("core", 0) or 0); ui = float(own.get("ui", 0) or 0)
            out["dewiar"] = {"core_gib": round(core, 2), "ui_gib": round(ui, 2),
                             "total_gib": round(core + ui, 2), "persona_separate": False}
        except (ImportError, OSError, ValueError):
            out["dewiar"] = {"core_gib": 0.0, "ui_gib": 0.0, "total_gib": 0.0, "persona_separate": False}
    return out


def _quit_macos_app(app_path):
    """Ask a selected GUI app to quit through AppKit/Apple Events."""
    if not MAC or not app_path:
        return False
    name = Path(app_path).stem
    # The name originates from a local .app path, but still escape it before
    # embedding it in AppleScript.
    quoted = name.replace("\\", "\\\\").replace('"', '\\"')
    try:
        result = subprocess.run(["/usr/bin/osascript", "-e", f'tell application "{quoted}" to quit'],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=8)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def close_memory_processes(pids, required_gib=0, include_dewiar=False):
    """Gracefully terminate explicitly selected user processes and report the result.

    The caller must obtain a fresh ``memory_processes`` list and confirm the
    selection.  We refuse unknown/system/Dewiar PIDs and never escalate to SIGKILL.
    """
    try:
        import psutil
    except ImportError:
        raise ValueError("Невозможно получить список процессов")
    try:
        wanted = sorted({int(pid) for pid in (pids or [])})
    except (TypeError, ValueError):
        raise ValueError("Некорректный список процессов")
    if not wanted or len(wanted) > 16:
        raise ValueError("Выберите от 1 до 16 приложений")
    allowed = {}
    for row in memory_processes(limit=128, include_dewiar=include_dewiar):
        for row_pid in row.get("pids", [row["pid"]]):
            allowed[int(row_pid)] = row
    before = psutil.virtual_memory().available / 2**30
    closed, refused, failed = [], [], []
    seen = set()
    for pid in wanted:
        row = allowed.get(pid)
        if not row or not row.get("closable"):
            refused.append(pid)
            continue
        key = row.get("app_path") or ("process", row["pid"])
        if key in seen:
            continue
        seen.add(key)
        member_pids = [int(x) for x in row.get("pids", [row["pid"]])]
        members = []
        for member_pid in member_pids:
            try:
                members.append(psutil.Process(member_pid))
            except (psutil.Error, OSError):
                pass
        try:
            asked_to_quit = False
            if row.get("app_path"):
                # Give a GUI application a short window to close normally.  In
                # particular, this lets Electron/Chromium flush its profile
                # instead of immediately terminating the main process.
                asked_to_quit = _quit_macos_app(row["app_path"])
            _, alive = psutil.wait_procs(members, timeout=3 if asked_to_quit else 0)
            # Never terminate the core process from inside this RPC.  If the
            # user selected Dewiar, Apple Events above may have closed it; if
            # not, report the remaining process and let the user close it from
            # the normal window instead of killing the caller mid-response.
            self_pid = os.getpid()
            self_group = bool(row.get("self"))
            self_remaining = []
            for proc in alive:
                if proc.pid == self_pid:
                    if proc.is_running():
                        self_remaining.append(proc.pid)
                    continue
                if self_group:
                    # The row can contain the bundled host process as well as
                    # the core.  Do not kill one half of Dewiar if Apple Events
                    # were denied or timed out; report the complete remainder.
                    if proc.is_running():
                        self_remaining.append(proc.pid)
                    continue
                if proc.is_running():
                    proc.terminate()
            wait_for = [p for p in alive if p.pid != self_pid and not self_group]
            _, alive = psutil.wait_procs(wait_for, timeout=5)
            remaining = self_remaining + [p.pid for p in alive]
            if remaining:
                failed.append({"pid": row["pid"], "name": row["name"], "remaining": remaining})
            else:
                closed.append({"pid": row["pid"], "pids": member_pids, "name": row["name"], "rss_gib": row["rss_gib"]})
        except (psutil.Error, OSError):
            refused.append(pid)
    if closed:
        try:
            psutil.wait_procs([psutil.Process(x["pid"]) for x in closed], timeout=2)
        except (psutil.Error, OSError):
            pass
    time.sleep(0.15)
    after = psutil.virtual_memory().available / 2**30
    required = max(0.0, float(required_gib or 0))
    out = {"closed": closed, "refused": refused, "failed": failed,
           "freed_gib": round(max(0.0, after - before), 1),
           "free_gib": round(after, 1), "required_gib": round(required, 1),
           "shortfall_gib": round(max(0.0, required - after), 1),
           "items": memory_processes(limit=24, include_dewiar=include_dewiar)}
    if include_dewiar:
        try:
            from memcap import dewiar_memory
            own = dewiar_memory()
            core = float(own.get("core", 0) or 0); ui = float(own.get("ui", 0) or 0)
            out["dewiar"] = {"core_gib": round(core, 2), "ui_gib": round(ui, 2),
                             "total_gib": round(core + ui, 2), "persona_separate": False}
        except (ImportError, OSError, ValueError):
            out["dewiar"] = {"core_gib": 0.0, "ui_gib": 0.0, "total_gib": 0.0, "persona_separate": False}
    return out


# Java — это не одна программа: в её командной строке видно, какая (отзыв 11.10: «Java 1,7 ГБ — это наш Dewiar?» — нет,
# Dewiar на Python; чаще это среда разработки, сборка Gradle, игра или сервер).
JAVA_APPS = [(r"android-studio|android studio", "Android Studio"), (r"pycharm", "PyCharm"), (r"idea|intellij", "IntelliJ IDEA"),
             (r"webstorm", "WebStorm"), (r"clion", "CLion"), (r"goland", "GoLand"), (r"rider", "Rider"), (r"datagrip", "DataGrip"),
             (r"jetbrains-toolbox|toolbox", "JetBrains Toolbox"), (r"kotlin.*daemon|kotlincompiledaemon", "сборка Kotlin"),
             (r"gradle", "сборка Gradle"), (r"maven|plexus-classworlds", "сборка Maven"), (r"minecraft|tlauncher|\.minecraft", "Minecraft"),
             (r"elasticsearch", "Elasticsearch"), (r"opensearch", "OpenSearch"), (r"dbeaver", "DBeaver"), (r"eclipse", "Eclipse"),
             (r"netbeans", "NetBeans"), (r"jenkins", "Jenkins"), (r"kafka", "Kafka"), (r"zookeeper", "ZooKeeper"),
             (r"languagetool", "LanguageTool"), (r"libreoffice|soffice", "LibreOffice"), (r"freeplane|freemind", "Freeplane"),
             (r"jdownloader", "JDownloader"), (r"burp", "Burp Suite"), (r"ghidra", "Ghidra"), (r"sonar", "SonarQube"), (r"tomcat|catalina", "Tomcat")]


def java_name(cmdline):
    """Командная строка процесса Java (байты с \\0 или текст) -> «Java (IntelliJ IDEA)», «Java (app.jar)» или «Java»."""
    c = cmdline.decode("utf-8", "replace") if isinstance(cmdline, (bytes, bytearray)) else str(cmdline or "")
    c = c.replace("\0", " ")
    low = c.lower()
    for rx, nm in JAVA_APPS:
        if re.search(rx, low):
            return f"Java ({nm})"
    m = re.search(r"-jar\s+(\S+\.jar)", c)
    if m:
        return f"Java ({Path(m.group(1)).name})"
    return "Java"


def _nice(name):
    n = re.sub(r"\.exe$", "", (name or "").strip(), flags=re.I)
    return NAMES.get(n.lower(), n)


def pressure():
    """-> {"total", "free" (ГБ), "swap" (% занятой подкачки или None — её нет/не узнать), "psi" (доля времени, когда
    программы ждали память, % за минуту, Linux; иначе None), "tight": памяти не хватает}."""
    fake = os.environ.get("DEWIAR_FAKE_PRESSURE")
    if fake:
        free, total, swap, *psi10 = (float(x) for x in fake.split(","))
        return _judge(total, free, swap, None, psi10[0] if psi10 else None)
    if MAC:
        try:
            import psutil
            mem, swap = psutil.virtual_memory(), psutil.swap_memory()
            return _judge(mem.total / 2**30, mem.available / 2**30, swap.percent if swap.total else None, None)
        except (ImportError, OSError):
            return _judge(0, 0, None, None)
    if os.name == "nt":
        try:
            import ctypes

            class M(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
            m = M()
            m.len = ctypes.sizeof(M)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                commit = 100.0 * (m.tp - m.ap) / m.tp if m.tp else None    # выделено из «память + файл подкачки»
                return _judge(m.total / 2**30, m.avail / 2**30, commit, None)
        except Exception:  # noqa: BLE001
            pass
        return {"total": 0, "free": 0, "swap": None, "psi": None, "psi10": None, "tight": False, "critical": False}
    try:
        mem = {k: int(v) for k, v in re.findall(r"(\w+):\s+(\d+)", Path("/proc/meminfo").read_text())}
    except (OSError, ValueError):
        return {"total": 0, "free": 0, "swap": None, "psi": None, "psi10": None, "tight": False, "critical": False}
    st, sf = mem.get("SwapTotal", 0), mem.get("SwapFree", 0)
    psi = psi10 = None
    try:
        m = re.search(r"^some avg10=([\d.]+) avg60=([\d.]+)", Path("/proc/pressure/memory").read_text(), re.M)
        psi10, psi = (float(m.group(1)), float(m.group(2))) if m else (None, None)
    except (OSError, ValueError):
        pass
    return _judge(mem.get("MemTotal", 0) / 1048576, mem.get("MemAvailable", 0) / 1048576,
                  100.0 * (st - sf) / st if st else None, psi, psi10)


def _judge(total, free, swap, psi, psi10=None):
    """Тесно: свободной памяти меньше 0,7 ГБ (или 7 %) и подкачка почти полна (или её нет) — либо программы заметную
    долю времени ждут память (PSI ≥ 20 % за минуту). Опасно (critical) — ещё чуть-чуть, и рабочий стол замрёт:
    свободно меньше 0,4 ГБ (или 4 %) при почти полной подкачке либо последние 10 с программы ждали память ≥ 40 %
    времени. Тогда разгружаться надо сразу, а не через две проверки."""
    low = total > 0 and free < max(0.7, total * 0.07)
    tight = bool((low and (swap is None or swap >= 50)) or (psi is not None and psi >= 20))
    crit = bool((total > 0 and free < max(0.4, total * 0.04) and (swap is None or swap >= 80))
                or (psi10 is not None and psi10 >= 40))
    return {"total": round(total, 1), "free": round(free, 2), "swap": None if swap is None else round(swap),
            "psi": psi, "psi10": psi10, "tight": tight or crit, "critical": crit}


def top_memory(n=3, skip=()):
    """Кто больше всех занимает память -> [(название, ГБ)], по программам (все окна Chrome — одной строкой)."""
    sums = {}
    if os.name == "nt" or MAC:
        try:
            import psutil
            for p in psutil.process_iter(["pid", "name", "memory_info"]):
                if p.info["pid"] in skip or not p.info.get("memory_info"):
                    continue
                k = _nice(p.info["name"])
                if k in ("Java", "javaw"):
                    try:
                        k = java_name(" ".join(p.cmdline()))
                    except Exception:  # noqa: BLE001
                        k = "Java"
                if k.lower().startswith("python"):
                    try:
                        k = "Dewiar" if "dewiar" in " ".join(p.cmdline()).lower() else k
                    except Exception:  # noqa: BLE001
                        pass
                sums[k] = sums.get(k, 0) + p.info["memory_info"].rss
        except Exception:  # noqa: BLE001
            return []
    else:
        for d in Path("/proc").iterdir():
            if not d.name.isdigit() or int(d.name) in skip:
                continue
            try:
                txt = (d / "status").read_text()
            except OSError:
                continue
            nm = re.search(r"^Name:\s+(.+)$", txt, re.M)
            rss = re.search(r"^VmRSS:\s+(\d+)", txt, re.M)
            if nm and rss:
                k = _nice(nm.group(1))
                if k == "Java":                              # какая именно программа на Java
                    try:
                        k = java_name((d / "cmdline").read_bytes())
                    except OSError:
                        pass
                if k.lower().startswith("python"):           # сам Dewiar — тоже Python: называем его по имени
                    try:
                        k = "Dewiar" if b"dewiar" in (d / "cmdline").read_bytes().lower() else k
                    except OSError:
                        pass
                sums[k] = sums.get(k, 0) + int(rss.group(1)) * 1024
    top = sorted(sums.items(), key=lambda x: -x[1])[:n]
    return [(k, round(v / 2**30, 1)) for k, v in top if v >= 0.3 * 2**30]


def pressure_text(p, top, light=False, advice=""):
    """Совет человеку: сколько свободно, кто занимает, что сделать."""
    gb = lambda x: f"{x:.1f}".replace(".", ",")     # noqa: E731
    head = f"Свободно {gb(p['free'])} ГБ памяти из {p['total']:.0f}"
    if p.get("swap") is not None and p["swap"] >= 50:
        head += f", подкачка заполнена на {p['swap']}%"
    out = [head + " — поэтому окна открываются медленно, а голос запаздывает."]
    if top:
        out.append("Больше всего занимают: " + ", ".join(f"{k} ({gb(v)} ГБ)" for k, v in top) + ".")
    tips = ["закройте то, чем сейчас не пользуетесь"]
    if not light:
        tips.append("включите лёгкий режим (студия → «Dewiar.pro»)")
    out.append("Что сделать: " + ", или ".join(tips) + ".")
    if advice and "сервер" in advice and not advice.startswith("Речь распознаёт"):
        out.append(advice)
    return " ".join(out)
