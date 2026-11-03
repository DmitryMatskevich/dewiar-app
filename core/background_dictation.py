"""Voice-command parsing and safe insertion for background dictation on macOS."""
import re
import threading


def draft_command(text):
    value = ' '.join(str(text or '').casefold().replace('ё', 'е').strip(' ,.;:!?—–-').split())
    if value in ('скопировать', 'скопируй', 'копировать', 'копируй'):
        return 'copy'
    if value in ('вставить', 'вставь', 'добавить', 'добавь', 'текст'):
        return 'insert'
    return None


def copy_text(text):
    """An explicit voice command replaces the clipboard with the dictated text."""
    from AppKit import NSPasteboard, NSPasteboardTypeString
    pasteboard = NSPasteboard.generalPasteboard()
    pasteboard.clearContents()
    if not pasteboard.setString_forType_(text, NSPasteboardTypeString):
        raise RuntimeError('Не удалось скопировать текст в буфер обмена.')


def copy_and_insert(text):
    copy_text(text)
    try:
        insert_at_focus(text)
        return {'copied': True, 'inserted': True}
    except Exception as exc:
        return {'copied': True, 'inserted': False, 'reason': str(exc)}


def parse(text, aliases):
    """Only an explicit assistant name starts a command; other speech is literal."""
    text = str(text or '').strip()
    names = sorted({str(a).strip() for a in aliases if str(a).strip()}, key=len, reverse=True)
    if not names:
        return text, None
    rx = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(n) for n in names) + r')(?!\w)', re.I)
    for match in rx.finditer(text):
        before = text[:match.start()].rstrip(' ,;:—–-')
        after = text[match.end():].strip(' ,.;:!?—–-').casefold().replace('ё', 'е')
        action = draft_command(after)
        if action:
            return before, (action, '')
        if not before and after in ('появись', 'появиться', 'покажись', 'покажи себя', ''):
            return before, ('show', '')
        if after in ('отмена', 'отмени'):
            return before, ('cancel', '')
        if after in ('прекрати запись', 'перестань слушать', 'закончи диктовку'):
            return before, ('stop', '')
        if after in ('слушай', 'записывай'):
            return before, ('listen', '')
        # Named commands are deliberate, but a name embedded in ordinary dictation
        # is kept as text unless the command is at the start of the phrase.
        if not before and after:
            return '', ('command', text[match.end():].strip(' ,.;:!?—–-'))
    return text, None


def insert_at_focus(text):
    """Set only the selected text of the current writable AX element; never press Enter."""
    from ApplicationServices import (AXIsProcessTrusted, AXUIElementCreateSystemWide,
        AXUIElementCopyAttributeValue, AXUIElementIsAttributeSettable, AXUIElementSetAttributeValue)
    from AppKit import NSWorkspace
    if not AXIsProcessTrusted():
        raise RuntimeError('Разрешите Dewiar «Универсальный доступ» в настройках macOS.')
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if not app or str(app.bundleIdentifier() or '') == 'pro.dewiar.macos':
        raise RuntimeError('Откройте поле в нужном приложении и повторите «Лира, вставить».')
    system = AXUIElementCreateSystemWide()
    def get(obj, key):
        code, value = AXUIElementCopyAttributeValue(obj, key, None)
        return value if code == 0 else None
    element = get(system, 'AXFocusedUIElement')
    if element is None:
        raise RuntimeError('Не удалось определить активное поле ввода.')
    role = str(get(element, 'AXRole') or '')
    subrole = str(get(element, 'AXSubrole') or '')
    if 'secure' in (role + subrole).lower():
        raise RuntimeError('Диктовка в защищённое поле недоступна.')
    code, allowed = AXUIElementIsAttributeSettable(element, 'AXSelectedText', None)
    direct = code == 0 and allowed
    if not direct:
        code, editable = AXUIElementIsAttributeSettable(element, 'AXValue', None)
        if code != 0 or not editable or role not in ('AXTextArea', 'AXTextField', 'AXComboBox', 'AXSearchField'):
            raise RuntimeError('Это поле не поддерживает безопасную вставку текста.')
    # Check focus once more immediately before mutation; never switch app or field.
    if get(system, 'AXFocusedUIElement') != element or NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() != app.processIdentifier():
        raise RuntimeError('Фокус изменился. Вернитесь в нужное поле и повторите команду.')
    if direct:
        code = AXUIElementSetAttributeValue(element, 'AXSelectedText', text)
        if code:
            raise RuntimeError('Приложение отклонило вставку текста (' + str(code) + ').')
        return
    # Some native controls accept Cmd+V but cannot set AXSelectedText. Preserve
    # every pasteboard representation and restore it only if it wasn't changed
    # by the user or the destination application in the meantime.
    from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString
    import Quartz
    pasteboard = NSPasteboard.generalPasteboard()
    old = []
    for item in pasteboard.pasteboardItems() or []:
        saved = NSPasteboardItem.alloc().init()
        for kind in item.types():
            data = item.dataForType_(kind)
            if data is not None:
                saved.setData_forType_(data, kind)
        old.append(saved)
    pasteboard.clearContents()
    if not pasteboard.setString_forType_(text, NSPasteboardTypeString):
        if old:
            pasteboard.writeObjects_(old)
        raise RuntimeError('Не удалось подготовить текст для вставки.')
    revision = pasteboard.changeCount()
    try:
        if get(system, 'AXFocusedUIElement') != element or NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() != app.processIdentifier():
            raise RuntimeError('Фокус изменился. Вернитесь в нужное поле и повторите команду.')
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, 9, down)  # V
            Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    finally:
        def restore():
            if pasteboard.changeCount() == revision:
                pasteboard.clearContents()
                if old:
                    pasteboard.writeObjects_(old)
        timer = threading.Timer(.7, restore)
        timer.daemon = True
        timer.start()
