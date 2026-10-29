#!/bin/bash
# Выпуск новой версии ОДНОЙ командой на вашем сервере: собирает пакет Linux (.deb) и установщик Windows (.exe)
# и сразу выкладывает оба в папку обновлений сайта. Клиенты увидят обновление сами, а установщик Windows
# будет лежать по прямой ссылке  https://dewiar.pro/dwrapp/Dewiar-Setup-X.Y.Z.exe  — целиком, без кусков.
#
#   tar xzf dewiar-source-0.13.0.tar.gz && cd dewiar-source-0.13.0
#   tools/release.sh /путь/к/сайту/dwrapp                 # всё сразу
#   tools/release.sh /путь/к/сайту/dwrapp --docker        # собрать в контейнере (ничего не ставится в систему)
#   tools/release.sh /путь/к/сайту/dwrapp --models        # плюс первый раз выложить модели (≈0,7 ГБ)
#
# Нужно (без --docker): curl, dpkg-deb, makensis (apt install nsis), python3. uv поставится сам в ~/.local/bin.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-}"; shift || true
[ -n "$DEST" ] || { sed -n '2,11p' "$0"; exit 1; }
DOCKER=0; EXTRA=()
for a in "$@"; do
  case "$a" in --docker) DOCKER=1 ;; *) EXTRA+=("$a") ;; esac
done
mkdir -p "$DEST"; DEST="$(cd "$DEST" && pwd)"
VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/core/version.py")
echo "== Dewiar $VERSION → $DEST"

if [ "$DOCKER" = 1 ]; then
  command -v docker >/dev/null || { echo "Нет docker"; exit 1; }
  docker run --rm -v "$ROOT":/src -v "${DEWIAR_WIN_CACHE:-$HOME/.cache/dewiar-win}":/cache ubuntu:24.04 bash -c '
    set -e; export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq nsis curl ca-certificates dpkg python3 >/dev/null
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null; export PATH=$HOME/.local/bin:$PATH
    cd /src && DEWIAR_WIN_CACHE=/cache tools/build_deb.sh /src/dist && DEWIAR_WIN_CACHE=/cache tools/build_win.sh \
      && DEWIAR_WIN_CACHE=/cache tools/build_win.sh --online'
else
  for c in curl dpkg-deb makensis python3; do
    command -v $c >/dev/null || { echo "Не хватает $c. Поставьте: sudo apt install nsis curl dpkg python3  (или запустите с --docker)"; exit 1; }
  done
  if ! command -v uv >/dev/null && [ ! -x "$HOME/.local/bin/uv" ]; then
    echo "Ставлю uv (менеджер пакетов Python) в ~/.local/bin…"; curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  fi
  export PATH="$HOME/.local/bin:$PATH"
  echo "== Пакет Linux";   "$ROOT/tools/build_deb.sh" "$ROOT/dist"
  echo "== Установщик Windows (5–10 минут, первый раз скачивается Python для Windows)"; "$ROOT/tools/build_win.sh"
  echo "== Компактный установщик Windows (<30 МБ, библиотеки скачивает при установке)"; "$ROOT/tools/build_win.sh" --online
fi

DEB="$ROOT/dist/dewiar-assistant_${VERSION}_all.deb"
EXE="$ROOT/dist/Dewiar-Setup-$VERSION.exe"
[ -f "$DEB" ] && [ -f "$EXE" ] || { echo "Сборка не получилась — пакетов нет в dist/"; exit 1; }
NOTES=()
[ -f "$ROOT/RELEASE_NOTES.md" ] && NOTES=(--notes "$(cat "$ROOT/RELEASE_NOTES.md")")
echo "== Выкладываю на сайт"
python3 "$ROOT/tools/publish_update.py" "$EXE" "$DEB" "$DEST" "${NOTES[@]}" "${EXTRA[@]}"
ONL="$ROOT/dist/Dewiar-Setup-$VERSION-online.exe"
[ -f "$ONL" ] && cp "$ONL" "$DEST/" && echo "Компактный установщик: $(basename "$ONL")"
echo
echo "Готово. Ссылки для скачивания (если папка отдаётся как https://dewiar.pro/dwrapp/):"
echo "  Windows: https://dewiar.pro/dwrapp/$(basename "$EXE")   (полный, ничего не качает)"
[ -f "$ONL" ] && echo "           https://dewiar.pro/dwrapp/$(basename "$ONL")   (компактный)"
echo "  Linux:   https://dewiar.pro/dwrapp/$(basename "$DEB")"
