# Сборка локального Mac-прототипа. Developer ID задаётся DEWIAR_CODESIGN_IDENTITY.
import os
import re
import subprocess
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
datas = [(str(root / name), name) for name in ("core", "ui", "data", "icons")]
datas += [(str(root / "tools/subscription_ui_smoke.js"), "tools")]
binaries = []
hidden = [p.stem for p in (root / "core").glob("*.py")] + ["macshell", "Vision", "webview.platforms.cocoa", "logging.handlers"]
for package in ("onnxruntime", "onnx_asr", "huggingface_hub", "vosk", "piper", "imageio_ffmpeg"):
    data, binary, imports = collect_all(package)
    datas += data
    binaries += binary
    hidden += imports
for distribution in ("onnx-asr", "huggingface-hub"):
    datas += copy_metadata(distribution)

a = Analysis([str(root / "tools/macos_entry.py")], pathex=[str(root / "core"), str(root / "ui")],
             binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=["pytest", "playwright", "tkinter", "gi", "PyQt5", "PyQt6", "PySide6"],
             noarchive=False)
pyz = PYZ(a.pure)
identity = os.environ.get("DEWIAR_CODESIGN_IDENTITY") or None
if not identity:
    available=subprocess.run(['security','find-identity','-v','-p','codesigning'],capture_output=True,text=True,check=True).stdout
    identities=re.findall(r'\b([A-F0-9]{40})\s+"(?:Apple Development:|Developer ID Application:)[^"]+"',available)
    if len(identities)==1:
        identity=identities[0]
    elif len(identities)>1:
        raise SystemExit('Several signing identities: choose DEWIAR_CODESIGN_IDENTITY explicitly; refusing an ad-hoc downgrade')
    else:
        previous=root/'dist/Dewiar.app'
        if previous.exists():
            previous_signing=subprocess.run(['codesign','-dv',str(previous)],capture_output=True,text=True)
            if 'Authority=' in previous_signing.stderr:
                raise SystemExit('The previous app has a persistent signer. Restore that signing identity; refusing an ad-hoc downgrade.')
        print('WARNING: ad-hoc development build; macOS microphone permissions can reset after each rebuild. Configure Apple Development/Developer ID signing in Xcode.')
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Dewiar", console=False,
          target_arch="arm64", codesign_identity=identity,
          entitlements_file=str(root / "packaging/macos-entitlements.plist"))
coll = COLLECT(exe, a.binaries, a.datas, name="Dewiar")
app = BUNDLE(coll, name="Dewiar.app", bundle_identifier="pro.dewiar.macos",
             info_plist={"CFBundleDisplayName": "Dewiar", "CFBundleShortVersionString": "0.45.3",
                         "NSContactsUsageDescription": "Импорт выбранных контактов в Dewiar и создание новых карточек по вашему подтверждению",
                         "NSMicrophoneUsageDescription": "Голосовое общение с помощником Dewiar",
                         "NSScreenCaptureUsageDescription": "Снимки экрана и OCR во время встречи по запросу пользователя",
                         "NSCameraUsageDescription": "Получение изображения по запросу пользователя",
                         "NSAppleEventsUsageDescription": "Управление выбранными пользователем приложениями",
                         "NSHighResolutionCapable": True})
