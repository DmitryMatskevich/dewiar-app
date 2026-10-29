#!/bin/bash
# Сборка .deb:  tools/build_deb.sh [папка_вывода]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/dist}"
VER="$(awk '/^Version:/{print $2}' "$ROOT/packaging/DEBIAN/control")"
STAGE="$(mktemp -d)/dewiar-assistant_${VER}_all"
APP="$STAGE/opt/dewiar"
mkdir -p "$APP" "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/share/applications" "$STAGE/etc/xdg/autostart" "$OUT"
cp "$ROOT"/packaging/DEBIAN/* "$STAGE/DEBIAN/"
cp -r "$ROOT/core" "$ROOT/ui" "$ROOT/data" "$ROOT/icons" "$APP/"
cp "$ROOT/requirements.txt" "$ROOT/requirements-extra.txt" "$ROOT/packaging/dewiar-setup" "$ROOT/packaging/dewiar-env" "$APP/"
cp "$ROOT/install.sh" "$APP/install.sh"                 # любой Linux без dpkg: им же ставится и обновляется (ui/app.py)
cp "$ROOT/packaging/dewiar" "$STAGE/usr/bin/dewiar"
cp "$ROOT/packaging/dewiar-server" "$APP/dewiar-server"; ln -s /opt/dewiar/dewiar-server "$STAGE/usr/bin/dewiar-server"   # Dewiar на сервере
cp "$ROOT/packaging/desktop/dewiar.desktop" "$ROOT/packaging/desktop/dewiar-studio.desktop" "$STAGE/usr/share/applications/"
cp "$ROOT/packaging/desktop/dewiar-autostart.desktop" "$STAGE/etc/xdg/autostart/dewiar.desktop"
for s in 48 64 128 256; do
  install -Dm644 "$ROOT/icons/dewiar-$s.png" "$STAGE/usr/share/icons/hicolor/${s}x${s}/apps/dewiar.png"
done
install -Dm644 "$ROOT/packaging/mime/dewiar.xml" "$STAGE/usr/share/mime/packages/dewiar.xml"
install -Dm644 "$ROOT/icons/dewiar-512.png" "$STAGE/usr/share/icons/hicolor/512x512/apps/dewiar.png"
# Преднастроенная сборка: положите свой provision.json рядом с build_deb.sh или передайте PROVISION=путь
PROV="${PROVISION:-}"                                   # только явно: адресов и ключей в сборке по умолчанию нет
[ -n "$PROV" ] && [ -f "$PROV" ] && cp "$PROV" "$APP/data/provision.json" && echo "Встроены настройки: $PROV"
rm -f "$APP/data/vosk-model-small-ru-0.22.zip"      # скачивается при установке (postinst) — пакет маленький
find "$STAGE" -name '__pycache__' -prune -exec rm -rf {} +
find "$STAGE" -name '*.png' -path '*/ui/*' ! -path '*/ui/assets/*' -delete
chmod 755 "$STAGE"/DEBIAN/{preinst,postinst,prerm,postrm} "$STAGE/usr/bin/dewiar" "$APP/dewiar-setup" "$APP/dewiar-env" "$APP/dewiar-server" "$APP/core/main.py" "$APP/ui/app.py" "$APP/install.sh"
dpkg-deb --build --root-owner-group -Zxz "$STAGE" "$OUT/dewiar-assistant_${VER}_all.deb" >/dev/null
python3 "$ROOT/tools/release_notes.py" "$OUT" >/dev/null 2>&1 || true   # «что нового» для кабинета: dewiar-notes-X.Y.Z.txt
echo "$OUT/dewiar-assistant_${VER}_all.deb"
