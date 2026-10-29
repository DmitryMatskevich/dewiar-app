"""Защита от опасных действий: ассистент не удалит системные папки, домашнюю папку, «всё» и не сотрёт диск —
даже если пользователь (или модель) этого попросит. Обычное удаление файлов — только в корзину и с подтверждением.

check_path(p)        — можно ли удалять/перезаписывать этот путь;
check_command(cmd)   — разбор команды терминала (bash или PowerShell): ok / warn (только с подтверждением) / block;
plan_delete + trash  — проверить пути, спросить пользователя (describe_delete) и удалить в корзину;
site_dir(base)       — sitecustomize.py для скриптов лаборатории (workspace.py): удаление только в разрешённых папках.
Это защита от ошибок модели и неосторожных просьб, а не песочница: разбор команд эвристический, и обойти его можно.
"""

import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from platform_info import MAC

WIN = os.name == "nt"
WORKSPACE = []            # папка ассистента (заполняет engine.py) — её корень и подпапки первого уровня тоже защищены

SYSTEM_UNIX = ["/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/libx32", "/opt", "/proc", "/root", "/run",
               "/sbin", "/snap", "/srv", "/sys", "/usr", "/var", "/media", "/mnt", "/lost+found", "/home", "/tmp"]
HOME_FOLDERS = ["Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos", "Templates", "Public",
                "Рабочий стол", "Документы", "Загрузки", "Музыка", "Изображения", "Видео", "Шаблоны", "Общедоступные",
                ".ssh", ".gnupg", ".config", ".local", ".local/share", ".cache", ".mozilla", "snap", "OneDrive",
                "AppData", "AppData/Roaming", "AppData/Local"]


class Blocked(Exception):
    """Действие запрещено; текст — причина по-русски, её можно сказать пользователю."""
    pass


def _home():
    return Path(os.path.expanduser("~")).resolve()


def _norm(p):
    """Абсолютный путь с раскрытыми «~» и ссылками (resolve), чтобы ссылка на системную папку не прошла проверку."""
    try:
        return Path(os.path.abspath(os.path.expanduser(str(p)))).resolve()
    except (OSError, RuntimeError):
        return Path(os.path.abspath(os.path.expanduser(str(p))))


def _win_system():
    out = []
    for v in ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "ProgramW6432"):
        if os.environ.get(v):
            out.append(os.environ[v])
    out += ["C:/Windows", "C:/Program Files", "C:/Program Files (x86)", "C:/ProgramData", "C:/Users", "C:/Recovery",
            "C:/System Volume Information", "C:/$Recycle.Bin", "C:/Boot"]
    return out


def protected():
    """-> (точные пути, которые трогать нельзя; корни, внутри которых нельзя ничего).
    /home, /tmp, /media, /mnt, /opt, /run защищены только сами — внутри них работать можно (кроме /opt/dewiar).
    Считается при каждом вызове: WORKSPACE и папки ассистента могут меняться."""
    home = _home()
    exact = {home} | {_norm(home / f) for f in HOME_FOLDERS}
    inside = set()
    if WIN:
        inside |= {_norm(p) for p in _win_system() if not p.endswith("Users")}
        exact |= {_norm("C:/Users")}
        for d in "CDEFGHIJ":
            exact.add(Path(f"{d}:/"))
    else:
        exact |= {Path("/")} | {Path(p) for p in SYSTEM_UNIX}
        inside |= {Path(p) for p in SYSTEM_UNIX if p not in ("/home", "/tmp", "/media", "/mnt", "/opt", "/run")}
        inside.add(Path("/opt/dewiar"))
        if MAC:
            # /etc и /var на Mac — ссылки. Обе стороны сравнения должны быть каноническими.
            exact = {_norm(p) for p in exact}
            inside = {_norm(p) for p in inside}
            exact |= {_norm(p) for p in ("/System", "/Library", "/Applications", "/Users", "/Volumes", "/private")}
            inside |= {_norm(p) for p in ("/System", "/Library", "/Applications", "/private/etc", "/cores")}
            # Пользовательские временные файлы живут внутри /var/folders; системные базы отдельно защищены.
            inside.discard(_norm("/var"))
            inside |= {_norm(p) for p in ("/private/var/db", "/private/var/root", "/private/var/audit")}
            exact |= {_norm(home / p) for p in ("Library", "Movies", "Applications", ".Trash", "Library/Mobile Documents")}
    for w in WORKSPACE:
        w = _norm(w)
        exact.add(w)
        try:
            exact |= {c.resolve() for c in w.iterdir() if c.is_dir()}
        except OSError:
            pass
    # своя программа и её настройки
    try:
        from settings import CONFIG_DIR, STATE_DIR
        inside |= {_norm(CONFIG_DIR), _norm(STATE_DIR)}
    except Exception:  # noqa: BLE001
        pass
    return exact, inside


# Системные пути Windows по написанию — работает и на Linux (путь от модели может быть виндовым), и до resolve().
WIN_SYS_RX = re.compile(r"^[a-z]:[\\/]*(?:(?:windows|program files(?: \(x86\))?|programdata|recovery|boot|"
                        r"system volume information|\$recycle\.bin)(?:[\\/].*)?|users[\\/]*|users[\\/]+[^\\/]+[\\/]*)?$", re.I)


def check_path(p):
    """None — можно удалять/перезаписывать; иначе причина по-русски."""
    raw = str(p).strip().strip('"\'')
    if WIN_SYS_RX.match(raw):
        return f"«{raw}» — системная папка Windows"
    t = _norm(p)
    exact, inside = protected()
    if t in exact or str(t) in ("/", "\\") or re.fullmatch(r"[A-Za-z]:[\\/]?", str(t)):
        return f"«{t}» — системная или важная папка"
    for r in inside:
        if t == r or r in t.parents:
            return f"«{t}» находится в системной папке «{r}»"
    home = _home()
    if t in home.parents:                      # /home, C:\Users — выше домашней папки
        return f"«{t}» содержит домашнюю папку"
    return None


# ---------- команды терминала ----------

# Команды удаления: их цели проверяются по check_path; удаление мимо корзины всегда требует подтверждения.
DEL_BASH = {"rm", "rmdir", "unlink", "shred", "srm", "wipe"}
DEL_PS = {"remove-item", "ri", "del", "erase", "rd", "rmdir", "rm"}
# Ищутся по всей команде в нижнем регистре: BLOCK_RX — запрещено всегда, WARN_RX — только с подтверждением.
BLOCK_RX = [
    (r"\bmkfs(\.\w+)?\b|\bmke2fs\b|\bwipefs\b|\bfdisk\b|\bparted\b|\bsgdisk\b|\bdiskpart\b|\bformat-volume\b|\bclear-disk\b|"
     r"\binitialize-disk\b|\bformat\s+[a-z]:", "форматирование или разметка диска"),
    (r"\bdd\b[^|;&]*\bof=/dev/", "запись прямо на диск"),
    (r">\s*/dev/(sd|nvme|hd|vd|mmcblk)", "запись прямо на диск"),
    (r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:", "«вилочная бомба» — повесит компьютер"),
    (r"--no-preserve-root", "удаление корня системы"),
    (r"\bbcdedit\b|\bbootrec\b|\bgrub-install\b|\bupdate-grub\b|\befibootmgr\b", "изменение загрузчика"),
    (r"\breg(\.exe)?\s+delete\s+hk(lm|ey_local_machine)", "удаление системного реестра"),
    (r"\bchmod\s+(-\w*R\w*\s+)?[0-7]{3,4}\s+/\s*$|\bchown\s+-\w*R\w*\s+\S+\s+/\s*$", "права на всю систему"),
    (r"\bpasswd\b|\busermod\b|\buserdel\b|\bdeluser\b|\bnet\s+user\b", "учётные записи пользователей"),
    (r"\bcrypt(setup)?\s+luksformat|\bcipher\s+/w", "необратимое стирание"),
]
WARN_RX = [
    (r"\bsudo\b|\bpkexec\b|\bdoas\b|\brunas\b|start-process[^|]*-verb\s+runas", "с правами администратора"),
    (r"\bshutdown\b|\breboot\b|\bpoweroff\b|\bhalt\b|\bsystemctl\s+(poweroff|reboot|halt|suspend|hibernate)|"
     r"\bstop-computer\b|\brestart-computer\b", "выключит или перезагрузит компьютер"),
    (r"\bchmod\s+-\w*R|\bchown\s+-\w*R|\bicacls\b|\btakeown\b", "меняет права на много файлов"),
    (r"\bgit\s+(clean\s+-\w*[fdx]|reset\s+--hard|push\s+(-f|--force))", "необратимо меняет репозиторий"),
    (r"\bkill(all)?\s+-9\s+-1\b|\bpkill\s+-9\b|\btaskkill\s+/f", "принудительно закроет программы"),
    (r"\bapt(-get)?\s+(remove|purge|autoremove)|\bdpkg\s+(-r|-P|--purge|--remove)|\bsnap\s+remove|\bwinget\s+uninstall|"
     r"\bpip\d?\s+uninstall", "удалит программы"),
    (r"\bfind\b[^|;&]*(-delete|-exec\s+rm)", "массовое удаление файлов"),
    (r"\bmv\b[^|;&]*\s/dev/null\b", "удаление через /dev/null"),
    (r"\btruncate\b|\bclear-content\b", "очистит содержимое файлов"),
    (r"\bcurl\b[^|]*\|\s*(ba|z)?sh\b|\bwget\b[^|]*\|\s*(ba|z)?sh\b|\biex\b|invoke-expression", "запустит код из интернета"),
]


def _split(cmd):
    """Команда → простые команды (по ; && || | & и переносам строк) → списки слов. Грубо: разделители в кавычках
    тоже режут команду — для проверки это безопаснее, чем пропустить."""
    parts = re.split(r"\|\||&&|[;\n|&]", cmd)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        try:
            w = shlex.split(p, posix=not WIN)
        except ValueError:
            w = p.split()
        out.append([x.strip("'\"") for x in w])
    return out


def _expand(arg, cwd):
    """Путь из команды — абсолютным: домашняя папка из $HOME/%USERPROFILE%/~, относительный — от cwd."""
    a = arg
    home = str(_home())
    for v in ("$HOME", "${HOME}", "%USERPROFILE%", "$env:USERPROFILE", "$env:HOMEPATH", "%HOMEPATH%"):
        a = a.replace(v, home)
    if a == "~" or a.startswith(("~/", "~\\")):
        a = home + a[1:]
    if not os.path.isabs(a) and not re.match(r"^[A-Za-z]:", a):
        a = os.path.join(cwd, a)
    return a


def _targets(words, cwd):
    """Пути, которые удаляет одна простая команда: None — это не удаление, [] — удаление без видимых путей.
    Пропускает обёртки (sudo, env, xargs, VAR=…) и заглядывает внутрь bash -c / cmd /c / powershell -Command."""
    if not words:
        return None
    i = 0
    while i < len(words) and (words[i] in ("sudo", "doas", "nice", "nohup", "command", "builtin", "env", "time", "xargs")
                              or re.match(r"^\w+=", words[i])):
        i += 1
    if i >= len(words):
        return None
    head = words[i].lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if head.endswith(".exe"):
        head = head[:-4]
    args = words[i + 1:]
    if head == "gio" and args[:1] == ["trash"]:
        return None                                       # в корзину — это не «насовсем»
    if head in ("cmd", "powershell", "pwsh", "bash", "sh", "zsh") and args:
        inner = " ".join(a for a in args if not a.startswith(("-", "/c", "/C")) or " " in a)
        return [t for ws in _split(inner) for t in (_targets(ws, cwd) or [])] or None
    if head not in DEL_BASH | DEL_PS:
        return None
    paths = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        low = a.lower()
        if low in ("-path", "-literalpath"):
            continue
        if low in ("-include", "-exclude", "-filter"):
            skip = True
            continue
        if a.startswith("-") or (WIN and re.fullmatch(r"/[a-zA-Z]", a)):
            continue
        paths.append(a)
    if not paths and head in ("rm", "rmdir", "unlink", "shred"):
        return []
    return [_expand(p, cwd) for p in paths]


def _glob_base(p):
    """«~/Загрузки/*» → («~/Загрузки», True); «/» → («/», False)."""
    m = re.search(r"[*?\[]", p)
    if not m:
        return p, False
    base = p[:m.start()]
    base = base.rsplit("/", 1)[0] if "/" in base else (base.rsplit("\\", 1)[0] if "\\" in base else base)
    return base or "/", True


def check_command(cmd, cwd=None):
    """-> {"level": "ok"|"warn"|"block", "reason": str, "targets": [пути]}. block — по BLOCK_RX или если удаление
    задевает защищённый путь; warn — любое удаление мимо корзины, путь из переменной, совпадение с WARN_RX."""
    cwd = str(cwd or _home())
    low = " ".join((cmd or "").lower().split())
    if MAC and re.search(r"\b(?:diskutil\s+(?:erase\w*|partitiondisk)|csrutil\b|nvram\b|bless\b|"
                         r"tmutil\s+delete\b|dscl\b|sysadminctl\b|launchctl\s+bootout\b)", low):
        return {"level": "block", "reason": "команда изменяет системные настройки или диски macOS", "targets": []}
    for rx, why in BLOCK_RX:
        if re.search(rx, low):
            return {"level": "block", "reason": why, "targets": []}
    targets, reasons = [], []
    for words in _split(cmd or ""):
        t = _targets(words, cwd)
        if t is None:
            continue
        if not t:
            reasons.append("удаление")
            continue
        for p in t:
            base, glob = _glob_base(p)
            if "$" in p or "`" in p or "%" in p:
                reasons.append(f"удаление по переменной ({p}) — не видно, что именно")
                targets.append(p)
                continue
            why = check_path(base if glob else p)
            if why:
                return {"level": "block", "reason": why if not glob else f"«{p}» — всё содержимое важной папки",
                        "targets": [p]}
            targets.append(p)
        reasons.append("удалит без корзины")
    for rx, why in WARN_RX:
        if re.search(rx, low):
            reasons.append(why)
    if reasons:
        return {"level": "warn", "reason": "; ".join(dict.fromkeys(reasons)), "targets": targets}
    return {"level": "ok", "reason": "", "targets": []}


def describe_warn(v):
    """Вопрос-подтверждение для опасной команды."""
    tg = ", ".join(v["targets"][:4]) + (" и др." if len(v["targets"]) > 4 else "")
    return f"Осторожно: команда {v['reason']}" + (f" ({tg})" if tg else "") + "."


# ---------- корзина ----------

def _size(p):
    """-> (файлов, байт); счёт обрывается на 200 000 файлов, чтобы вопрос о большой папке не ждал минуты."""
    if p.is_file() or p.is_symlink():
        try:
            return 1, p.lstat().st_size
        except OSError:
            return 1, 0
    n = s = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            n += 1
            try:
                s += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
            if n > 200000:
                return n, s
    return n, s


def human_size(b):
    for unit, k in (("ГБ", 1 << 30), ("МБ", 1 << 20), ("КБ", 1 << 10)):
        if b >= k:
            return f"{b / k:.1f} {unit}".replace(".0 ", " ").replace(".", ",")
    return f"{b} байт"


def plan_delete(paths):
    """Проверить пути до вопроса пользователю -> [{"path", "dir", "files", "size"}]; Blocked — если что-то нельзя."""
    out = []
    for raw in paths or []:
        p = _norm(raw)
        if not p.exists() and not p.is_symlink():
            raise Blocked(f"нет такого файла или папки: {p}")
        why = check_path(p)
        if why:
            raise Blocked(why)
        n, s = _size(p)
        out.append({"path": str(p), "dir": p.is_dir() and not p.is_symlink(), "files": n, "size": s})
    if not out:
        raise Blocked("не указано, что удалить")
    return out


def describe_delete(plan):
    """Вопрос пользователю перед удалением в корзину (по плану из plan_delete)."""
    names = []
    for x in plan[:3]:
        nm = Path(x["path"]).name or x["path"]
        names.append(f"папку «{nm}» ({x['files']} файл., {human_size(x['size'])})" if x["dir"] else f"«{nm}»")
    more = f" и ещё {len(plan) - 3}" if len(plan) > 3 else ""
    return "Удалить в корзину " + ", ".join(names) + more + "? Восстановить можно из корзины."


def _trash_linux(p):
    """Сначала gio trash (умеет корзину другого диска), без него — сами по стандарту freedesktop:
    ~/.local/share/Trash/{files,info} (файл с другого диска при этом копируется)."""
    if shutil.which("gio"):
        r = subprocess.run(["gio", "trash", str(p)], capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return
    trash = Path(os.environ.get("XDG_DATA_HOME") or _home() / ".local/share") / "Trash"
    (trash / "files").mkdir(parents=True, exist_ok=True)
    (trash / "info").mkdir(parents=True, exist_ok=True)
    name, n = p.name, 1
    while (trash / "files" / name).exists() or (trash / "info" / f"{name}.trashinfo").exists():
        n += 1
        name = f"{p.stem}.{n}{p.suffix}"
    (trash / "info" / f"{name}.trashinfo").write_text(
        f"[Trash Info]\nPath={urllib.parse.quote(str(p))}\nDeletionDate={datetime.now():%Y-%m-%dT%H:%M:%S}\n",
        encoding="utf-8")
    try:
        shutil.move(str(p), str(trash / "files" / name))
    except OSError:
        (trash / "info" / f"{name}.trashinfo").unlink(missing_ok=True)
        raise


def _trash_win(p):
    """«Корзина» Windows через VisualBasic.FileIO из PowerShell; путь — через переменную окружения, а не в тексте
    команды, чтобы кавычки и $ в имени файла не стали кодом."""
    kind = "DeleteDirectory" if p.is_dir() else "DeleteFile"
    ps = ("Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::" + kind +
          "($env:DEWIAR_TRASH, 'OnlyErrorDialogs', 'SendToRecycleBin')")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                       timeout=300, env=dict(os.environ, DEWIAR_TRASH=str(p)), creationflags=0x08000000)
    if r.returncode != 0 or p.exists():
        raise OSError((r.stderr or "не удалось").strip()[-300:])


def trash(plan):
    """Удалить в корзину пункты плана -> имена удалённых. Каждый путь проверяется ещё раз; Blocked или OSError —
    остановка на первом сбое (уже удалённое остаётся в корзине)."""
    done = []
    for x in plan:
        p = Path(x["path"])
        if check_path(p):                       # ещё раз — вдруг путь подменили между вопросом и делом
            raise Blocked(check_path(p))
        if MAC:
            from macsys import trash as trash_mac
            trash_mac(p)
        else:
            (_trash_win if WIN else _trash_linux)(p)
        done.append(p.name)
        time.sleep(0)
    return done


# ---------- скрипты Python задач: защита внутри процесса ----------
# sitecustomize.py, который Python сам загружает при старте скрипта (папка — в PYTHONPATH). Подменяет os.remove,
# os.unlink, os.rmdir, shutil.rmtree и Path.unlink/rmdir: удалять можно только внутри DEWIAR_ALLOW_DEL (папки задачи
# и временная папка, их задаёт workspace.py). Перезапись файлов, os.replace и внешние команды это не ловит.

SITE_GUARD = r'''
import os, shutil, pathlib
_ALLOW = [os.path.realpath(p) for p in os.environ.get("DEWIAR_ALLOW_DEL", "").split(os.pathsep) if p]
def _ok(p):
    try:
        r = os.path.realpath(os.fspath(p))
    except Exception:
        return False
    return any(r == a or r.startswith(a.rstrip(os.sep) + os.sep) for a in _ALLOW)
def _wrap(f, name):
    def g(p, *a, **k):
        if k.get("dir_fd") is None and not _ok(p):     # dir_fd — изнутри rmtree, корень уже проверен
            raise PermissionError(f"Dewiar: удалять можно только в папке задачи и во временной папке, а не «{p}». "
                                  "Если нужно удалить что-то другое — попроси пользователя.")
        return f(p, *a, **k)
    g.__name__ = name
    return g
os.remove = _wrap(os.remove, "remove"); os.unlink = _wrap(os.unlink, "unlink"); os.rmdir = _wrap(os.rmdir, "rmdir")
shutil.rmtree = _wrap(shutil.rmtree, "rmtree")
_pu = pathlib.Path.unlink
pathlib.Path.unlink = lambda self, *a, **k: (_wrap(lambda p, *a2, **k2: _pu(pathlib.Path(p), *a2, **k2), "unlink"))(self, *a, **k)
_pr = pathlib.Path.rmdir
pathlib.Path.rmdir = lambda self: (_wrap(lambda p: _pr(pathlib.Path(p)), "rmdir"))(self)
'''


def site_dir(base):
    """Папка с sitecustomize.py для PYTHONPATH скриптов задачи (файл переписывается, если устарел)."""
    d = Path(base) / "_guard"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "sitecustomize.py"
    if not f.exists() or f.read_text(encoding="utf-8") != SITE_GUARD:
        f.write_text(SITE_GUARD, encoding="utf-8")
    return str(d)
