"""Voice commands for dictation on macOS and pasting the text where the user's cursor is."""
import os
import re
import time


def draft_command(text):
    value = ' '.join(str(text or '').casefold().replace('ё', 'е').strip(' ,.;:!?—–-').split())
    if value in ('скопировать', 'скопируй', 'копировать', 'копируй',
                 'скопировать текст', 'скопируй текст', 'копировать текст', 'копируй текст'):
        return 'copy'
    if value in ('сотри', 'стереть', 'сотри текст', 'стереть текст',
                 'очисти', 'очистить', 'очисти текст', 'очистить текст'):
        return 'clear'
    if value in ('вставить', 'вставь', 'вставить текст', 'вставь текст',
                 'ставить', 'ставить текст', 'добавить', 'добавь', 'добавить текст', 'добавь текст', 'текст'):
        return 'insert'
    return None


def copy_text(text):
    """An explicit voice command replaces the clipboard with the dictated text."""
    from AppKit import NSPasteboard, NSPasteboardTypeString
    pasteboard = NSPasteboard.generalPasteboard()
    pasteboard.clearContents()
    if not pasteboard.setString_forType_(text, NSPasteboardTypeString):
        raise RuntimeError('Не удалось скопировать текст в буфер обмена.')


def parse(text, aliases):
    """Only an explicit assistant name starts a command; other speech is literal."""
    text = str(text or '').strip()
    names = sorted({str(a).strip() for a in aliases if str(a).strip()}, key=len, reverse=True)
    if not names:
        return text, None
    rx = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(n) for n in names) + r')(?!\w)', re.I)
    for match in rx.finditer(text):
        before = text[:match.start()].rstrip(' ,;:—–-')
        after = text[match.end():].strip(' ,.;:!?…—–-').casefold().replace('ё', 'е')
        action = draft_command(after)
        if action:
            return before, (action, '')
        if not before and after in ('появись', 'появиться', 'покажись', 'покажи себя', ''):
            return before, ('show', '')
        if after in ('отмена', 'отмени'):
            return before, ('cancel', '')
        if after in ('стоп', 'прекрати запись', 'перестань слушать', 'закончи диктовку'):
            return before, ('stop', '')
        if after in ('слушай', 'записывай'):
            return before, ('listen', '')
        # Named commands are deliberate, but a name embedded in ordinary dictation
        # is kept as text unless the command is at the start of the phrase.
        if not before and after and re.match(r'^(?:открой|открыть|закрой|закрыть|сделай|сделать|сними|'
                                              r'снимок|покажи|найди|запусти|останови|включи|выключи|'
                                              r'перемести|перенеси|сохрани|создай|удали|очисти|'
                                              r'спрячься|скройся|покажись|появись|отключись)\b', after):
            return '', ('command', text[match.end():].strip(' ,.;:!?—–-'))
    return text, None


DEWIAR_BUNDLE = 'pro.dewiar.macos'
# Physical V and left Command. Russian layouts keep the Latin layer for ⌘
# shortcuts, so this is ⌘V there as well.
V_KEY, COMMAND_KEY = 9, 55
# Ordinary-level windows of these processes are never a paste target.
SYSTEM_OWNERS = {'window server', 'dock', 'control center', 'notification center', 'systemuiserver'}
ACCESS_HINT = ('Нет разрешения «Универсальный доступ»: macOS не даёт Dewiar нажать ⌘V. '
               'Настройки → Конфиденциальность и безопасность → Универсальный доступ. '
               'Если Dewiar там уже включён, удалите запись (−) и добавьте Dewiar.app заново (+): '
               'после пересборки macOS считает его новым приложением.')
_prompted = False


def copy_and_insert(text):
    """⌘C, then ⌘V: the text stays on the clipboard even if pasting fails."""
    copy_text(text)
    try:
        return {'copied': True, 'inserted': True, 'app': paste_into_focus()}
    except Exception as exc:  # noqa: BLE001 — the reason is shown to the user and logged
        return {'copied': True, 'inserted': False, 'reason': str(exc)}


def insert_at_focus(text):
    """Copy and paste into the focused field; raises when pasting is impossible."""
    copy_text(text)
    return paste_into_focus()


def paste_into_focus():
    """Press ⌘V in the field that has the text cursor; returns the application name.

    The text must already be on the clipboard. Only ⌘V is pressed, never Enter.
    If Dewiar itself is active (its window was clicked or shown), the user's
    application right behind it is activated first, and the paste goes to the
    field that was focused there."""
    import ApplicationServices as ax
    _require_access(ax)
    pid, name, switch = paste_target(ax)
    if switch:
        _activate(ax, pid, name)
    _refuse_secure_field(ax)
    if _focused_pid(ax) != pid:
        raise RuntimeError('Фокус перешёл в другое приложение. Вернитесь в нужное поле и повторите «Лира, вставить».')
    press_paste()
    return name or 'активное приложение'


def press_paste():
    """Command down, V down and up, Command up — the same events as the keyboard."""
    import Quartz
    command = Quartz.kCGEventFlagMaskCommand
    for key, down, flags in ((COMMAND_KEY, True, command), (V_KEY, True, command),
                             (V_KEY, False, command), (COMMAND_KEY, False, 0)):
        event = Quartz.CGEventCreateKeyboardEvent(None, key, down)
        Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(.01)


def paste_target(ax):
    """(pid, name, switch) of the application that must receive ⌘V."""
    own = _dewiar_pids()
    pid = _focused_pid(ax)
    if not pid:
        raise RuntimeError('Не удалось определить активное приложение. Щёлкните в нужное поле и повторите «Лира, вставить».')
    owners = [(p, n) for p, n in _window_owners() if p and n.casefold() not in SYSTEM_OWNERS]
    if pid and pid not in own:
        return pid, next((n for p, n in owners if p == pid), ''), False
    for p, n in owners:  # front to back: the window right behind Dewiar
        if p not in own and n.casefold() != 'dewiar':
            return p, n, True
    raise RuntimeError('Не вижу окна, куда вставить. Щёлкните в нужное поле и повторите «Лира, вставить».')


def _require_access(ax):
    """Without Accessibility macOS silently drops synthetic key presses."""
    global _prompted
    if ax.AXIsProcessTrusted():
        return
    if not _prompted:
        _prompted = True
        try:  # once per run: the system dialog opens the right settings page
            ax.AXIsProcessTrustedWithOptions({'AXTrustedCheckOptionPrompt': True})
        except Exception:  # noqa: BLE001
            pass
    raise RuntimeError(ACCESS_HINT)


def _ax_value(ax, element, key):
    code, value = ax.AXUIElementCopyAttributeValue(element, key, None)
    return value if code == 0 else None


def _focused_pid(ax):
    """Active application as the accessibility server sees it, 0 if unknown.

    NSWorkspace.frontmostApplication is not used: the core process has no Cocoa
    run loop, so there it keeps the value of its first call."""
    app = _ax_value(ax, ax.AXUIElementCreateSystemWide(), 'AXFocusedApplication')
    if app is None:
        return 0
    code, pid = ax.AXUIElementGetPid(app, None)
    return int(pid) if code == 0 else 0


def _window_owners():
    """(pid, owner) of ordinary on-screen windows, front to back.

    Owner names are available without the screen-recording permission."""
    import Quartz
    rows = []
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    for item in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
        bounds = item.get('kCGWindowBounds') or {}
        if (int(item.get('kCGWindowLayer', 0)) != 0 or float(item.get('kCGWindowAlpha', 1)) <= 0
                or float(bounds.get('Width', 0)) < 50 or float(bounds.get('Height', 0)) < 30):
            continue
        rows.append((int(item.get('kCGWindowOwnerPID') or 0), str(item.get('kCGWindowOwnerName') or '')))
    return rows


def _ui_pid():
    """The Dewiar window process publishes its pid (in a source run it is a generic Python)."""
    try:
        import json
        from settings import STATE_DIR, WS_PORT
        info = json.loads((STATE_DIR / 'core-port.json').read_text())
        return int(info.get('ui_pid', 0)) if int(info.get('port', 0)) == WS_PORT else 0
    except Exception:  # noqa: BLE001 — no window process or a stale file
        return 0


def _dewiar_pids():
    pids = {os.getpid(), os.getppid(), _ui_pid()}
    try:
        from AppKit import NSRunningApplication
        pids.update(int(app.processIdentifier()) for app in
                    NSRunningApplication.runningApplicationsWithBundleIdentifier_(DEWIAR_BUNDLE) or [])
    except Exception:  # noqa: BLE001
        pass
    return pids - {0, 1}


def _activate(ax, pid, name):
    ax.AXUIElementSetAttributeValue(ax.AXUIElementCreateApplication(pid), 'AXFrontmost', True)
    try:
        from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is not None:
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    except Exception:  # noqa: BLE001 — the accessibility request above is the main path
        pass
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        if _focused_pid(ax) == pid:
            time.sleep(.15)  # the window becomes key and restores its text cursor
            return
        time.sleep(.05)
    raise RuntimeError('Не удалось перейти в «' + (name or 'приложение') + '». '
                       'Щёлкните в нужное поле и повторите «Лира, вставить».')


def _refuse_secure_field(ax):
    element = _ax_value(ax, ax.AXUIElementCreateSystemWide(), 'AXFocusedUIElement')
    if element is None:
        raise RuntimeError('Не удалось определить активное поле и проверить, что оно не защищено. Щёлкните в нужное поле и повторите команду.')
    kind = (str(_ax_value(ax, element, 'AXRole') or '') + str(_ax_value(ax, element, 'AXSubrole') or '')).lower()
    if 'secure' in kind:
        raise RuntimeError('В поле пароля текст не вставляю.')
