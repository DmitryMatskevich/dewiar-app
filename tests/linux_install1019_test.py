"""Установка на любой Linux (вопросы 19.10: «Только на Ubuntu встаёт? У меня Omarchy на арче», «install on altlinux»):
install.sh в корне — узнаёт систему по /etc/os-release и ставит её менеджером пакетов GTK, WebKit2GTK, PyGObject и звук,
скачивает тот же .deb (sha256 по latest.json) и раскладывает его без dpkg (ar + tar), запускает его postinst;
--remove убирает разложенное; в пакете лежит сам install.sh — им программа и обновляется там, где нет dpkg.
Проверяем: выбор команды для Arch/Omarchy, Fedora, openSUSE, ALT, Void, Alpine (отказ), Ubuntu (через apt); раскладку
настоящего пакета в папку и удаление."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


tmp = Path(tempfile.mkdtemp())
SH = ROOT / "install.sh"
ok(subprocess.run(["bash", "-n", str(SH)]).returncode == 0, "install.sh без синтаксических ошибок")


def dry(osrel, *extra):
    f = tmp / "os-release"; f.write_text(osrel, encoding="utf-8")
    r = subprocess.run(["bash", str(SH), "--dry-run", "--yes", *extra], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, DEWIAR_OS_RELEASE=str(f), DEWIAR_ROOT_DIR=str(tmp / "dry")))
    return r.returncode, r.stdout + r.stderr


cases = {
    'NAME="Omarchy"\nID=omarchy\nID_LIKE=arch\nPRETTY_NAME="Omarchy"\n': ("pacman -S --needed", "webkit2gtk-4.1", "python-gobject"),
    'NAME="Arch Linux"\nID=arch\nPRETTY_NAME="Arch Linux"\n': ("pacman -S --needed", "webkit2gtk-4.1"),
    'NAME="Fedora Linux"\nID=fedora\nPRETTY_NAME="Fedora Linux 42"\n': ("dnf install -y", "webkit2gtk4.1", "python3-gobject"),
    'NAME="openSUSE Tumbleweed"\nID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n': ("zypper --non-interactive install", "typelib-1_0-WebKit2-4_1"),
    'NAME="ALT Workstation"\nID=altlinux\nPRETTY_NAME="ALT Workstation 11"\n': ("apt-get install -y", "python3-module-pygobject3"),
    'NAME="Void"\nID=void\n': ("xbps-install -Sy", "webkit2gtk"),
}
for osrel, want in cases.items():
    rc, out = dry(osrel)
    name = osrel.split("\n")[0]
    ok(rc == 0 and all(w in out for w in want), f"{name}: команда менеджера пакетов — {[w for w in want if w not in out] or 'верная'}")
rc, out = dry('NAME="Alpine Linux"\nID=alpine\n')
ok(rc != 0 and "musl" in out, "Alpine (musl): честный отказ с причиной")
rc, out = dry('NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\n', "--no-deps")
ok(rc == 0 and "(пакеты: deb)" in out, "Ubuntu/Debian: ставится обычным пакетом через apt")

# настоящий пакет — раскладка без dpkg и удаление
if shutil.which("dpkg-deb"):
    deb = subprocess.run(["bash", str(ROOT / "tools/build_deb.sh"), str(tmp / "dist")], capture_output=True, text=True).stdout.strip().splitlines()[-1]
    root = tmp / "root"
    f = tmp / "os-release"; f.write_text('NAME="Arch Linux"\nID=arch\n', encoding="utf-8")
    env = dict(os.environ, DEWIAR_OS_RELEASE=str(f), DEWIAR_ROOT_DIR=str(root))
    r = subprocess.run(["bash", str(SH), "--yes", "--no-deps", "--no-postinst", "--deb", deb], capture_output=True, text=True, timeout=300, env=env)
    files = (root / "opt/dewiar/.installed-files").read_text(encoding="utf-8").split() if (root / "opt/dewiar/.installed-files").exists() else []
    ok(r.returncode == 0 and (root / "opt/dewiar/core/main.py").is_file() and (root / "usr/bin/dewiar").is_file()
       and (root / "usr/share/applications/dewiar.desktop").is_file() and os.access(root / "usr/bin/dewiar", os.X_OK),
       f"пакет разложен без dpkg: /opt/dewiar, запуск dewiar, меню приложений: {r.stdout[-300:]}{r.stderr[-300:]}")
    ok("/usr/bin/dewiar" in files and (root / "opt/dewiar/install.sh").is_file(), "список файлов для удаления; install.sh — внутри (им и обновляется)")
    app_src = (ROOT / "ui/app.py").read_text(encoding="utf-8")
    ok('"/opt/dewiar/install.sh", "--yes", "--no-deps", "--deb"' in app_src and 'shutil.which("dpkg")' in app_src,
       "обновление из программы там, где нет dpkg, — через install.sh")
    r = subprocess.run(["bash", str(SH), "--remove"], capture_output=True, text=True, timeout=120, env=env)
    ok(r.returncode == 0 and not (root / "opt/dewiar").exists() and not (root / "usr/bin/dewiar").exists()
       and not (root / "usr/share/applications/dewiar.desktop").exists(), f"--remove убирает разложенное: {r.stdout[-200:]}{r.stderr[-200:]}")
else:
    print("нет dpkg-deb — раскладку пакета не проверяю")
shutil.rmtree(tmp, ignore_errors=True)
print("\nИТОГ:", "всё прошло" if not fails else f"не прошло {len(fails)}")
sys.exit(1 if fails else 0)
