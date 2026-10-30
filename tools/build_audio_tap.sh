#!/bin/bash
# Сборка native/macos/bin/dewiar-audio-tap (Core Audio Process Tap → нужен SDK macOS 14.2+).
#   tools/build_audio_tap.sh            — arm64
#   ARCH=x64 tools/build_audio_tap.sh   — Intel
# Подпись: DEWIAR_CODESIGN_IDENTITY (та же, что у Dewiar.app); без неё — ad hoc
# (разрешение «Запись системного звука» тогда может сбрасываться после пересборки).
set -euo pipefail
cd "$(dirname "$0")/.."
SRC=native/macos/dewiar_audio_tap.swift
PLIST=native/macos/dewiar_audio_tap.Info.plist
OUT="${1:-native/macos/bin/dewiar-audio-tap}"
mkdir -p "$(dirname "$OUT")"

SWIFTC=(xcrun swiftc)
SDK=$(xcrun --show-sdk-path 2>/dev/null || true)
if ! grep -q CATapDescription "$SDK/System/Library/Frameworks/CoreAudio.framework/Headers/"*.h 2>/dev/null; then
  # В старом Xcode нет API tap; Command Line Tools бывают новее.
  CLT=/Library/Developer/CommandLineTools
  if grep -q CATapDescription "$CLT/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreAudio.framework/Headers/"*.h 2>/dev/null; then
    SWIFTC=("$CLT/usr/bin/swiftc" -sdk "$CLT/SDKs/MacOSX.sdk")
  else
    echo "Нет SDK с CATapDescription (нужен macOS 14.2 SDK: обновите Xcode или Command Line Tools)" >&2
    exit 1
  fi
fi
[[ "${ARCH:-arm64}" == x64 ]] && TARGET=x86_64-apple-macos14.2 || TARGET=arm64-apple-macos14.2

"${SWIFTC[@]}" -O -target "$TARGET" -o "$OUT" "$SRC" \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$PLIST"

IDENTITY="${DEWIAR_CODESIGN_IDENTITY:--}"
codesign --force --options runtime --timestamp=none --sign "$IDENTITY" \
  --identifier pro.dewiar.audio-tap "$OUT"
echo "Готово: $OUT ($(codesign -dv "$OUT" 2>&1 | grep -E '^(Authority|Signature)=' | head -1))"
"$OUT" --list >/dev/null && echo "Проверка --list: OK"
"$OUT" --self-test
