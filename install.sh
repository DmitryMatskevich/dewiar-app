#!/bin/bash
# Dewiar на любом Linux — не только Ubuntu: Arch, Manjaro, Omarchy, EndeavourOS, Fedora, openSUSE, ALT, Void, Debian, Mint…
# (вопросы 19.10: «На линуксе только на Ubuntu встаёт? У меня Omarchy на арче», «install on altlinux»).
#
#   curl -fsSL https://raw.githubusercontent.com/dwr-git/dewiar-app/main/install.sh | bash
#   bash install.sh [--yes] [--no-deps] [--deb ФАЙЛ] [--remove] [--dry-run]
#
# Что делает: 1) ставит системные библиотеки окна и звука (GTK 3, WebKit2GTK, PyGObject, PulseAudio/PipeWire) менеджером
# пакетов вашей системы — покажет команду и спросит; 2) скачивает тот же пакет, что для Ubuntu (dewiar-assistant_all.deb,
# с dewiar.pro с проверкой sha256, запасной — GitHub) и раскладывает его файлы по тем же местам (/opt/dewiar, меню
# приложений, значки); 3) готовит окружение Python (/opt/dewiar/dewiar-env — библиотеки качает uv) и модель имён.
# На Debian, Ubuntu, Mint и других с dpkg — просто ставит пакет через apt (зависимости и обновления — как обычно).
# Удалить: bash install.sh --remove (ваши настройки и заметки остаются в ~/.config/dewiar и ~/Документы/Dewiar).
set -euo pipefail

YES=0; DEPS=1; DEB=""; REMOVE=0; DRY=0; POST=1
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) YES=1 ;;
    --no-deps) DEPS=0 ;;
    --no-postinst) POST=0 ;;
    --deb) DEB="$2"; shift ;;
    --remove|--uninstall) REMOVE=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,15p' "$0" 2>/dev/null || true; exit 0 ;;
    *) echo "Не понял параметр: $1 (есть --yes, --no-deps, --deb ФАЙЛ, --remove, --dry-run)"; exit 2 ;;
  esac
  shift
done

ROOTDIR="${DEWIAR_ROOT_DIR:-}"            # проверки: ставить не в /, а в эту папку
OSREL="${DEWIAR_OS_RELEASE:-/etc/os-release}"
LATEST="${DEWIAR_LATEST:-https://dewiar.pro/dwrapp/latest.json}"
GH="https://github.com/dwr-git/dewiar-app/releases/latest/download/dewiar-assistant_all.deb"
say()  { printf '\033[1;35mDewiar:\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mDewiar:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mDewiar:\033[0m %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -eq 0 ] || [ -n "$ROOTDIR" ]; then SUDO=""; else
  command -v sudo >/dev/null 2>&1 && SUDO="sudo" || { command -v doas >/dev/null 2>&1 && SUDO="doas" || die "нужны права администратора: запустите от root или поставьте sudo"; }
fi
command -v python3 >/dev/null 2>&1 || NO_PY=1

# ---------- какая система ----------
ID=""; ID_LIKE=""; NAME="Linux"
[ -r "$OSREL" ] && . "$OSREL" || true
LIKE=" ${ID:-} ${ID_LIKE:-} "
pm() {
  [ -n "${DEWIAR_FORCE_PM:-}" ] && { echo "$DEWIAR_FORCE_PM"; return; }
  case "$LIKE" in                                     # сначала — что за система (os-release), потом — какие команды есть
    *" altlinux "*|*" alt "*) echo alt; return ;;
    *" arch "*|*" archlinux "*|*" manjaro "*|*" endeavouros "*|*" omarchy "*|*" cachyos "*|*" garuda "*) echo pacman; return ;;
    *" fedora "*|*" rhel "*|*" centos "*|*" rocky "*|*" almalinux "*|*" nobara "*) echo dnf; return ;;
    *" suse "*|*" opensuse "*|*" opensuse-tumbleweed "*|*" opensuse-leap "*) echo zypper; return ;;
    *" void "*) echo xbps; return ;;
    *" alpine "*) echo apk; return ;;
    *" debian "*|*" ubuntu "*|*" linuxmint "*|*" pop "*|*" elementary "*|*" astra "*|*" kali "*|*" zorin "*)
      command -v dpkg >/dev/null 2>&1 && { echo deb; return; } ;;
  esac
  command -v dpkg >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1 && ! command -v rpm >/dev/null 2>&1 && { echo deb; return; }
  for p in pacman dnf zypper xbps-install apk; do command -v "$p" >/dev/null 2>&1 && { echo "${p%-install}"; return; }; done
  echo unknown
}
PM="$(pm)"

# ---------- удалить ----------
if [ "$REMOVE" = 1 ]; then
  if [ "$PM" = deb ] && dpkg-query -W -f='${Status}' dewiar-assistant 2>/dev/null | grep -q "ok installed"; then
    say "удаляю пакет dewiar-assistant (настройки и заметки остаются)"; [ "$DRY" = 1 ] || $SUDO apt-get remove -y dewiar-assistant; exit 0
  fi
  LIST="$ROOTDIR/opt/dewiar/.installed-files"
  [ -f "$LIST" ] || die "Dewiar, поставленный этим скриптом, не найден (нет $LIST)"
  if [ -z "$ROOTDIR" ]; then                       # проверка (папка вместо /) — службу пользователя не трогаем
    systemctl --user disable --now dewiar-core.service >/dev/null 2>&1 || true
    rm -f "$HOME/.config/systemd/user/dewiar-core.service" 2>/dev/null || true
  fi
  say "удаляю файлы Dewiar (настройки ~/.config/dewiar и заметки остаются)"
  if [ "$DRY" = 0 ]; then
    $SUDO python3 - "$LIST" "$ROOTDIR" <<'PY'
import os, shutil, sys
lst, root = sys.argv[1], sys.argv[2]
files = [l.rstrip("\n") for l in open(lst, encoding="utf-8") if l.strip()]
for f in sorted(files, key=len, reverse=True):
    p = root + f
    if p.startswith(root + "/opt/dewiar"):
        continue
    try:
        if os.path.islink(p) or os.path.isfile(p):
            os.remove(p)
    except OSError:
        pass
shutil.rmtree(root + "/opt/dewiar", ignore_errors=True)
PY
  fi
  say "готово — Dewiar удалён"; exit 0
fi

say "система: ${PRETTY_NAME:-$NAME} (пакеты: $PM)"
[ "${NO_PY:-0}" = 1 ] && [ "$PM" != deb ] && warn "нет python3 — поставлю его вместе с остальным"

# ---------- системные библиотеки (окно, звук) ----------
case "$PM" in
  pacman) PKGS="python python-gobject gtk3 webkit2gtk-4.1 libpulse libnotify"; OPT="playerctl ffmpeg espeak-ng libayatana-appindicator"
          CMD="pacman -S --needed --noconfirm" ;;
  dnf)    PKGS="python3 python3-gobject gtk3 webkit2gtk4.1 pulseaudio-libs libnotify"; OPT="playerctl ffmpeg-free espeak-ng libayatana-appindicator-gtk3"
          CMD="dnf install -y" ;;
  zypper) PKGS="python3 python3-gobject python3-gobject-Gdk typelib-1_0-Gtk-3_0 typelib-1_0-WebKit2-4_1 libpulse0 libnotify-tools"; OPT="playerctl ffmpeg espeak-ng"
          CMD="zypper --non-interactive install" ;;
  alt)    PKGS="python3 python3-module-pygobject3 libgtk+3-gir libwebkit2gtk4.1-gir libpulseaudio libnotify"; OPT="playerctl ffmpeg espeak-ng"
          CMD="apt-get install -y" ;;
  xbps)   PKGS="python3 python3-gobject gtk+3 webkit2gtk libpulseaudio libnotify"; OPT="playerctl ffmpeg espeak-ng"
          CMD="xbps-install -Sy" ;;
  deb)    PKGS=""; OPT=""; CMD="" ;;
  apk)    die "Alpine (musl) не поддерживается: библиотеки Python для Dewiar собраны под glibc. Подойдёт любой дистрибутив на glibc." ;;
  *)      PKGS=""; OPT=""; CMD="" ;;
esac
if [ "$DEPS" = 1 ] && [ -n "$CMD" ]; then
  say "нужны системные библиотеки окна и звука:"
  echo "    $SUDO $CMD $PKGS"
  if [ "$DRY" = 0 ]; then
    if [ "$YES" = 0 ] && [ -t 0 ]; then read -r -p "Поставить? [Y/n] " a; case "$a" in [nN]*) die "без них окно Dewiar не откроется — остановился" ;; esac; fi
    $SUDO $CMD $PKGS || warn "часть пакетов не поставилась — проверю, хватает ли того, что есть"
    $SUDO $CMD $OPT >/dev/null 2>&1 || warn "необязательные (playerctl, ffmpeg, espeak-ng, значок в трее) поставились не все — Dewiar работает и без них"
  fi
elif [ "$PM" = unknown ] && [ "$DEPS" = 1 ]; then
  warn "не узнал менеджер пакетов. Поставьте сами: Python 3.10+, PyGObject, GTK 3, WebKit2GTK 4.1 (или 4.0), libpulse — и запустите снова с --no-deps"
fi

# ---------- проверка: есть ли окно ----------
if [ "$PM" != deb ] && [ "$DRY" = 0 ] && [ -z "$ROOTDIR" ]; then
  python3 - <<'PY' || die "нет GTK/WebKit для Python (PyGObject + WebKit2GTK 4.1 или 4.0) — без них окно Dewiar не откроется. Поставьте их менеджером пакетов и запустите снова."
import gi
gi.require_version("Gtk", "3.0")
for v in ("4.1", "4.0"):
    try:
        gi.require_version("WebKit2", v); break
    except ValueError:
        pass
else:
    raise SystemExit(1)
PY
fi

# ---------- пакет ----------
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
if [ -z "$DEB" ]; then
  say "скачиваю Dewiar…"
  [ "$DRY" = 1 ] && { say "(проверка: дальше — скачать, разложить, подготовить окружение)"; exit 0; }
  python3 - "$LATEST" "$GH" "$TMP" <<'PY' || die "не скачался пакет Dewiar — проверьте интернет (нужны dewiar.pro или github.com)"
import hashlib, json, os, sys, urllib.parse, urllib.request
latest, gh, tmp = sys.argv[1:4]
want, name, url = "", "dewiar-assistant_all.deb", gh
try:
    with urllib.request.urlopen(latest, timeout=20) as r:
        L = json.load(r)
    if L.get("deb"):
        name, want = L["deb"], L.get("sha256", "")
        url = urllib.parse.urljoin(latest, L["deb"])
except Exception as e:
    print(f"   latest.json недоступен ({e}) — беру с GitHub", file=sys.stderr)
for u in [url] + ([gh] if url != gh else []):
    try:
        h, dst = hashlib.sha256(), os.path.join(tmp, "dewiar.deb")
        with urllib.request.urlopen(u, timeout=60) as r, open(dst, "wb") as f:
            while True:
                b = r.read(1 << 16)
                if not b:
                    break
                f.write(b); h.update(b)
        if want and h.hexdigest() != want:
            if u == gh:
                print("   на GitHub, видимо, версия новее, чем на сайте — ставлю её (сумму сверить не с чем)", file=sys.stderr)
            else:
                print("   контрольная сумма не совпала — пробую другой адрес", file=sys.stderr); continue
        print("   " + u.rsplit("/", 1)[-1], file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"   {u}: {e}", file=sys.stderr)
sys.exit(1)
PY
  DEB="$TMP/dewiar.deb"
fi
[ -f "$DEB" ] || die "нет файла пакета: $DEB"

if [ "$PM" = deb ] && [ -z "$ROOTDIR" ]; then
  say "ставлю пакет через apt (зависимости — как обычно)"
  [ "$DRY" = 1 ] || { cp "$DEB" "$TMP/dewiar-assistant.deb"; $SUDO apt-get install -y "$TMP/dewiar-assistant.deb"; }
  say "готово. Откройте «Dewiar» в меню приложений или выполните: dewiar"; exit 0
fi

# ---------- разложить файлы пакета (без dpkg: формат ar + tar) ----------
say "раскладываю файлы в /opt/dewiar и меню приложений"
$SUDO python3 - "$DEB" "${ROOTDIR:-/}" "$TMP" <<'PY'
import io, os, sys, tarfile
deb, root, tmp = sys.argv[1:4]
data = open(deb, "rb").read()
if not data.startswith(b"!<arch>\n"):
    sys.exit("это не пакет .deb")
i, parts = 8, {}
while i + 60 <= len(data):
    hdr = data[i:i + 60]
    name = hdr[:16].decode().strip().rstrip("/")
    size = int(hdr[48:58].decode().strip())
    parts[name] = data[i + 60:i + 60 + size]
    i += 60 + size + (size & 1)
def tar_of(prefix):
    for n, b in parts.items():
        if n.startswith(prefix):
            return tarfile.open(fileobj=io.BytesIO(b), mode="r:*")
    sys.exit(f"в пакете нет {prefix}.tar (сжатие zstd этот скрипт не читает)")
files = []
with tar_of("data.tar") as t:
    members = []
    for m in t.getmembers():
        n = os.path.normpath(m.name.lstrip("./"))
        if n.startswith("..") or os.path.isabs(n) or n == ".":
            continue
        m.name = n
        members.append(m)
        if not m.isdir():
            files.append("/" + n)
    kw = {"filter": "tar"} if hasattr(tarfile, "data_filter") else {}
    t.extractall(root, members=members, **kw)
with tar_of("control.tar") as t:
    for m in t.getmembers():
        if os.path.basename(m.name) == "postinst":
            open(os.path.join(tmp, "postinst"), "wb").write(t.extractfile(m).read())
app = os.path.join(root, "opt/dewiar")
os.makedirs(app, exist_ok=True)
with open(os.path.join(app, ".installed-files"), "w", encoding="utf-8") as f:
    f.write("\n".join(sorted(files)) + "\n")
print(f"   файлов: {len(files)}")
PY

if [ "$POST" = 1 ] && [ -f "$TMP/postinst" ]; then
  say "готовлю окружение Python и модель имён (библиотеки ~400 МБ — несколько минут)…"
  $SUDO bash "$TMP/postinst" configure || warn "подготовка закончилась с ошибкой — запустите ещё раз: sudo /usr/bin/python3 /opt/dewiar/dewiar-env"
fi
say "готово. Откройте «Dewiar» в меню приложений или выполните: dewiar"
say "обновить — запустить этот скрипт ещё раз; удалить — bash install.sh --remove"
