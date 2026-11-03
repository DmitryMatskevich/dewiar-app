"""Системные действия macOS: открытие файлов и корзина Finder.

Нативные библиотеки импортируются внутри функций: ядро и тесты других ОС их не требуют.
Пути передаются аргументами, без вставки пользовательского текста в shell/AppleScript.
"""
import subprocess
from pathlib import Path


def microphone_permission():
    from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
    status = int(AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio))
    state = {0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized"}.get(status, "unknown")
    return {"state": state, "granted": state == "authorized"}


def applications(directories=None):
    """Discover installed bundles without executing them or traversing their internals."""
    import plistlib
    roots = directories if directories is not None else (
        Path("/Applications"), Path("/System/Applications"), Path.home() / "Applications")
    found = {}
    bundles = [bundle for root in map(Path, roots) for pattern in ('*.app', '*/*.app') for bundle in sorted(root.glob(pattern))]
    # A packaged Dewiar may run from dist/ or Downloads rather than /Applications.
    import sys
    if directories is None and getattr(sys, 'frozen', False):
        current = Path(sys.executable).parents[2]
        if current.suffix == '.app':bundles.append(current)
    for bundle in bundles:
        try:
            with (bundle / "Contents/Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
            key = str(info.get("CFBundleIdentifier") or bundle.resolve())
            name = str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or bundle.stem)
            found.setdefault(key, {"name": name, "ru": "", "keywords": bundle.stem,
                                   "id": key, "path": str(bundle)})
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
    return list(found.values())


def open_target(target, reveal=False):
    command = ["/usr/bin/open"] + (["-R"] if reveal else []) + [str(target)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise OSError(result.stderr.strip() or "macOS не смогла открыть файл или адрес")


def trash(path):
    from Foundation import NSFileManager, NSURL
    url = NSURL.fileURLWithPath_(str(Path(path).absolute()))
    result = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(url, None, None)
    if not result[0]:
        raise OSError(str(result[-1]) or "macOS не смогла переместить файл в Корзину")


def _screenshot_without_dewiar():
    """macOS 14+: compositor capture, revealing the content behind Dewiar windows."""
    import json
    import threading
    import ScreenCaptureKit as SC
    from AppKit import NSBitmapImageRep, NSBitmapImageFileTypePNG
    from Quartz import CGMainDisplayID
    from settings import STATE_DIR, WS_PORT

    def await_result(start):
        ready, result = threading.Event(), {}
        def complete(value, error):
            result.update(value=value, error=error)
            ready.set()
        start(complete)
        if not ready.wait(15):
            raise TimeoutError("macOS не ответила на запрос снимка экрана за 15 секунд")
        if result.get("error") or result.get("value") is None:
            raise OSError(str(result.get("error") or "macOS вернула пустой снимок"))
        return result["value"]

    content = await_result(SC.SCShareableContent.getShareableContentWithCompletionHandler_)
    # Source runs in Python, so a generic Python bundle id must never exclude
    # other Python applications. The authenticated shell publishes its pid/port.
    ui_pid = 0
    try:
        shell = json.loads((STATE_DIR / "core-port.json").read_text())
        if int(shell.get("port", 0)) == WS_PORT:
            ui_pid = int(shell.get("ui_pid", 0))
    except (OSError, ValueError, TypeError):
        pass
    excluded = []
    for window in content.windows():
        owner = window.owningApplication()
        title = str(window.title() or "").casefold()
        if ((owner and str(owner.bundleIdentifier()) == "pro.dewiar.macos")
                or (ui_pid and owner and int(owner.processID()) == ui_pid)
                or title == "dewiar" or title.startswith("dewiar — ")):
            excluded.append(window)
    displays = list(content.displays())
    display = next((d for d in displays if d.displayID() == CGMainDisplayID()), None)
    if display is None:
        raise OSError("основной экран Mac не найден; проверьте, не заблокирован ли компьютер")
    filter_ = SC.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, excluded)
    config = SC.SCStreamConfiguration.alloc().init()
    # Keep native pixel resolution for OCR, rather than the LLM's resized JPEG.
    scale = float(filter_.pointPixelScale())
    rect = filter_.contentRect()
    config.setWidth_(max(1, round(rect.size.width * scale)))
    config.setHeight_(max(1, round(rect.size.height * scale)))
    config.setShowsCursor_(False)
    image = await_result(lambda done: SC.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(filter_, config, done))
    bitmap = NSBitmapImageRep.alloc().initWithCGImage_(image)
    data = bitmap.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    if not data:
        raise OSError("macOS не смогла преобразовать снимок в PNG")
    return bytes(data)


def screen_capture(source="screen", region=None, application="", exclude_dewiar=False):
    """Capture a screen, region, or visible application window.

    ``source`` is deliberately small and serialisable because it is passed from
    the meeting window: ``screen`` (default), ``region`` (x/y/width/height), or
    ``application`` (owner name/window title substring).  A missing permission
    is reported before attempting a partial capture.
    """
    import io
    import tempfile
    from Quartz import CGPreflightScreenCaptureAccess, CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly, kCGNullWindowID
    from PIL import ImageGrab
    if not CGPreflightScreenCaptureAccess():
        raise PermissionError("Разрешите Dewiar запись экрана: Системные настройки → Конфиденциальность и безопасность → Запись экрана; затем перезапустите приложение")
    source = str(source or "screen").lower()
    if exclude_dewiar and source in ("screen", "region"):
        try:
            raw = _screenshot_without_dewiar()
            if source == "screen":
                return raw
            from PIL import Image
            from Quartz import CGDisplayBounds, CGMainDisplayID
            try:
                x, y, w, h = [int(region.get(k, 0)) for k in ("x", "y", "width", "height")]
            except (AttributeError, TypeError, ValueError):
                raise ValueError("область экрана задана неверно") from None
            bounds = CGDisplayBounds(CGMainDisplayID())
            if w < 2 or h < 2 or x < bounds.origin.x or y < bounds.origin.y or x + w > bounds.origin.x + bounds.size.width or y + h > bounds.origin.y + bounds.size.height:
                raise ValueError("область должна целиком находиться на основном экране")
            image = Image.open(io.BytesIO(raw))
            sx, sy = image.width / bounds.size.width, image.height / bounds.size.height
            box = (round((x - bounds.origin.x) * sx), round((y - bounds.origin.y) * sy),
                   round((x + w - bounds.origin.x) * sx), round((y + h - bounds.origin.y) * sy))
            result = io.BytesIO()
            image.crop(box).save(result, "PNG")
            return result.getvalue()
        except (ImportError, AttributeError) as exc:
            # Do not silently fall back to a screenshot containing the assistant.
            raise OSError("Для снимка без ассистента нужны macOS 14+ и компонент ScreenCaptureKit. Обновите зависимости Dewiar.") from exc
    if source == "region":
        try:
            x, y, w, h = [int(region.get(k, 0)) for k in ("x", "y", "width", "height")]
        except (AttributeError, TypeError, ValueError):
            raise ValueError("область экрана задана неверно") from None
        if w < 2 or h < 2:
            raise ValueError("ширина и высота области должны быть больше нуля")
        image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    elif source in ("application", "window"):
        needle = str(application or "").strip().lower()
        if not needle:
            raise ValueError("укажите приложение или окно для записи")
        windows = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []
        found = None
        for item in windows:
            owner = str(item.get("kCGWindowOwnerName") or "")
            title = str(item.get("kCGWindowName") or "")
            if needle in owner.lower() or needle in title.lower():
                wid = int(item.get("kCGWindowNumber") or 0)
                if wid:
                    found = wid
                    break
        if not found:
            raise ValueError(f"окно «{application}» не найдено")
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            result = subprocess.run(["/usr/sbin/screencapture", "-l", str(found), "-x", "-t", "png", tmp.name],
                                    capture_output=True, text=True, timeout=15)
            if result.returncode or not Path(tmp.name).is_file():
                raise OSError(result.stderr.strip() or "macOS не смогла снять окно приложения")
            return Path(tmp.name).read_bytes()
    else:
        image = ImageGrab.grab()
    result = io.BytesIO()
    image.save(result, "PNG")
    return result.getvalue()


def request_screen_capture_permission():
    """Ask macOS for Screen Recording permission, then report the current state.

    macOS shows the prompt only once per application build/signature.  Calling
    this before an automatic frame session makes the first-use flow explicit;
    the user still has to enable Dewiar in System Settings when the prompt is
    unavailable or was previously denied.
    """
    from Quartz import CGPreflightScreenCaptureAccess, CGRequestScreenCaptureAccess
    if CGPreflightScreenCaptureAccess():
        return True
    try:
        CGRequestScreenCaptureAccess()
    except Exception:  # noqa: BLE001 — older macOS/PyObjC may not expose the request API
        pass
    return bool(CGPreflightScreenCaptureAccess())


def copy_image_data(url, board=None):
    """Write actual PNG bytes to the pasteboard, never a path or data-URL string."""
    import base64
    import io
    from PIL import Image
    from Foundation import NSData
    from AppKit import NSPasteboard
    if not isinstance(url, str) or not url.startswith('data:image/png;base64,') or len(url) > 90_000_000:
        raise ValueError('Нужен снимок в формате PNG')
    data = base64.b64decode(url.split(',', 1)[1], validate=True)
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
    board = board if board is not None else NSPasteboard.generalPasteboard()
    board.clearContents()
    if not board.setData_forType_(NSData.dataWithBytes_length_(data, len(data)), 'public.png'):
        raise ValueError('macOS не смогла скопировать картинку')
    return True


def clipboard_image(board=None):
    """PNG from the native pasteboard; a copied filename is not an image attachment."""
    import base64
    from AppKit import NSPasteboard, NSBitmapImageRep, NSBitmapImageFileTypePNG
    board = board if board is not None else NSPasteboard.generalPasteboard()
    data = board.dataForType_("public.png")
    if data is None:
        data = board.dataForType_("public.tiff")
        if data is None:
            return ""
        bitmap = NSBitmapImageRep.imageRepWithData_(data)
        if bitmap is None:
            return ""
        data = bitmap.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    return "data:image/png;base64," + base64.b64encode(bytes(data)).decode() if data else ""


def clip_secret(text, sec=30, board=None):
    """Expire only our pasteboard revision, leaving anything copied later untouched."""
    import threading
    from AppKit import NSPasteboard, NSPasteboardTypeString
    text = str(text or "")
    if not text:
        return False
    board = board if board is not None else NSPasteboard.generalPasteboard()
    board.clearContents()
    if not board.setString_forType_(text, NSPasteboardTypeString):
        return False
    revision = board.changeCount()

    def clear():
        if board.changeCount() == revision and board.stringForType_(NSPasteboardTypeString) == text:
            board.clearContents()
    timer = threading.Timer(max(5, min(300, int(sec or 30))), clear)
    timer.daemon = True
    timer.start()
    return True


# Only known browser identifiers enter AppleScript; never interpolate arbitrary app names.
_TAB_BROWSERS = {
    "com.apple.Safari": ("Safari", "safari"),
    "com.google.Chrome": ("Google Chrome", "chromium"),
    "ru.yandex.desktop.yandex-browser": ("Яндекс Браузер", "chromium"),
    "com.microsoft.edgemac": ("Microsoft Edge", "chromium"),
    "com.brave.Browser": ("Brave", "chromium"),
    "org.chromium.Chromium": ("Chromium", "chromium"),
    "com.vivaldi.Vivaldi": ("Vivaldi", "chromium"),
}


def transcription_browsers():
    """Running supported browsers only: listing never opens an application."""
    from AppKit import NSWorkspace
    running = {str(app.bundleIdentifier()) for app in NSWorkspace.sharedWorkspace().runningApplications()}
    return [{"id": key, "name": name} for key, (name, _) in _TAB_BROWSERS.items() if key in running]


def transcription_browser_url(browser_id):
    """Read the selected browser's front window, without switching apps or using the clipboard."""
    from urllib.parse import urlsplit
    if browser_id not in _TAB_BROWSERS:
        return {"error": "Этот браузер не поддерживается. Скопируйте ссылку и вставьте из буфера."}
    name, kind = _TAB_BROWSERS[browser_id]
    tab = "current tab" if kind == "safari" else "active tab"
    script = (f'tell application id "{browser_id}"\n'
              'if not running then return ""\n'
              'if (count of windows) is 0 then return ""\n'
              f'return URL of {tab} of front window\nend tell')
    try:
        result = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return {"error": "Браузер не ответил. Проверьте системный запрос доступа и попробуйте снова."}
    if result.returncode:
        return {"error": f"Не удалось прочитать вкладку {name}. Разрешите Dewiar доступ к браузеру в настройках macOS → Конфиденциальность и безопасность → Автоматизация, либо вставьте ссылку из буфера."}
    url = result.stdout.strip()
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in ("http", "https") and bool(parsed.hostname) and not any(c.isspace() for c in url)
    except ValueError:
        valid = False
    if not valid:
        return {"error": f"В {name} нет активной вкладки с веб-ссылкой. Откройте страницу с видео или аудио."}
    return {"url": url, "browser": name}
