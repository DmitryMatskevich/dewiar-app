"""Visible screenshot targets. Enumeration never activates or moves an application."""
import subprocess
import tempfile
from pathlib import Path


def subtract(rect, cover):
    x, y, w, h = rect; a, b, c, d = cover
    left, top, right, bottom = max(x, a), max(y, b), min(x+w, a+c), min(y+h, b+d)
    if right <= left or bottom <= top:return [rect]
    return [r for r in [(x,y,w,top-y),(x,bottom,w,y+h-bottom),(x,top,left-x,bottom-top),(right,top,x+w-right,bottom-top)] if r[2] > 0 and r[3] > 0]


def visible_windows():
    from Quartz import CGPreflightScreenCaptureAccess, CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly, kCGNullWindowID
    from AppKit import NSWorkspace
    if not CGPreflightScreenCaptureAccess():
        raise PermissionError('Разрешите Dewiar запись экрана в настройках macOS → Конфиденциальность и безопасность → Запись экрана.')
    apps = {int(app.processIdentifier()): {'id': str(app.bundleIdentifier() or ''), 'regular': app.activationPolicy() == 0}
            for app in NSWorkspace.sharedWorkspace().runningApplications() if not app.isTerminated()}
    import json
    from settings import STATE_DIR, WS_PORT
    ui_pid = 0
    try:
        info = json.loads((STATE_DIR / 'core-port.json').read_text())
        if int(info.get('port', 0)) == WS_PORT:ui_pid = int(info.get('ui_pid', 0))
    except (OSError, ValueError, TypeError):pass
    raw = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []
    rows, covers = [], []
    for item in raw:
        if int(item.get('kCGWindowLayer', 0)) != 0 or float(item.get('kCGWindowAlpha', 1)) <= 0:continue
        if item.get('kCGWindowIsOnscreen') is False:continue
        owner = str(item.get('kCGWindowOwnerName') or '')
        title = str(item.get('kCGWindowName') or '')
        pid = int(item.get('kCGWindowOwnerPID') or 0); app = apps.get(pid, {})
        if app.get('id') == 'pro.dewiar.macos' or (ui_pid and pid == ui_pid) or owner.casefold() == 'dewiar' or title.casefold().startswith('dewiar —'):continue
        bounds = item.get('kCGWindowBounds') or {}
        rect = tuple(float(bounds.get(k, 0)) for k in ('X','Y','Width','Height'))
        if rect[2] < 2 or rect[3] < 2:continue
        parts = [rect]
        for cover in covers:
            parts = [piece for part in parts for piece in subtract(part, cover)]
            if not parts:break
        if parts and owner and app.get('regular', True) and int(item.get('kCGWindowNumber') or 0):
            rows.append({'window_id': int(item['kCGWindowNumber']), 'name': owner, 'title': title,
                         'id': app.get('id', ''), 'pid': pid})
        if float(item.get('kCGWindowAlpha', 1)) >= .99:covers.append(rect)
    return rows


def choose_window(rows, application='', window_id=None, aliases=None):
    if window_id is not None:
        if isinstance(window_id, bool) or not isinstance(window_id, int) or window_id <= 0:raise ValueError('Неверный номер окна')
        found = [r for r in rows if r['window_id'] == window_id]
        if found and application:
            from voice_commands import config, target, norm
            query = norm(target(application, config(aliases)))
            if query not in (norm(found[0]['name']), norm(found[0]['id']), norm(found[0]['title'])):
                raise ValueError('Выбранное окно не относится к указанному приложению. Обновите список окон.')
    else:
        from voice_commands import config, target, norm
        query = norm(target(application, config(aliases)))
        if not query:raise ValueError('Укажите название приложения')
        found = [r for r in rows if query in (norm(r['name']), norm(r['id']), norm(r['title']))]
    if not found:raise ValueError('Видимое окно приложения не найдено. Откройте его на экране и обновите список.')
    if len(found) > 1:
        raise ValueError('У приложения несколько видимых окон. Выберите нужное окно в меню снимка экрана: ' + '; '.join(r['title'] or r['name'] for r in found[:5]))
    return found[0]


def capture_window(application='', window_id=None, aliases=None):
    # Re-enumerate immediately before capture: stale menu entries cannot capture hidden windows.
    row = choose_window(visible_windows(), application, window_id, aliases)
    with tempfile.TemporaryDirectory(prefix='dewiar-window-') as folder:
        path = Path(folder) / 'window.png'
        result = subprocess.run(['/usr/sbin/screencapture', '-l', str(row['window_id']), '-x', '-o', '-t', 'png', str(path)],
                                capture_output=True, text=True, timeout=15)
        if result.returncode or not path.is_file() or not path.stat().st_size:
            raise OSError(result.stderr.strip() or 'macOS не смогла сделать снимок выбранного окна')
        return path.read_bytes()
