"""Серверы пользователя по SSH: «проверь место на сервере сайта», «почему не открывается сайт — посмотри nginx».

Профиль сервера — имя («сайт»), адрес, порт, пользователь. Вход — по ключу Dewiar (ed25519, создаётся один раз и лежит
в папке настроек, в копию без пароля не попадает). Пароль сервера спрашивается один раз — чтобы положить ключ на
сервер, — и нигде не хранится. Отпечаток сервера запоминается при первом входе: если он потом сменится (подмена
сервера), Dewiar откажется подключаться.

Каждая команда — только после «Выполнить?»; для сервера можно выбрать ask="changes" — тогда без вопроса идут команды,
которые readonly() признал только смотрящими (df, free, systemctl status, journalctl…; решает tools.auto_ok).
check(): разрушительное (CATASTROPHE: «rm -rf /», mkfs, dd на диск, fork-бомба) не выполняется вовсе, опасное (WARN) —
с отдельным предупреждением. Работает системный ssh: Linux — openssh-client, Windows 10/11 — встроенный OpenSSH.
Свой known_hosts в папке ssh/ рядом с ключом; ключ попадает только в резервную копию с паролем (backup.py)."""

import json
import logging
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from settings import CONFIG_DIR

log = logging.getLogger("dewiar.servers")
WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if WIN else 0     # CREATE_NO_WINDOW: без мелькающего окна консоли на Windows
PATH = CONFIG_DIR / "servers.json"
SSH_DIR = CONFIG_DIR / "ssh"
OUT_LIMIT = 6000                         # символов вывода команды для модели — хвост, там обычно главное

# Программы, которые в принципе могут только смотреть; для части из них ниже (RO_SUB) уточнено, какие подкоманды.
READONLY = {"df", "du", "free", "uptime", "uname", "whoami", "id", "hostname", "hostnamectl", "ls", "ll", "cat", "head", "tail",
            "less", "more", "grep", "egrep", "zgrep", "awk", "wc", "sort", "uniq", "cut", "ps", "pgrep", "top", "htop",
            "vmstat", "iostat", "mpstat", "lsblk", "blkid", "findmnt", "mount", "stat", "file", "date", "timedatectl", "w",
            "who", "last", "lastlog", "dmesg", "journalctl", "ss", "netstat", "ip", "ifconfig", "ping", "dig", "nslookup",
            "host", "curl", "wget", "traceroute", "tracepath", "nproc", "lscpu", "lsmem", "lspci", "lsusb", "sensors",
            "env", "printenv", "echo", "pwd", "which", "whereis", "type", "openssl", "certbot", "nginx", "apache2ctl",
            "apachectl", "php", "python3", "node", "mysql", "psql", "redis-cli", "docker", "podman", "systemctl",
            "service", "crontab", "ufw", "iptables", "nft", "fail2ban-client", "apt", "apt-cache", "dpkg", "rpm",
            "yum", "dnf", "snap", "pip", "pip3", "git", "tree", "find", "realpath", "readlink", "md5sum", "sha256sum",
            "test", "true", "sleep", "smartctl", "zpool", "zfs", "btrfs", "pm2", "supervisorctl", "tmux", "screen"}
# У этих программ «ничего не меняет» только часть команд. Множество — допустимые первые аргументы (проверяется
# только первый); None — клиент базы, нужен явный запрос на чтение; строка — особое правило в readonly().
RO_SUB = {"systemctl": {"status", "is-active", "is-enabled", "is-failed", "list-units", "list-unit-files", "list-timers",
                        "show", "cat", "--failed"},
          "service": {"status"}, "docker": {"ps", "logs", "images", "inspect", "stats", "version", "info", "top", "port",
                                            "compose", "volume", "network"},
          "podman": {"ps", "logs", "images", "inspect", "stats", "version", "info"},
          "apt": {"list", "show", "policy", "search"}, "apt-cache": None, "dpkg": {"-l", "-L", "-s", "--list", "--status"},
          "rpm": {"-q", "-qa", "-qi", "-ql"}, "yum": {"list", "info", "check-update"}, "dnf": {"list", "info", "check-update"},
          "snap": {"list", "info"}, "pip": {"list", "show", "freeze"}, "pip3": {"list", "show", "freeze"},
          "git": {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "describe"},
          "crontab": {"-l"}, "ufw": {"status"}, "iptables": {"-L", "-S", "-nL", "-nvL"}, "nft": {"list"},
          "fail2ban-client": {"status"}, "nginx": {"-t", "-T", "-v", "-V"}, "apache2ctl": {"-t", "-S", "-M", "configtest"},
          "apachectl": {"-t", "-S", "-M", "configtest"}, "certbot": {"certificates"}, "openssl": {"s_client", "x509", "version"},
          "php": {"-v", "-m", "-i", "--version"}, "python3": {"--version", "-V"}, "node": {"--version", "-v"},
          "pm2": {"list", "ls", "status", "logs", "show", "describe"}, "supervisorctl": {"status"},
          "zpool": {"status", "list", "iostat"}, "zfs": {"list", "get"}, "btrfs": {"filesystem", "device"},
          "mysql": None, "psql": None, "redis-cli": None, "curl": "nodata", "wget": "spider", "find": "nofind",
          "tmux": {"ls", "list-sessions"}, "screen": {"-ls", "-list"}, "ip": {"a", "addr", "r", "route", "link", "-br", "-s", "-4", "-6"},
          "hostnamectl": {"status", "hostname", "--static", "--transient", "--pretty", "--json=pretty", "--json=short"},
          "timedatectl": {"status", "show", "list-timezones", "timesync-status", "show-timesync"},
          "btrfs": {"filesystem", "device", "subvolume", "qgroup"}}
# Аргументы, с которыми «смотрящая» программа всё-таки меняет сервер или запускает другое (где угодно в её аргументах).
RO_BAD_ARGS = {
    "ip": {"add", "del", "delete", "set", "change", "replace", "flush", "append", "prepend", "exec", "netns"},
    "ifconfig": {"up", "down", "add", "del", "inet", "netmask", "mtu", "hw", "promisc", "-promisc"},
    "docker": {"rm", "prune", "create", "connect", "disconnect", "run", "exec", "up", "down", "restart", "start", "stop", "kill", "pull", "push"},
    "podman": {"rm", "prune", "create", "run", "exec", "restart", "start", "stop", "kill"},
    "git": {"--output", "-o", "add", "set-url", "remove", "rm", "rename", "prune", "update", "-d", "-D", "--delete", "-m", "-M", "-c", "-C",
            "--move", "--copy", "--set-upstream-to", "--unset-upstream", "--edit-description", "-f", "--force", "--exec-path"},
    "dmesg": {"-c", "-C", "-D", "-E", "-n", "--clear", "--read-clear", "--console-off", "--console-on", "--console-level"},
    "date": {"-s", "--set"}, "hostname": {"-b", "-F", "--boot", "--file"}, "sensors": {"-s", "--set"},
    "smartctl": {"-s", "-t", "-X", "--smart", "--test", "--abort", "-o", "-S"}, "ss": {"-K", "--kill"},
    "crontab": {"-r", "-e", "-i"}, "nginx": {"-s", "-g"}, "apache2ctl": {"-k", "start", "stop", "restart", "graceful", "graceful-stop"},
    "apachectl": {"-k", "start", "stop", "restart", "graceful", "graceful-stop"},
    "iptables": {"-A", "-I", "-D", "-F", "-X", "-P", "-N", "-E", "-R", "-Z", "--append", "--insert", "--delete", "--flush", "--policy", "--zero"},
    "nft": {"add", "delete", "flush", "insert", "replace", "create", "destroy", "-f", "--file"},
    "ufw": {"enable", "disable", "allow", "deny", "reject", "delete", "reset", "limit", "route", "default", "logging", "insert"},
    "php": {"-r", "-f", "-S", "-a", "-B", "-R", "-F", "-E", "--run", "--file", "--server"},
    "python3": {"-c", "-m", "-"}, "node": {"-e", "-p", "--eval", "--print", "-r", "--require"},
    "sort": {"-o", "--output"}, "tree": {"-o"}, "less": {"-o", "-O", "--log-file", "--LOG-FILE"},
    "openssl": {"-out", "-keyout", "-outform"}, "mount": {"-o", "--options", "-t", "--types", "-a", "--all", "--bind", "-B", "-M", "--move", "-r", "-w"},
    "wget": {"--post-file", "--post-data", "--body-file", "--body-data", "--method", "-e", "--execute", "-o", "--output-file", "-a", "--append-output",
             "-i", "--input-file", "-c", "--continue", "-r", "--recursive", "-m", "--mirror"},
    "curl": {"--data-urlencode", "--data-raw", "--data-ascii", "--json", "--output", "--remote-name", "--remote-name-all", "-K", "--config",
             "-c", "--cookie-jar", "--trace", "--trace-ascii", "--dump-header", "-D", "--create-dirs"},
    "pm2": {"start", "stop", "restart", "delete", "reload", "kill", "flush", "save", "resurrect", "scale"},
    "supervisorctl": {"start", "stop", "restart", "reload", "update", "clear", "shutdown", "signal"},
    "zfs": {"set", "create", "destroy", "snapshot", "rollback", "mount", "unmount", "send", "receive", "rename"},
    "btrfs": {"resize", "defragment", "defrag", "balance", "label", "sync", "mkswapfile", "delete", "add", "create", "snapshot", "set-default",
              "remove", "replace", "scrub", "rescue", "check", "restore", "assign", "limit", "clear", "destroy", "property"},
    "zpool": {"create", "destroy", "add", "remove", "replace", "clear", "export", "import", "scrub", "set", "offline", "online"},
    "systemctl": {"--now", "kill", "set-property", "edit", "daemon-reload", "mask", "unmask", "enable", "disable", "start", "stop", "restart", "reload"},
    "service": {"start", "stop", "restart", "reload", "force-reload"},
    "awk": {"system(", "|&", "getline"},                     # system(…) и команда через канал — запуск чего угодно; proверяется по подстроке
}
# Не выполняется никогда. Шаблоны с заглавной R (chmod -R, chown -R) проверяются с учётом регистра, остальные — без.
CATASTROPHE = [
    (r"\brm\s+(-[a-z]*\s+)*-[a-z]*r[a-z]*\s+(-[a-z]+\s+)*(/|/\*|~|~/|\$HOME|/(bin|boot|etc|home|lib|lib64|opt|root|sbin|srv|usr|var)/?)(\s|;|&|$)",
     "удалить целиком корень сервера или его системную папку"),
    (r"--no-preserve-root", "удалить корень сервера"),
    (r"\bmkfs(\.\w+)?\b|\bmke2fs\b|\bwipefs\b", "отформатировать диск"),
    (r"\bdd\b[^|;&]*\bof=/dev/(sd|nvme|vd|xvd|hd|mmcblk|md)", "записать поверх диска"),
    (r">\s*/dev/(sd|nvme|vd|xvd|hd|mmcblk)\w*", "записать поверх диска"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork-бомба — сервер зависнет"),
    (r"\bchmod\s+(-[a-z]*\s+)*-[a-z]*R[a-z]*\s+[0-7]*777\s+/(\s|$)", "открыть всем весь диск сервера"),
    (r"\bchown\s+(-[a-z]*\s+)*-[a-z]*R[a-z]*\s+\S+\s+/(\s|$)", "сменить владельца всего диска сервера"),
    (r"\b(shred|srm)\b[^|;&]*/dev/", "стереть диск"),
    (r"\bcrontab\s+-r\b", "удалить все задания cron без копии"),
    (r"\buserdel\s+(-[a-z]+\s+)*root\b|\bpasswd\s+(-[a-z]+\s+)*-d\s+root\b", "сломать вход администратора"),
]
# Выполняется только после вопроса с этим предупреждением (даже при ask="changes").
WARN = [
    (r"\b(reboot|shutdown|poweroff|halt)\b|\binit\s+[06]\b|systemctl\s+(reboot|poweroff|halt)", "сервер перезагрузится или выключится"),
    (r"\brm\b[^|;&]*\s-[a-z]*r", "удалить папку целиком"),
    (r"\b(drop\s+database|drop\s+table|truncate\s+table)\b", "удалить данные в базе"),
    (r"\b(ufw\s+(deny|disable|reset)|iptables\s+-(F|P\s+INPUT\s+DROP))\b", "можно потерять доступ к серверу по сети"),
    (r"\bsystemctl\s+(stop|disable|mask)\s+(ssh|sshd)\b", "можно потерять доступ к серверу по SSH"),
    (r"\bapt(-get)?\s+(remove|purge|autoremove|dist-upgrade|full-upgrade)\b", "удалить или массово обновить программы"),
]


class ServerError(Exception):
    pass


def ssh_bin(name="ssh"):
    """Путь к ssh / ssh-keygen или None. Windows: встроенный OpenSSH лежит в System32\\OpenSSH, который бывает
    не в PATH у программы."""
    exe =shutil.which(name)
    if not exe and WIN:
        p = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / f"{name}.exe"
        exe = str(p) if p.exists() else None
    return exe


def _segments(cmd):
    """Команда → части между |, ;, &&, || (кавычки учитываются грубо — для проверки этого достаточно)."""
    out, cur, q = [], "", ""
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if q:
            cur += c
            if c == q:
                q = ""
        elif c in "'\"":
            q = c
            cur += c
        elif c in "|;&\n":
            if cur.strip():
                out.append(cur.strip())
            cur = ""
            if cmd[i:i + 2] in ("&&", "||"):
                i += 1
        else:
            cur += c
        i += 1
    if cur.strip():
        out.append(cur.strip())
    return out


def readonly(cmd):
    """Команда только смотрит (ничего не меняет на сервере)? Такие команды могут выполняться без вопроса,
    поэтому сомнение — всегда «нет»: запись в файл (>, >>, tee), подстановки $(…) и `…`, su, неразборчивые кавычки.
    Каждая часть цепочки (|, ;, &&) проверяется отдельно; префиксы sudo, nice, timeout, VAR=… пропускаются."""
    cmd = (cmd or "").strip()
    # «>» допустим только как 2>&1, >&2, 2>/dev/null, >/dev/null (иначе «cat x 2>/etc/passwd» затёр бы файл); <(…) и >(…) —
    # подстановка процесса, то есть запуск любой команды
    plain = re.sub(r"(?:[12]?>&[12]|[12]?>\s*/dev/null)", " ", cmd)
    if not cmd or ">" in plain or re.search(r"<\(|\btee\b|\$\(|`|\bsudo\s+-s\b|\bsu\b|\bxargs\b|\beval\b", cmd):
        return False
    for seg in _segments(plain):                           # без допустимых перенаправлений: «2>&1» не часть команды
        try:
            words = shlex.split(seg)
        except ValueError:
            return False
        while words and (words[0] in ("sudo", "nice", "timeout", "time") or re.match(r"^[A-Z_]+=", words[0]) or
                         (words[0].startswith("-") and len(words) > 1) or re.fullmatch(r"\d+[smh]?", words[0])):
            words = words[1:]
        if not words:
            return False
        prog = os.path.basename(words[0])
        if prog not in READONLY:
            return False
        if prog == "journalctl" and any(a.startswith(("--vacuum", "--rotate", "--flush", "--sync", "--relinquish")) for a in words):
            return False
        if prog in ("echo", "printenv", "env") and len(words) > 1 and prog == "env":
            return False                                   # env с аргументами запускает другую программу
        rule = RO_SUB.get(prog, "any")
        args = [w for w in words[1:]]
        bad = RO_BAD_ARGS.get(prog)
        if bad and (prog == "awk" and any(b in seg for b in bad) or prog != "awk" and
                    any(a in bad or a.split("=")[0] in bad for a in args)):
            return False                                   # ip route del, docker volume rm, sort -o, date -s, awk system(…)
        if prog in ("mount", "hostname", "ifconfig", "uniq") and len([a for a in args if not a.startswith("-")]) > (1 if prog in ("ifconfig", "uniq") else 0):
            return False                                   # mount /dev/sdb1 /mnt, hostname новое-имя, ifconfig eth0 down, uniq вход выход
        if rule is None:                                   # клиенты баз — только с явным запросом SELECT/SHOW
            joined = " ".join(args).lower()
            if not re.search(r"\b(select|show|describe|explain|info|ping|\\l|\\dt)\b", joined) or \
                    re.search(r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|flushall|flushdb|set|del|copy|into|outfile|"
                              r"dumpfile|load|call|do|execute|exec|terminate|cancel|lo_export|lo_import|system|shutdown|kill|rename|replace|"
                              r"merge|vacuum|reindex|cluster|lock|unload|notify|listen|refresh|eval|script|config|debug|migrate|restore|"
                              r"bgsave|save|slaveof|replicaof|monitor|append|incr|decr|lpush|rpush|sadd|hset|publish)\b", joined) or \
                    re.search(r"\\(?:!|(?:copy|o|w|i|ir|e|g|gx|gset|include|edit|out|write)\b)", joined):
                return False                               # запись в файл (copy … to, into outfile, \o), запуск (\!, system), изменения
        elif rule == "nodata":                             # curl: без отправки данных и записи в файл
            if any(a in ("-d", "--data", "--data-binary", "-F", "--form", "-T", "--upload-file", "-o", "-O", "-X") for a in args):
                return False
        elif rule == "spider":                             # wget: только проверка или вывод на экран, без файла
            if "--spider" not in args and "-O-" not in args and "-qO-" not in args:
                return False
        elif rule == "nofind":                             # find: без удаления и запуска команд
            if any(a in ("-delete", "-exec", "-execdir", "-ok", "-fprint", "-fls") for a in args):
                return False
        elif isinstance(rule, set):
            first = next((a for a in args if not a.startswith("--no-pager") and a not in ("--user", "-a", "--all")), "")
            if prog == "docker" and first == "compose":
                if not set(args) & {"ps", "logs", "config", "ls", "images", "top"}:
                    return False
            elif first not in rule and not (prog in ("ip", "timedatectl", "hostnamectl") and not args):   # голые ip, timedatectl, hostnamectl — только показ
                return False
    return True


def check(cmd):
    """Оценка команды для сервера -> {"level": "ok" | "warn" | "block", "reason": "…", "readonly": bool}.
    block — не выполнять никогда, warn — спросить с предупреждением, ok — обычный вопрос (или без него, если readonly
    и для сервера выбрано ask="changes")."""
    c = (cmd or "").strip()
    low = c.lower()
    for rx, why in CATASTROPHE:
        if re.search(rx, low if "R" not in rx else c):
            return {"level": "block", "reason": why, "readonly": False}
    for rx, why in WARN:
        if re.search(rx, low):
            return {"level": "warn", "reason": why, "readonly": False}
    return {"level": "ok", "reason": "", "readonly": readonly(c)}


def _slug(name):
    s = re.sub(r"[^\w-]+", "-", (name or "").lower()).strip("-")
    return s[:40] or secrets.token_hex(3)


class Servers:
    """Список серверов (servers.json: id, name, host, user, port, aliases, notes, ask, fingerprint…), ключ Dewiar
    и запуск команд. Паролей и закрытых ключей в servers.json нет."""

    def __init__(self, path=PATH, ssh_dir=SSH_DIR):
        self.path, self.dir = Path(path), Path(ssh_dir)
        self._lock = threading.Lock()
        self.items = self._load()

    # ---------- хранение ----------

    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d if isinstance(d, list) else []
        except (OSError, ValueError):
            return []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def public(self):
        return {"servers": [dict(x) for x in self.items], "key": self.pubkey(), "ssh": bool(ssh_bin()),
                "keygen": bool(ssh_bin("ssh-keygen"))}

    def find(self, name):
        """Сервер по словам человека или модели: точно по id, имени или адресу, затем по вхождению имени или
        другого названия (aliases). Пустое имя — единственный сервер, если он один."""
        q =(name or "").strip().lower().replace("ё", "е")
        if not q:
            return self.items[0] if len(self.items) == 1 else None
        for x in self.items:
            if q in (x["id"], x["name"].lower().replace("ё", "е"), x["host"].lower()):
                return x
        for x in self.items:
            words = [x["name"].lower().replace("ё", "е")] + [w.lower() for w in x.get("aliases") or []]
            if any(q in w or w in q for w in words if w):
                return x
        return None

    def names(self):
        return ", ".join(f"«{x['name']}» ({x['user']}@{x['host']})" for x in self.items)

    # ---------- ключ Dewiar ----------

    @property
    def key_path(self):
        return self.dir / "id_ed25519"

    def pubkey(self):
        try:
            return (self.dir / "id_ed25519.pub").read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def ensure_key(self):
        """Ключ Dewiar (ed25519, без парольной фразы — ssh работает без человека) -> открытая часть.
        Создаётся один раз; папка ssh/ — 0700, закрытый ключ — только владельцу."""
        if self.key_path.exists() and self.pubkey():
            return self.pubkey()
        kg = ssh_bin("ssh-keygen")
        if not kg:
            raise ServerError("нет программы ssh-keygen — " + ("включите «Клиент OpenSSH» в параметрах Windows "
                              "(Приложения → Дополнительные компоненты)" if WIN else "установите: sudo apt install openssh-client"))
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        r = subprocess.run([kg, "-q", "-t", "ed25519", "-N", "", "-C", f"dewiar@{socket.gethostname()}", "-f", str(self.key_path)],
                           capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise ServerError(f"ключ не создался: {(r.stderr or r.stdout).strip()[:300]}")
        self._protect(self.key_path)
        log.info("🔑 Ключ Dewiar для серверов создан")
        return self.pubkey()

    @staticmethod
    def _protect(p):
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        if WIN:                                      # OpenSSH для Windows не берёт ключ, который видят другие
            user = os.environ.get("USERNAME", "")
            subprocess.run(["icacls", str(p), "/inheritance:r", "/grant:r", f"{user}:F"], capture_output=True,
                           creationflags=NO_WINDOW)

    # ---------- ssh ----------

    def _base(self, s, batch=True, accept_new=False):
        """Начало команды ssh для сервера s. Только свой known_hosts (системный отключён): отпечаток сменился —
        отказ. accept_new — запомнить отпечаток при первом входе. batch — вход только ключом, без вопросов
        (иначе ssh повис бы, ожидая пароль). Путь known_hosts в кавычках: в нём бывают пробелы (Windows)."""
        exe = ssh_bin()
        if not exe:
            raise ServerError("нет программы ssh — " + ("включите «Клиент OpenSSH» в параметрах Windows "
                              "(Приложения → Дополнительные компоненты)" if WIN else "установите: sudo apt install openssh-client"))
        self.dir.mkdir(parents=True, exist_ok=True)
        kh = self.dir / "known_hosts"
        args = [exe, "-p", str(int(s.get("port") or 22)), "-o", f'UserKnownHostsFile="{kh}"', "-o", "GlobalKnownHostsFile=/dev/null" if not WIN else "GlobalKnownHostsFile=NUL",
                "-o", f"StrictHostKeyChecking={'accept-new' if accept_new else 'yes'}", "-o", "ConnectTimeout=12",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "-o", "LogLevel=ERROR", "-T"]
        if batch:
            kp = Path(s.get("keyfile") or self.key_path)
            if not WIN and kp.exists() and kp.stat().st_mode & 0o077:
                self._protect(kp)                    # ключ из резервной копии — права «только владелец», иначе ssh его не возьмёт
            args += ["-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-i", str(Path(s.get("keyfile") or self.key_path))]
        return args + [f"{s['user']}@{s['host']}"]

    def _run(self, args, timeout, stdin=None, env=None):
        """Запустить ssh -> (код, stdout, stderr). Не уложился — ServerError с тем, что команда успела вывести."""
        try:
            r = subprocess.run(args, input=stdin, capture_output=True, timeout=timeout, creationflags=NO_WINDOW, env=env,
                               stdin=None if stdin is not None else subprocess.DEVNULL)
        except subprocess.TimeoutExpired as e:
            out = ((e.stdout or b"") + (e.stderr or b"")).decode("utf-8", "replace")
            raise ServerError(f"команда не уложилась в {timeout} с — остановлена" + (f". Успела вывести:\n{out[-2000:]}" if out else ""))
        except OSError as e:
            raise ServerError(f"ssh не запустился: {e}") from e
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")

    @staticmethod
    def _human_err(err, code):
        """Ошибка ssh -> понятный человеку текст с тем, что делать."""
        e =(err or "").strip()
        if "REMOTE HOST IDENTIFICATION HAS CHANGED" in e or "Host key verification failed" in e:
            return ("отпечаток сервера изменился с прошлого раза — возможна подмена сервера. Если сервер переустанавливали, "
                    "удалите его в студии («Серверы») и добавьте заново")
        if "Permission denied" in e:
            return "сервер не пустил по ключу Dewiar — поставьте ключ заново (студия → «Серверы» → «Поставить ключ»)"
        if "Could not resolve hostname" in e or "Name or service not known" in e:
            return "не нашёл такой адрес сервера — проверьте имя"
        if "Connection refused" in e:
            return "сервер отказал в подключении — SSH не запущен или другой порт"
        if "timed out" in e.lower():
            return "сервер не отвечает (выключен, другой порт или закрыт брандмауэром)"
        return e[-400:] or f"ssh завершился с кодом {code}"

    def run(self, name, command, timeout=60):
        """Выполнить команду на сервере -> {server, code, output}. Подтверждение спрашивают раньше (tools.py);
        здесь — последняя защита: CATASTROPHE не выполняется. Таймаут 5–600 с. Код 255 без вывода — ошибка самого ssh."""
        s = self.find(name)
        if not s:
            raise ServerError(f"нет сервера «{name}». Есть: {self.names() or 'ни одного — добавьте в студии, «Серверы»'}")
        command = (command or "").strip()
        if not command:
            raise ServerError("пустая команда")
        v = check(command)
        if v["level"] == "block":
            raise ServerError(f"защита Dewiar не выполнит это на сервере: {v['reason']}")
        timeout = max(5, min(int(timeout or 60), 600))
        code, out, err = self._run(self._base(s) + [command], timeout)
        if code == 255 and not out:
            raise ServerError(self._human_err(err, code))
        text = (out + (("\n" + err) if err.strip() else "")).strip()
        if len(text) > OUT_LIMIT:
            text = "…(начало вывода пропущено)\n" + text[-OUT_LIMIT:]
        with self._lock:
            s["last"] = time.strftime("%Y-%m-%d %H:%M")
            self._save()
        log.info("🖥 %s: %s → код %s", s["name"], command[:120], code)
        return {"server": s["name"], "code": code, "output": text}

    # ---------- добавить / проверить / убрать ----------

    def add(self, name, host, user, port=22, password="", keyfile="", aliases=None, notes=""):
        """Добавить сервер -> его запись. Понимает «user@host» и «host:порт». С паролем — сначала кладёт ключ Dewiar
        (install_key), затем проверяет вход по ключу и запоминает отпечаток. Тот же host+user+port заменяется.
        Адрес и пользователь проверяются строго: они попадают в командную строку ssh."""
        name, host, user = (name or "").strip(), (host or "").strip(), (user or "").strip()
        if "@" in host and not user:
            user, host = host.split("@", 1)
        m = re.match(r"^(.+):(\d+)$", host)
        if m and not port:
            host, port = m.group(1), int(m.group(2))
        if not host or not user:
            raise ServerError("нужны адрес сервера и пользователь (например, root или admin)")
        if not re.fullmatch(r"[\w.\-]+|\[[0-9a-fA-F:]+\]", host) or not re.fullmatch(r"[\w.\-]+", user):
            raise ServerError("адрес или пользователь с недопустимыми символами")
        name = name or host
        s = {"id": _slug(name), "name": name, "host": host, "user": user, "port": int(port or 22),
             "aliases": [a.strip() for a in (aliases or []) if a.strip()], "notes": (notes or "").strip(),
             "ask": "all", "added": time.strftime("%Y-%m-%d %H:%M")}
        if keyfile:
            kp = Path(os.path.expanduser(keyfile))
            if not kp.is_file():
                raise ServerError(f"нет файла ключа {keyfile}")
            s["keyfile"] = str(kp)
        else:
            self.ensure_key()
        if any(x["id"] == s["id"] for x in self.items):
            s["id"] = s["id"] + "-" + secrets.token_hex(2)
        if password and not keyfile:
            self.install_key(s, password)
        info = self.test(s)
        with self._lock:
            self.items = [x for x in self.items if not (x["host"] == s["host"] and x["user"] == s["user"] and x["port"] == s["port"])]
            s["fingerprint"] = info.get("fingerprint", "")
            s["system"] = info.get("system", "")
            self.items.append(s)
            self._save()
        return s

    def install_key(self, s, password):
        """Положить ключ Dewiar на сервер: один вход по паролю. Пароль передаётся ssh через SSH_ASKPASS: маленький
        скрипт во временной папке печатает его из переменной окружения; на диск пароль не пишется, папка удаляется.
        SSH_ASKPASS_REQUIRE=force и DISPLAY — иначе ssh без терминала не спросит askpass. Ключ дописывается
        в authorized_keys, только если его там ещё нет."""
        pub = self.ensure_key()
        helper_dir = Path(tempfile.mkdtemp(prefix="dwssh"))
        try:
            py = helper_dir / "askpass.py"
            py.write_text("import os, sys\nsys.stdout.write(os.environ.get('DEWIAR_SSH_PASS', '') + '\\n')\n", encoding="utf-8")
            if WIN:
                ask = helper_dir / "askpass.cmd"
                pyw = Path(sys.executable)
                ask.write_text(f'@"{pyw}" "{py}"\r\n', encoding="utf-8")
            else:
                ask = helper_dir / "askpass.sh"
                ask.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{py}"\n', encoding="utf-8")
                os.chmod(ask, 0o700)
            env = dict(os.environ, SSH_ASKPASS=str(ask), SSH_ASKPASS_REQUIRE="force", DISPLAY=os.environ.get("DISPLAY") or ":0",
                       DEWIAR_SSH_PASS=password)
            args = self._base(s, batch=False, accept_new=True)
            args[1:1] = ["-o", "PreferredAuthentications=password,keyboard-interactive", "-o", "PubkeyAuthentication=no",
                         "-o", "NumberOfPasswordPrompts=1"]
            remote = ("umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && "
                      f"(grep -qxF {shlex.quote(pub)} ~/.ssh/authorized_keys || echo {shlex.quote(pub)} >> ~/.ssh/authorized_keys) && echo DEWIAR-KEY-OK")
            code, out, err = self._run(args + [remote], 40, env=env)
        finally:
            shutil.rmtree(helper_dir, ignore_errors=True)
        if "DEWIAR-KEY-OK" not in out:
            if "Permission denied" in err:
                raise ServerError("сервер не принял пароль (или вход по паролю на нём выключен — тогда добавьте ключ вручную: "
                                  "он показан в студии, строку нужно дописать в ~/.ssh/authorized_keys на сервере)")
            raise ServerError(self._human_err(err, code))
        log.info("🔑 Ключ Dewiar поставлен на %s@%s", s["user"], s["host"])

    def test(self, s):
        """Войти по ключу и узнать систему; отпечаток сервера запоминается при первом входе."""
        code, out, err = self._run(self._base(s, accept_new=True) +
                                   ["echo DEWIAR-OK; (cat /etc/os-release 2>/dev/null | grep ^PRETTY_NAME= | cut -d= -f2 | tr -d '\"') "
                                    "|| uname -sr; uptime -p 2>/dev/null || uptime"], 30)
        if "DEWIAR-OK" not in out:
            raise ServerError(self._human_err(err, code))
        lines = [x for x in out.splitlines() if x.strip() and x.strip() != "DEWIAR-OK"]
        return {"ok": True, "system": lines[0] if lines else "", "uptime": lines[1] if len(lines) > 1 else "",
                "fingerprint": self._fingerprint(s)}

    def _fingerprint(self, s):
        """Отпечаток сервера (SHA256:…) из нашего known_hosts — для показа в студии; "" — если не узнали.
        Нестандартный порт в known_hosts записывается как [host]:port."""
        kg =ssh_bin("ssh-keygen")
        kh = self.dir / "known_hosts"
        if not kg or not kh.exists():
            return ""
        h = s["host"] if int(s.get("port") or 22) == 22 else f"[{s['host']}]:{s['port']}"
        r = subprocess.run([kg, "-l", "-F", h, "-f", str(kh)], capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW)
        m = re.search(r"(SHA256:\S+)", r.stdout or "")
        return m.group(1) if m else ""

    def check_one(self, sid):
        s = next((x for x in self.items if x["id"] == sid), None)
        if not s:
            raise ServerError("нет такого сервера")
        info = self.test(s)
        with self._lock:
            s["system"] = info.get("system") or s.get("system", "")
            s["last"] = time.strftime("%Y-%m-%d %H:%M")
            self._save()
        return info

    def update(self, sid, patch):
        """Изменить имя, другие названия, заметки и правило вопросов (ask: all — спрашивать всё, changes — только
        изменения). Адрес и пользователя не меняем: для этого сервер добавляют заново (с новым отпечатком)."""
        with self._lock:
            s = next((x for x in self.items if x["id"] == sid), None)
            if not s:
                raise ServerError("нет такого сервера")
            if "name" in patch and str(patch["name"]).strip():
                s["name"] = str(patch["name"]).strip()
            if "aliases" in patch:
                al = patch["aliases"]
                s["aliases"] = [a.strip() for a in (al.split(",") if isinstance(al, str) else al or []) if a.strip()]
            if "notes" in patch:
                s["notes"] = str(patch["notes"] or "").strip()
            if patch.get("ask") in ("all", "changes"):
                s["ask"] = patch["ask"]
            self._save()
            return dict(s)

    def remove(self, sid):
        with self._lock:
            s = next((x for x in self.items if x["id"] == sid), None)
            self.items = [x for x in self.items if x["id"] != sid]
            self._save()
        if s:                                        # забыть отпечаток — добавить заново можно будет с нуля
            kg = ssh_bin("ssh-keygen")
            kh = self.dir / "known_hosts"
            if kg and kh.exists():
                h = s["host"] if int(s.get("port") or 22) == 22 else f"[{s['host']}]:{s['port']}"
                subprocess.run([kg, "-R", h, "-f", str(kh)], capture_output=True, timeout=10, creationflags=NO_WINDOW)
        return self.public()
