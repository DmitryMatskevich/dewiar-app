"""Чтобы Dewiar не мог «положить» компьютер (Linux).

Если программе не хватает памяти, Linux начинает выгружать всё подряд в подкачку — и тогда замирает весь
рабочий стол: курсор, окна, звук, иногда так, что спасает только перезагрузка. Поэтому у каждой части Dewiar
есть свой потолок памяти — отдельная «коробка» systemd (cgroup):

* ядро (слух, голос, мысли) — служба dewiar-core с потолком из caps("core");
* окна (виджет, студия) — своя коробка из caps("ui"), запуск — scope_cmd(..., "ui");
* программы, которые открыла Лира (браузер, проигрыватель), — в свою коробку без потолка: они не должны ни
  упираться в потолок Dewiar, ни закрываться вместе с ним при перезапуске ядра.

Упёрлась часть Dewiar в мягкий потолок (MemoryHigh) — замедляется только она сама, а не весь компьютер; в жёсткий
(MemoryMax) — система закрывает только её, служба поднимается снова. dewiar_memory() — сколько Dewiar занимает
сейчас (для журнала и отчётов). Без systemd (Windows, macOS, контейнер) всё работает как раньше, без потолков.
Для проверки: DEWIAR_FAKE_RAM_GB (сколько памяти у «компьютера»), DEWIAR_NO_SCOPE=1 — не использовать systemd-run.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

# Ядро в обычной работе — 1,5–2,5 ГБ (GigaAM, голоса, отпечатки голоса), окна — 0,3–1 ГБ. Потолки — с запасом
# вдвое и больше, но так, чтобы рабочему столу всегда оставалось место.
CAPS = {"core": (0.30, 3.0, 6.0, 0.45, 4.0, 8.0),       # доля памяти, не меньше, не больше: мягкий, затем жёсткий
        "ui": (0.15, 1.5, 3.0, 0.25, 2.5, 5.0)}
SWAP_MAX = "1G"                                         # в подкачку — не больше гигабайта: долгие выгрузки и есть «зависание»


def ram_total_gb():
    fake = os.environ.get("DEWIAR_FAKE_RAM_GB")
    if fake:
        return float(fake)
    try:
        return int(re.search(r"^MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text(), re.M).group(1)) / 1048576
    except (OSError, AttributeError, ValueError):
        return 0.0


def caps(part, total=None):
    """(мягкий, жёсткий) потолок части Dewiar в ГБ; part — "core" или "ui". Памяти не узнать — нижние значения."""
    share, lo, hi, share2, lo2, hi2 = CAPS[part]
    t = ram_total_gb() if total is None else total
    hard = round(min(hi2, max(lo2, t * share2)), 1)
    # мягкий потолок — у самой границы: раньше он стоял заметно ниже, и при подходе к нему система тормозила ядро
    # (отчёт 11.10: «Лира» → «слушаю», а фраза теряется). Тормозить заранее не нужно — защищает жёсткий потолок.
    return round(hard - 0.5, 1), hard


def _mb(gb):
    return f"{int(gb * 1024)}M"


def props(part):
    """Свойства systemd для части: ["MemoryHigh=…", "MemoryMax=…", "MemorySwapMax=1G"]."""
    soft, hard = caps(part)
    return [f"MemoryHigh={_mb(soft)}", f"MemoryMax={_mb(hard)}", f"MemorySwapMax={SWAP_MAX}"]


_SCOPE = {}


def can_scope(part=None):
    """Можно ли запускать в своей коробке systemd (systemd-run --user --scope). Проверяется один раз на пробном
    «true» с теми же свойствами: старый systemd, нет сеанса пользователя, Windows — False, запуск как раньше."""
    if os.name == "nt" or os.environ.get("DEWIAR_NO_SCOPE"):
        return False
    if part in _SCOPE:
        return _SCOPE[part]
    ok = False
    run = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    if shutil.which("systemd-run") and (run / "systemd").exists():
        cmd = ["systemd-run", "--user", "--scope", "--quiet", "--collect"]
        for p in (props(part) if part else []):
            cmd += ["-p", p]
        try:
            ok = subprocess.run(cmd + ["true"], capture_output=True, timeout=8).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
    _SCOPE[part] = ok
    return ok


def scope_cmd(cmd, part=None, what=""):
    """Команда, запускающая cmd в своей коробке systemd: part ("core"/"ui") — с потолком памяти, None — без
    потолка (программы, открытые Лирой: так они не закроются при перезапуске ядра). Нельзя — cmd как есть."""
    cmd = list(cmd)
    if not can_scope(part):
        return cmd
    pre = ["systemd-run", "--user", "--scope", "--quiet", "--collect"]
    if what:
        pre += ["--description", what]
    for p in (props(part) if part else []):
        pre += ["-p", p]
    return pre + cmd


# ---------- сколько занимает сам Dewiar ----------

def _procs():
    """{pid: (ppid, rss в байтах, имя)} по /proc."""
    out = {}
    if __import__("sys").platform == "darwin":
        import psutil
        for p in psutil.process_iter(["pid", "ppid", "name", "memory_info"]):
            info = p.info
            if info.get("memory_info") and info.get("ppid") is not None:
                out[p.pid] = (info["ppid"], info["memory_info"].rss, info.get("name") or "")
        return out
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            st = (d / "stat").read_text()
            rss = re.search(r"^VmRSS:\s+(\d+)", (d / "status").read_text(), re.M)
        except OSError:
            continue
        r = st.rfind(")")
        f = st[r + 2:].split()
        out[int(d.name)] = (int(f[1]), int(rss.group(1)) * 1024 if rss else 0, st[st.find("(") + 1:r])
    return out


def _tree(root, procs):
    kids = {}
    for pid, (pp, _r, _n) in procs.items():
        kids.setdefault(pp, []).append(pid)
    seen, todo = set(), [root]
    while todo:
        p = todo.pop()
        if p in seen or p not in procs:
            continue
        seen.add(p)
        todo += kids.get(p, [])
    return seen


def tree_gb(pid):
    """Сколько занимают процесс pid и все его потомки, ГБ (Linux; иначе 0)."""
    try:
        procs = _procs()
    except OSError:
        return 0.0
    return round(sum(procs[p][1] for p in _tree(pid, procs)) / 2**30, 2)


def dewiar_memory(core_pid=None):
    """-> {"core": ГБ (ядро и его процессы: голоса, радио), "ui": ГБ (окна и их WebKit), "biggest": (имя, ГБ)}.
    Linux; иначе нули. Окна ищутся по команде ui/app.py."""
    res = {"core": 0.0, "ui": 0.0, "biggest": ("", 0.0)}
    mac = __import__("sys").platform == "darwin"
    if mac:
        import psutil
        try:
            root = psutil.Process(core_pid or os.getpid())
            core_members = [root] + root.children(recursive=True)
            members = list(core_members)
            core_ids = {member.pid for member in core_members}
            ui_root_pid = None
            # In the bundled Mac app the WebKit window is the parent process
            # and the core runs as ``Dewiar --core`` below it.  Include that
            # parent in the total and report it as UI memory; on source runs
            # where the UI is a child, the older ui/app.py detection below is
            # still used.
            try:
                parent = root.parent()
                while parent:
                    try:
                        exe = str(parent.exe() or "").lower()
                        name = str(parent.name() or "").lower()
                    except (psutil.Error, OSError):
                        break
                    if name == "dewiar" or "/dewiar.app/" in exe:
                        parent_members = [parent] + parent.children(recursive=True)
                        members = list({m.pid: m for m in members + parent_members}.values())
                        ui_root_pid = parent.pid
                        break
                    parent = parent.parent()
            except (psutil.Error, OSError):
                pass
            sizes, ui_pids = {}, set()
            for member in members:
                try:
                    sizes[member.pid] = (member.name(), member.memory_info().rss)
                    command = member.cmdline()
                    # A source run starts ``ui/app.py`` as a child of the
                    # measured process, so it is already present in
                    # ``core_ids``.  Keep the explicit command check ahead of
                    # the bundled-app parent check; otherwise the UI would be
                    # counted as core on macOS.
                    is_ui = member.pid != root.pid and any(arg.endswith("ui/app.py") for arg in command)
                    if is_ui or member.pid == ui_root_pid:
                        ui_pids.add(member.pid)
                        ui_pids.update(child.pid for child in member.children(recursive=True) if child.pid not in core_ids)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            res["core"] = round(sum(size for pid, (_, size) in sizes.items() if pid not in ui_pids) / 2**30, 2)
            res["ui"] = round(sum(size for pid, (_, size) in sizes.items() if pid in ui_pids) / 2**30, 2)
            if sizes:
                name, size = max(sizes.values(), key=lambda item: item[1])
                res["biggest"] = (name, round(size / 2**30, 2))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return res
    if not mac and not Path("/proc/self/stat").exists():
        return res
    try:
        procs = _procs()
    except OSError:
        return res
    me = core_pid or os.getpid()
    ui = set()
    for pid in procs:                     # окна, открытые самим ядром, — его потомки, но считаются как окна
        if pid == me or pid in ui:
            continue
        try:
            if mac:
                import psutil
                cl = "\0".join(psutil.Process(pid).cmdline()).encode()
            else:
                cl = (Path("/proc") / str(pid) / "cmdline").read_bytes()
        except Exception:  # процесс может завершиться во время замера
            continue
        if b"ui/app.py" in cl and b"python" in cl.split(b"\0")[0]:
            ui |= _tree(pid, procs)
    core = _tree(me, procs) - ui
    big = max(core | ui, key=lambda p: procs[p][1], default=None)
    gb = lambda ps: round(sum(procs[p][1] for p in ps) / 2**30, 2)     # noqa: E731
    res.update(core=gb(core), ui=gb(ui))
    if big:
        res["biggest"] = (procs[big][2], round(procs[big][1] / 2**30, 2))
    return res
