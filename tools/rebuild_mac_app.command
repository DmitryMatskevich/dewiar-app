#!/bin/bash
# Сборка → проверка → установка в /Applications/Dewiar.app → запуск установленной версии.
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

step "Тесты записи, доступов и поля ввода"
for t in meeting_player_test prompter_dual_track_test prompter_links_test prompter_recording_test \
         prompter_capture_test meeting_capture_regression_test meeting_video_test prompter_schedule_test \
         mac_access_test macos_screen_startup_test macos_install_test dictation_engine_test \
         hud_draft_actions_ui_test hud_voice_finish_ui_test screenshot_text_test screenshot_button_ui_test mac_access_ui_test \
         song_source_search_test song_capture_unittest song_cards_ui_test song_capture_assistant_mode_test \
         song_instrumental_batch_test; do
  if "$PY" "tests/$t.py" >"work/rebuild-$TS-$t.log" 2>&1; then echo "OK   $t"; else fail "тест $t — см. work/rebuild-$TS-$t.log"; fi
done

step "Установка локального runtime минусовок"
"$PY" tools/install_song_separation_runtime.py || fail "runtime минусовок"

step "Хелпер звука dewiar-audio-tap"
bash tools/build_audio_tap.sh || fail "сборка dewiar-audio-tap"

step "Закрываю запущенный Dewiar"
# Stop only these exact binaries; resolving an app by name can select an old backup.
osascript -e 'tell application id "pro.dewiar.macos" to quit' >/dev/null 2>&1 || true
"$PY" - <<'PY' || fail "завершение прежней версии"
import os, signal, subprocess, time
from pathlib import Path
binaries = {str(p.resolve() / 'Contents/MacOS/Dewiar') for p in
            (Path('dist/Dewiar.app'), Path('/Applications/Dewiar.app'))}
def running():
    return [int(pid) for line in subprocess.check_output(['ps', '-axo', 'pid=,command='], text=True).splitlines()
            for pid, command in [line.strip().split(None, 1)]
            if any(command == exe or command.startswith(exe + ' ') for exe in binaries)]
for pid in running():
    try: os.kill(pid, signal.SIGTERM)
    except ProcessLookupError: pass
for _ in range(30):
    if not running(): break
    time.sleep(0.5)
else: raise SystemExit('Старое ядро не завершилось; приложение не будет заменено')
PY

if [ -d dist/Dewiar.app ]; then
  step "Копия прежней сборки"
  ditto -c -k --sequesterRsrc --keepParent dist/Dewiar.app "work/Dewiar-before-meetings-$TS.zip" || fail "архив прежней сборки"
fi

step "Сборка PyInstaller"
"$PY" -m PyInstaller --noconfirm packaging/macos.spec || fail "PyInstaller"

step "Проверка сборки"
codesign --verify --deep --strict dist/Dewiar.app && echo "OK   подпись" || fail "подпись dist/Dewiar.app"
"$PY" - <<'PY' || fail "манифест свежести сборки"
import sys
from pathlib import Path
sys.path.insert(0, str(Path('tools').resolve()))
from mac_build_identity import validate_manifest, source_fingerprint
validate_manifest(Path('dist/Dewiar.app'), Path('.').resolve(), source_fingerprint(Path('.').resolve()))
print('OK   манифест свежести исходников')
PY
TAP=$(find dist/Dewiar.app -name dewiar-audio-tap -type f | head -1)
[ -n "$TAP" ] && echo "OK   хелпер звука: $TAP" || echo "--   хелпер звука не вложен (запись — через BlackHole)"
UI=$(find dist/Dewiar.app -path "*ui/studio/links.js" | head -1)
grep -q prMeetingBar "$UI" && echo "OK   новый интерфейс встреч в сборке" || fail "в сборке старый links.js"
[ -n "$(find dist/Dewiar.app -name meeting_media.py | head -1)" ] || fail "нет core/meeting_media.py в сборке"

step "Установка в Applications"
"$PY" tools/install_mac_app.py || fail "установка /Applications/Dewiar.app"

step "Запуск установленной версии"
open /Applications/Dewiar.app
echo "REBUILD_STATUS=OK"
