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
    for root in map(Path, roots):
        for pattern in ("*.app", "*/*.app"):
            for bundle in sorted(root.glob(pattern)):
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


def screen_capture(source="screen", region=None, application=""):
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
