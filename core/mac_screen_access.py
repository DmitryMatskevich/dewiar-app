"""Remember requests per installed build, never cache macOS permission grants."""
import json
import plistlib
import sys
from pathlib import Path
from settings import STATE_DIR


def build_id():
    if not getattr(sys, 'frozen', False):
        return ''
    try:
        info = Path(sys.executable).resolve().parents[1] / 'Info.plist'
        return str(plistlib.loads(info.read_bytes()).get('DewiarBuildID') or '')
    except (OSError, ValueError, plistlib.InvalidFileException):
        return ''


def was_requested():
    build = build_id()
    if not build:
        return False
    try:
        return json.loads((STATE_DIR / 'screen-access-request.json').read_text()).get('build') == build
    except (OSError, ValueError, AttributeError):
        return False


def mark_requested():
    build = build_id()
    if not build:
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = STATE_DIR / 'screen-access-request.json'
    pending = target.with_suffix('.tmp')
    pending.write_text(json.dumps({'build': build}), encoding='utf-8')
    pending.replace(target)
