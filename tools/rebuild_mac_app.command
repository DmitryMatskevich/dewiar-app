#!/bin/bash
# Пересборка dist/Dewiar.app из исходников: тесты встреч → хелпер звука → PyInstaller → проверка → запуск.
# Двойной щелчок в Finder. Журнал: work/rebuild-<время>.log; итог — строка REBUILD_STATUS=OK|FAIL.
set -uo pipefail
cd "$(dirname "$0")/.."
TS=$(date +%Y%m%d-%H%M%S)
mkdir -p work
LOG="work/rebuild-$TS.log"
exec > >(tee -a "$LOG") 2>&1
step() { echo; echo "=== $(date +%T) $*"; }
fail() { echo; echo "!!! ОШИБКА: $*"; echo "REBUILD_STATUS=FAIL"; exit 1; }
PY=.venv/bin/python
[ -x "$PY" ] || fail "нет .venv/bin/python"

step "Тесты записи встреч"
for t in meeting_player_test prompter_dual_track_test prompter_links_test prompter_recording_test \
         prompter_capture_test meeting_capture_regression_test meeting_video_test prompter_schedule_test; do
  if "$PY" "tests/$t.py" >"work/rebuild-$TS-$t.log" 2>&1; then echo "OK   $t"; else fail "тест $t — см. work/rebuild-$TS-$t.log"; fi
done

step "Хелпер звука dewiar-audio-tap"
bash tools/build_audio_tap.sh || fail "сборка dewiar-audio-tap"

step "Закрываю запущенный Dewiar"
osascript -e 'tell application "Dewiar" to quit' >/dev/null 2>&1 || true
for i in $(seq 1 20); do pgrep -f "dist/Dewiar.app/Contents/MacOS/" >/dev/null || break; sleep 1; done
if pgrep -f "dist/Dewiar.app/Contents/MacOS/" >/dev/null; then
  echo "Dewiar не закрылся сам — завершаю процессы сборки dist/Dewiar.app"
  pkill -TERM -f "dist/Dewiar.app/Contents/MacOS/" || true; sleep 3
fi

if [ -d dist/Dewiar.app ]; then
  step "Копия прежней сборки"
  ditto dist/Dewiar.app "work/Dewiar-before-meetings-$TS.app" && echo "work/Dewiar-before-meetings-$TS.app"
fi

step "Сборка PyInstaller"
"$PY" -m PyInstaller --noconfirm packaging/macos.spec || fail "PyInstaller"

step "Проверка сборки"
codesign --verify --deep --strict dist/Dewiar.app && echo "OK   подпись" || fail "подпись dist/Dewiar.app"
TAP=$(find dist/Dewiar.app -name dewiar-audio-tap -type f | head -1)
[ -n "$TAP" ] && echo "OK   хелпер звука: $TAP" || echo "--   хелпер звука не вложен (запись — через BlackHole)"
UI=$(find dist/Dewiar.app -path "*ui/studio/links.js" | head -1)
grep -q prMeetingBar "$UI" && echo "OK   новый интерфейс встреч в сборке" || fail "в сборке старый links.js"
[ -n "$(find dist/Dewiar.app -name meeting_media.py | head -1)" ] || fail "нет core/meeting_media.py в сборке"

step "Запуск новой версии"
open dist/Dewiar.app
echo "REBUILD_STATUS=OK"
