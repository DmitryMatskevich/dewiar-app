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


def _marker(scope):
    if scope not in ('screen', 'files'):
        raise ValueError('Unknown permission scope')
    return STATE_DIR / f'{scope}-access-request.json'


def was_requested(scope='screen'):
    build = build_id()
    if not build:
        return False
    try:
        return json.loads(_marker(scope).read_text()).get('build') == build
    except (OSError, ValueError, AttributeError):
        return False


def mark_requested(scope='screen'):
    build = build_id()
    if not build:
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = _marker(scope)
    pending = target.with_suffix('.tmp')
    pending.write_text(json.dumps({'build': build}), encoding='utf-8')
    pending.replace(target)
