"""Cocoa-оболочка прототипа Mac: существующие страницы и отдельный процесс ядра.

Только публичные оконные методы pywebview; GTK и Win32 не импортируются.
Ядро завершается вместе с интерфейсом; LaunchAgent добавляется после приёмки памяти.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from settings import CONFIG_DIR, STATE_DIR, WS_PORT, ensure_token


def core_authenticated(port, token):
    """A listening TCP socket alone may belong to a different application."""
    from websockets.sync.client import connect
    try:
        with connect(f"ws://127.0.0.1:{port}", open_timeout=0.6, close_timeout=0.2, proxy=None) as ws:
            ws.send(json.dumps({"cmd": "hello", "token": token}))
            message = json.loads(ws.recv(timeout=1))
            return message.get("type") == "hello" and isinstance(message.get("data", {}).get("settings"), dict)
    except Exception:  # noqa: BLE001 — absent core, wrong token or unrelated server
        return False


def free_core_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def source_checkout_update(root, timeout=120):
    """Safely update a Dewiar checkout and return a small, UI-safe result.

    This is intentionally an allow-listed operation: the UI never accepts a shell
    command or a script path.  A dirty checkout is left untouched because an
    automatic pull must never overwrite a user's local work.
    """
    root = Path(root).resolve()
    if not (root / ".git").exists() or not (root / "tools" / "run_macos.sh").is_file():
        return {"ok": False, "reason": "not_source", "error": "исходная папка Dewiar не найдена"}

    def run(args, limit=timeout):
        env = os.environ.copy()
        # A GUI update must fail clearly instead of waiting for a hidden password
        # or GitHub credential prompt in the background.
        env["GIT_TERMINAL_PROMPT"] = "0"
        try:
            return subprocess.run(["git", *args], cwd=str(root), env=env, text=True,
                                  capture_output=True, timeout=limit, check=False)
        except FileNotFoundError:
            return None
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(["git", *args], 124, "", f"тайм-аут через {limit} с: {exc}")

    status = run(["status", "--porcelain"], limit=min(timeout, 20))
    if status is None:
        return {"ok": False, "reason": "git_missing", "error": "Git не установлен. Установите Xcode Command Line Tools."}
    if status.returncode != 0:
        return {"ok": False, "reason": "git_error", "error": (status.stderr or status.stdout).strip()[-500:] or "не удалось проверить репозиторий"}
    dirty = status.stdout.strip()
    if dirty:
        return {"ok": False, "reason": "dirty", "error": "есть незакоммиченные изменения — сначала сохраните или закоммитьте их", "files": dirty.splitlines()[:20]}

    before = run(["rev-parse", "HEAD"], limit=min(timeout, 20))
    branch = run(["branch", "--show-current"], limit=min(timeout, 20))
    if before is None or before.returncode != 0:
        return {"ok": False, "reason": "git_error", "error": "не удалось определить текущую версию"}
    before_sha = before.stdout.strip()
    branch_name = (branch.stdout.strip() if branch and branch.returncode == 0 else "") or "текущая ветка"
    pulled = run(["pull", "--ff-only"], limit=timeout)
    if pulled is None or pulled.returncode != 0:
        text = ((pulled.stderr if pulled else "") or (pulled.stdout if pulled else "")).strip()
        return {"ok": False, "reason": "pull_failed", "branch": branch_name,
                "error": text[-700:] or "git pull --ff-only завершился ошибкой"}
    after = run(["rev-parse", "HEAD"], limit=min(timeout, 20))
    version = run(["log", "-1", "--oneline"], limit=min(timeout, 20))
    after_sha = after.stdout.strip() if after and after.returncode == 0 else before_sha
    return {"ok": True, "changed": after_sha != before_sha, "branch": branch_name,
            "version": (version.stdout.strip() if version and version.returncode == 0 else after_sha)[:180],
            "output": pulled.stdout.strip()[-500:]}


def _core_request(token, cmd, args=None, timeout=3):
    """Send one authenticated command to the local core (used by native Mac menus)."""
    from websockets.sync.client import connect
    with connect(f"ws://127.0.0.1:{WS_PORT}", open_timeout=timeout, close_timeout=1, proxy=None) as ws:
        ws.send(json.dumps({"cmd": "hello", "token": token}))
        hello = json.loads(ws.recv(timeout=timeout))
        if hello.get("type") != "hello":
            raise RuntimeError("ядро Dewiar не подтвердило подключение")
        ws.send(json.dumps({"id": 1, "cmd": cmd, "args": dict(args or {})}))
        reply = json.loads(ws.recv(timeout=timeout))
        if reply.get("error"):
            raise RuntimeError(str(reply["error"]))
        return reply.get("data")


def install_meeting_status(api):
    """Install a small native status-bar controller for meetings running in background.

    This is deliberately kept outside pywebview: the prompter window may be hidden or
    closed while the core continues recording.  The item remains visible as Dewiar and
    changes to a recording timer while a meeting is active.
    """
    try:
        import objc
        from AppKit import NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength
        from Foundation import NSObject, NSTimer
    except Exception:  # pragma: no cover - only available in a packaged Cocoa build
        return None

    class MeetingStatusTarget(NSObject):
        def initWithApi_(self, owner):
            self = objc.super(MeetingStatusTarget, self).init()
            if self is not None:
                self.owner = owner
                self.state = {}
                self.status = None
                self.item = None
                self.header = None
                self.pause_item = None
                self.stop_item = None
                self.meetings_item = None
                self.audio_item = None
            return self

        # A leading-underscore method is treated by PyObjC as a selector with
        # zero arguments.  Use explicit selector naming here because this
        # callback receives both the command and its JSON arguments.
        def runCommand_args_(self, command, args):
            def work():
                try:
                    _core_request(self.owner.token, command, args)
                except Exception:
                    pass
                self.refresh_(None)
            threading.Thread(target=work, name="dewiar-menu-command", daemon=True).start()

        def open_(self, _sender):
            self.owner.open_prompter()

        def frames_(self, _sender):
            self.owner.open_prompter()

        def meetings_(self, _sender):
            self.owner.open_settings("prompter")

        def update_(self, _sender):
            self.owner.update_from_source()

        def pause_(self, _sender):
            self.runCommand_args_("prompter_pause", {"on": not bool(self.state.get("paused"))})

        def stop_(self, _sender):
            self.runCommand_args_("prompter_stop", {"summary": True})

        def audio_(self, _sender):
            recording = self.state.get("recording") or {}
            self.runCommand_args_("prompter_audio_pause", {"on": not bool(recording.get("audio_paused"))})

        def refresh_(self, _timer):
            try:
                state = _core_request(self.owner.token, "prompter_state", timeout=1.5) or {}
            except Exception:
                state = {}
            self.state = state
            on = bool(state.get("on"))
            meeting = state.get("meeting") or {}
            recording = bool(on and meeting.get("record"))
            schedules = state.get("schedules") or []
            upcoming = sorted((x for x in schedules if x.get("state") in ("scheduled", "starting", "error")),
                              key=lambda x: float(x.get("start_at") or 0))
            next_item = upcoming[0] if upcoming else None
            title = str(meeting.get("title") or "Встреча")
            if recording:
                started = float(meeting.get("started") or time.time())
                elapsed = max(0, int(time.time() - started))
                label = f"● Запись {elapsed // 60}:{elapsed % 60:02d}"
                self.item.button().setTitle_(label)
                self.item.button().setToolTip_(f"Идёт запись: {title}. Нажмите для управления.")
                self.header.setTitle_(f"Записывается: {title}")
            elif on:
                self.item.button().setTitle_("Dewiar")
                self.item.button().setToolTip_(f"Режим встречи работает: {title}")
                self.header.setTitle_(f"Режим встречи работает: {title}")
            elif next_item and next_item.get("state") == "error":
                reason = str(next_item.get("error") or (next_item.get("preflight") or {}).get("message") or "проверка не пройдена")
                self.item.button().setTitle_("⚠ Запись не готова")
                self.item.button().setToolTip_(f"{next_item.get('title', 'Встреча')}: {reason}")
                self.header.setTitle_(f"⚠ Запись не началась: {next_item.get('title', 'Встреча')}")
            elif next_item and next_item.get("state") == "starting":
                self.item.button().setTitle_("Запуск записи…")
                self.item.button().setToolTip_("Dewiar запускает запланированную запись")
                self.header.setTitle_(f"Запуск записи: {next_item.get('title', 'Встреча')}")
            elif next_item:
                ts = float(next_item.get("start_at") or 0)
                when = datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "—"
                self.item.button().setTitle_(f"Запись в {when}")
                pre = next_item.get("preflight") or {}
                suffix = " · проверка пройдена" if pre.get("state") == "ok" else " · проверка через 5 минут"
                self.item.button().setToolTip_(f"{next_item.get('title', 'Встреча')} начнётся в {when}{suffix}")
                self.header.setTitle_(f"Следующая запись: {next_item.get('title', 'Встреча')} · {when}")
            else:
                self.item.button().setTitle_("Dewiar")
                self.item.button().setToolTip_("Открыть Dewiar")
                self.header.setTitle_("Запись встречи не активна")
            active = bool(on)
            self.pause_item.setEnabled_(active)
            self.stop_item.setEnabled_(active)
            audio_enabled = bool(recording)
            self.audio_item.setEnabled_(audio_enabled)
            self.audio_item.setTitle_("Продолжить запись звука" if (state.get("recording") or {}).get("audio_paused") else "Пауза записи звука")
            if active:
                self.pause_item.setTitle_("Продолжить" if state.get("paused") else "Пауза")
            else:
                self.pause_item.setTitle_("Пауза")

    target = MeetingStatusTarget.alloc().initWithApi_(api)
    item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
    menu = NSMenu.alloc().init()
    header = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Запись встречи не активна", None, "")
    header.setEnabled_(False)
    menu.addItem_(header)
    menu.addItem_(NSMenuItem.separatorItem())
    open_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Открыть окно встречи", "open:", "")
    open_item.setTarget_(target); menu.addItem_(open_item)
    pause_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Пауза", "pause:", "")
    pause_item.setTarget_(target); menu.addItem_(pause_item)
    stop_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Завершить запись и сохранить итог", "stop:", "")
    stop_item.setTarget_(target); menu.addItem_(stop_item)
    audio_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Пауза записи звука", "audio:", "")
    audio_item.setTarget_(target); menu.addItem_(audio_item)
    menu.addItem_(NSMenuItem.separatorItem())
    frame_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Настройки кадров…", "frames:", "")
    frame_item.setTarget_(target); menu.addItem_(frame_item)
    meetings_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Открыть настройки встреч…", "meetings:", "")
    meetings_item.setTarget_(target); menu.addItem_(meetings_item)
    menu.addItem_(NSMenuItem.separatorItem())
    update_title = "Обновить и перезапустить Dewiar" if not getattr(sys, "frozen", False) else "Проверить обновления Dewiar"
    update_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(update_title, "update:", "")
    update_item.setTarget_(target); menu.addItem_(update_item)
    target.item, target.header, target.pause_item, target.stop_item, target.meetings_item, target.audio_item = item, header, pause_item, stop_item, meetings_item, audio_item
    item.setMenu_(menu)
    api._meeting_status = target
    target.refresh_(None)
    # Polling is intentional: it also works for schedules started with no window open.
    target.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1.0, target, "refresh:", None, True)
    return target


def make_macos_menu(api):
    """Return pywebview's persistent native menu for every Dewiar window."""
    from webview.menu import Menu, MenuAction, MenuSeparator

    update_title = "Обновить и перезапустить Dewiar" if not getattr(sys, "frozen", False) else "Проверить обновления Dewiar"
    actions = [
        MenuSeparator(),
        MenuAction("Показать агента", lambda: api.from_tray()),
        MenuAction("Открыть настройки", lambda: api.open_settings()),
        MenuAction("Модели", lambda: api.open_settings("brain")),
        MenuAction(update_title, lambda: api.update_from_source()),
    ]
    # ``__app__`` inserts actions into the standard macOS application menu;
    # the explicit top-level menu remains easy to find even if macOS localizes
    # the app-name menu title.
    return [Menu("__app__", actions), Menu("Агент Dewiar", actions[1:])]


def make_api(app):
    import webview
    from macsys import open_target

    class MacApi(app.Api):
        _mic_permission_lock = threading.RLock()

        def microphone_permission(self):
            from macsys import microphone_permission
            with self._mic_permission_lock:
                status=microphone_permission()
                if status['state']!='not_determined':self._mic_permission_pending=False
                if getattr(self,'_mic_permission_pending',False) and time.monotonic()-self._mic_permission_started>=60:
                    self._mic_permission_pending=False
                requested=bool(getattr(self,'_mic_permission_requested',False))
                pending=bool(getattr(self,'_mic_permission_pending',False))
                return dict(status,pending=pending,requested=requested,
                            retry_blocked=requested and not pending and status['state']=='not_determined',
                            request_supported=bool(getattr(sys, "frozen", False)))

        def request_microphone_access(self):
            with self._mic_permission_lock:
                status = self.microphone_permission()
                if not status["request_supported"]:
                    return status  # A source launch could grant access to Python/Terminal rather than Dewiar.
                if status["state"] != "not_determined" or status["pending"] or status['requested']:
                    return status
                self._mic_permission_requested=True
                self._mic_permission_pending=True
                self._mic_permission_started=time.monotonic()
            from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
            from PyObjCTools import AppHelper
            def completed(granted):
                with self._mic_permission_lock:self._mic_permission_pending = False

            def request():
                try:
                    AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, completed)
                except Exception:
                    with self._mic_permission_lock:self._mic_permission_pending = False
            AppHelper.callAfter(request)
            return self.microphone_permission()

        _contacts_lock = threading.RLock()

        def contacts_permission(self):
            from mac_contacts import permission
            with self._contacts_lock:
                status=permission();pending=bool(getattr(self,'_contacts_pending',False))
                if status['state']!='not_determined' or (pending and time.monotonic()-self._contacts_started>=60):
                    self._contacts_pending=False;pending=False
                return dict(status,pending=pending,requested=bool(getattr(self,'_contacts_requested',False)),request_supported=bool(getattr(sys,'frozen',False)))

        def request_contacts_access(self):
            with self._contacts_lock:
                status=self.contacts_permission()
                if not status['request_supported'] or status['state']!='not_determined' or status['pending'] or status['requested']:return status
                self._contacts_requested=True;self._contacts_pending=True;self._contacts_started=time.monotonic()
            from Contacts import CNContactStore,CNEntityTypeContacts
            from PyObjCTools import AppHelper
            def completed(granted,error):
                with self._contacts_lock:self._contacts_pending=False
            def request():
                try:
                    self._contacts_native_store=CNContactStore.alloc().init()
                    self._contacts_native_store.requestAccessForEntityType_completionHandler_(CNEntityTypeContacts,completed)
                except Exception:
                    with self._contacts_lock:self._contacts_pending=False
            AppHelper.callAfter(request)
            return self.contacts_permission()

        def open_contacts_settings(self):
            open_target("x-apple.systempreferences:com.apple.preference.security?Privacy_Contacts")
            return True

        def accessibility_permission(self):
            from mac_control import permission
            return dict(permission(), packaged=bool(getattr(sys, "frozen", False)))

        def open_accessibility_settings(self):
            open_target("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
            return True

        def open_microphone_settings(self):
            open_target("x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
            return True

        def open_sound_settings(self):
            open_target("x-apple.systempreferences:com.apple.preference.sound")
            return True

        def screen_capture_permission(self):
            """Return the Screen Recording status for this running Dewiar process."""
            try:
                from Quartz import CGPreflightScreenCaptureAccess
                granted = bool(CGPreflightScreenCaptureAccess())
            except Exception as e:  # noqa: BLE001 — older macOS/PyObjC
                return {"state": "unknown", "granted": False, "error": str(e)}
            return {"state": "authorized" if granted else "denied", "granted": granted}

        def open_screen_settings(self):
            open_target("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")
            return True

        def get_pos(self, mode="card"):
            return [self._hud.x, self._hud.y] if self._hud else [0, 0]

        def move_to(self, mode, x, y):
            self._hud.move(int(x), int(y))

        def _ensure_hud(self):
            """Return the HUD, recreating it if the user closed its window."""
            if self._hud is not None:
                try:
                    self._hud.show()
                    return self._hud
                except Exception:
                    self._hud = None
            window = webview.create_window(
                "Dewiar", self._url("hud.html", "&mode=card"), width=340, height=500,
                min_size=(180, 250), js_api=self, on_top=True,
                background_color="#0b0b0e", menu=getattr(self, "_mac_menu_spec", []),
            )
            self._hud = window
            self.wins["card"] = window

            def closed():
                if self._hud is window:
                    self._hud = None
                    self.wins.pop("card", None)

            window.events.closed += closed
            return window

        def _activate(self, mode="card"):
            hud = self._ensure_hud()
            hud.show()
            hud.restore()

        def show(self, mode="card"):
            self.from_tray()

        def hide(self, mode="card"):
            self._hud.hide()

        def _hide_now(self, mode="card"):
            self.hide(mode)

        def to_tray(self, quiet=False):
            self.in_tray = True
            self.hide()

        def from_tray(self, quiet=False):
            self.in_tray = False
            self._activate()

        def bring_back(self):
            self.from_tray()

        def tray_state(self):
            return {"in_tray": self.in_tray, "icon": False}

        def set_mode(self, mode="card", auto=False):
            self.mode = "card"
            self.from_tray()

        def card_size(self):
            return [self._hud.width, self._hud.height]

        def resize_card(self, w, h, final=False):
            self._hud.resize(max(280, min(640, int(w))), max(360, min(1000, int(h))))

        def set_zoom(self, z):
            if app.set_zoom_sizes(z):
                self._hud.resize(*app.SIZES["card"])
            return self.get_pos()

        def clipboard_image(self):
            if os.environ.get("DEWIAR_MAC_SMOKE_FEATURES") and os.environ.get("DEWIAR_MAC_SMOKE_REPORT"):
                return ""  # GUI probes must not copy personal pasteboard contents into their fixture.
            from macsys import clipboard_image
            return clipboard_image()

        def clip_secret(self, text, sec=30):
            from macsys import clip_secret
            return clip_secret(text, sec)

        def _mac_panel(self, attr, title, page, width, height, extra=""):
            old = getattr(self, attr, None)
            if old is not None:
                old.show()
                old.restore()
                return old
            window = webview.create_window(title, self._url(page, extra), width=width, height=height,
                                           min_size=(280, 260), on_top=True, js_api=self,
                                           background_color="#12131b", menu=self._mac_menu_spec)
            setattr(self, attr, window)

            def closed():
                if getattr(self, attr, None) is window:
                    setattr(self, attr, None)
                    if attr == "_pad":
                        self._pad_on = False
                    elif attr == "_capture":
                        self._capture_shown = False
            window.events.closed += closed
            return window

        def show_capture(self):
            if getattr(self, "_capture_shown", False):
                self.close_capture()
                return True
            self._capture_shown = True
            win = self._mac_panel("_capture", app.CAPTURE_TITLE, "capture.html", 600, 440)
            win.events.loaded += lambda: win.evaluate_js("window.capOpen && capOpen()")
            # A reused window is already loaded; restore its draft and clipboard preview.
            win.evaluate_js("window.capOpen && capOpen()")
            return True

        def capture_status(self):
            message = "На Mac пока открывайте «Закинуть идею» кнопкой в приложении; глобальное сочетание ещё не перенесено"
            return {"on": False, "key": "", "works": False, "command": "", "how": message, "error": message}

        def capture_set(self, on=True, key=""):
            return self.capture_status()

        def open_prompter(self):
            self._mac_panel("_prompter", app.PROMPTER_TITLE, "prompter.html", 440, 680)
            return True

        def pad_open(self, mode="notes"):
            self._pad_mode = "play" if mode == "play" else "notes"
            self._pad_on = True
            win = self._mac_panel("_pad", app.PAD_TITLE, "pad.html", 540, 600,
                                  "&side=left&gap=0&mode=" + self._pad_mode)
            win.evaluate_js("window.padMode && padMode(" + json.dumps(self._pad_mode) + ")")
            self._js("window.padMark && padMark(true, " + json.dumps(self._pad_mode) + ")")
            return True

        def pad_hide(self):
            self._pad_on = False
            if getattr(self, "_pad", None):
                self._pad.hide()
            self._js("window.padMark && padMark(false)")

        def pad_size(self):
            win = getattr(self, "_pad", None)
            return [win.width, win.height] if win else [540, 600]

        def pad_resize(self, w, h, final=False):
            if getattr(self, "_pad", None):
                self._pad.resize(max(280, min(1200, int(w))), max(260, min(1200, int(h))))
            return self.pad_size()

        def pad_flip(self):
            return False  # Native Mac panel is independent of the HUD.

        def pad_full(self, on):
            on = bool(on)
            if getattr(self, "_pad", None) and on != getattr(self, "_pad_full", False):
                self._pad.toggle_fullscreen()
                self._pad_full = on
            return on

        def subscription_claude_login(self):
            # Fixed official executable and fixed /login command; no user-controlled shell text.
            import shlex
            import tempfile
            from subscriptions import executable, _env
            cli = executable("claude")
            fd, path = tempfile.mkstemp(prefix="dewiar-claude-login-", suffix=".command")
            script = "#!/bin/zsh\nrm -- " + shlex.quote(path) + "\ncd /private/tmp\n"
            script += "unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_OAUTH_TOKEN CLAUDECODE\n"
            script += shlex.quote(cli) + " --safe-mode --setting-sources '' /login\n"
            with os.fdopen(fd, "w") as f:
                f.write(script)
            os.chmod(path, 0o700)
            subprocess.Popen(["/usr/bin/open", "-a", "Terminal", path], env=_env("claude"))
            return True

        def open_url(self, url):
            from urllib.parse import urlparse
            if urlparse(str(url)).scheme not in ("http", "https", "mailto", "obsidian", "tg"):
                raise ValueError("неподдерживаемая схема адреса")
            open_target(url)
            return True

        def open_settings(self, page="", query=""):
            if self._studio is not None:
                if query:
                    self._studio.load_url(self._url("settings.html", query))
                self._studio.show()
                self._studio.restore()
                if page:
                    self._studio.evaluate_js("document.querySelector(" + json.dumps(f'nav button[data-p="{page}"]') + ")?.click()")
                return
            w, h = self._studio_size()
            self._studio = webview.create_window("Dewiar — Студия", self._url("settings.html", query),
                                                 width=w, height=h, min_size=(640, 460), js_api=self,
                                                 background_color="#0b0b0e", menu=self._mac_menu_spec)
            self._studio.events.closed += self._mac_studio_closed
            if page:
                self._studio.events.loaded += lambda: self._studio.evaluate_js(
                    "document.querySelector(" + json.dumps(f'nav button[data-p="{page}"]') + ")?.click()")

        def _mac_studio_closed(self):
            self._studio = None

        def _raise_studio(self):
            if self._studio:
                self._studio.show()
                self._studio.restore()

        def studio_min(self):
            if self._studio:
                self._studio.minimize()

        def studio_max(self):
            if self._studio:
                self._studio.toggle_fullscreen()

        def studio_close(self):
            if self._studio:
                self._studio.destroy()

        def _source_notice(self, text, error=False):
            """Show a transient status in the agent window and keep a terminal log."""
            print(f"Dewiar update: {text}", file=sys.stderr, flush=True)
            code = "(window.showCap && (showCap(null, " + json.dumps(str(text), ensure_ascii=False) + ", " + ("true" if error else "false") + "), window.hideCapLater && hideCapLater(9000)))"
            self._js(code)

        def source_update_status(self):
            state = getattr(self, "_source_update", None)
            if state is None:
                return {"running": False, "source": not bool(getattr(sys, "frozen", False))}
            return dict(state)

        def update_from_source(self):
            """Pull the current source branch and restart only after a fast-forward.

            Packaged builds deliberately do not run Git; their normal signed updater
            remains in the «Обновления» section.  Source builds use the same command
            that was previously typed in Terminal, but only this fixed action is
            exposed to the UI.
            """
            if getattr(sys, "frozen", False):
                result = {"ok": False, "source": False, "error": "Это установленная сборка. Откройте раздел «Обновления» и используйте официальный обновлятор."}
                self._source_update = {"running": False, **result}
                self._source_notice(result["error"], error=True)
                return result
            current = getattr(self, "_source_update", None) or {}
            if current.get("running"):
                return dict(current)
            root = Path(app.ROOT).resolve()
            self._source_update = {"running": True, "stage": "checking", "root": str(root), "started": time.time()}
            self._source_notice("Проверяю исходники Dewiar…")

            def work():
                result = source_checkout_update(root)
                if not result.get("ok"):
                    self._source_update = {"running": False, "stage": "error", **result}
                    if result.get("reason") == "dirty":
                        detail = "Сначала сохраните изменения в Git, затем повторите обновление."
                    else:
                        detail = result.get("error") or "обновление не выполнено"
                    self._source_notice("Обновление не выполнено: " + detail, error=True)
                    return
                self._source_update = {"running": False, "stage": "ready", **result}
                if not result.get("changed"):
                    self._source_notice("Уже установлена последняя версия Dewiar (" + result.get("version", "") + ").")
                    return
                self._source_update = {"running": True, "stage": "restarting", **result}
                self._source_notice("Dewiar обновлён. Перезапускаю приложение…")
                time.sleep(0.6)
                self.restart("Dewiar обновлён из GitHub.")

            threading.Thread(target=work, name="dewiar-source-update", daemon=True).start()
            return {"ok": True, "started": True, "source": True}

        def install_update(self, *args):
            return {"ok": False, "error": "Обновление Mac-прототипа пока выполняется вручную"}

        def restart_core(self):
            return {"ok": False, "error": "Перезапустите Mac-прототип целиком"}

        def show_alarm(self, payload=None):
            payload = payload if isinstance(payload, dict) else {}
            old = getattr(self, "_alarm", None)
            rid = str(payload.get("id") or "")
            if old:
                if getattr(self, "_alarm_id", "") == rid:
                    old.show()
                    old.restore()
                    return
                old.destroy()
            self._alarm_id = rid
            query = dict(payload, rep=payload.get("repeat_text", ""), late="1" if payload.get("late") else "")
            window = webview.create_window("Dewiar — Напоминание", self._url("alarm.html", "&" + urlencode(query)),
                                                width=420, height=400, on_top=True, js_api=self,
                                                background_color="#0b0b0e", menu=self._mac_menu_spec)
            self._alarm = window

            def closed():
                if getattr(self, "_alarm", None) is window:
                    self._alarm = None
            window.events.closed += closed

        def close_alarm(self, rid=""):
            if rid and rid != getattr(self, "_alarm_id", ""):
                return
            if getattr(self, "_alarm", None):
                old = self._alarm
                self._alarm = None
                old.destroy()

        def quit_all(self):
            for window in list(webview.windows):
                window.destroy()

        def open_skills_folder(self, path=""):
            target = CONFIG_DIR / "skills"
            target.mkdir(parents=True, exist_ok=True)
            open_target(target)

    return MacApi


def main_mac(args, extra, app):
    global WS_PORT
    import fcntl
    import webview
    import settings
    try:
        saved_voice = json.loads(settings.SETTINGS_PATH.read_text()).get("audio", {}).get("voice_enabled", False)
    except (OSError, ValueError):
        saved_voice = False
    # AVFoundation must first be loaded after Cocoa/NSApplication starts. Loading it here
    # can stall later permission requests when restoring a saved voice preference.
    wanted_voice = not args.text_only and (getattr(args, "enable_voice", False) or saved_voice)
    args.text_only = True
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    held = (STATE_DIR / "ui.lock").open("w")
    try:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise RuntimeError("Dewiar уже запущен: завершите предыдущий экземпляр")
    process = None
    stream = None
    token = ensure_token()
    own_core = False
    if app.core_alive():
        if os.environ.get("DEWIAR_PORT"):
            own_core = core_authenticated(WS_PORT, token)
            if not own_core:
                raise RuntimeError(f"Порт {WS_PORT} занят другой программой; выберите свободный DEWIAR_PORT")
        else:
            # Do not send our token to an unknown WebSocket service on the default port.
            WS_PORT = free_core_port()
            settings.WS_PORT = app.WS_PORT = WS_PORT
    os.environ["DEWIAR_MAC_DESKTOP"] = "1"
    os.environ["DEWIAR_PORT"] = str(WS_PORT)
    if not own_core:
        logs = STATE_DIR / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stream = (logs / "mac-launch.log").open("a", encoding="utf-8")
        command = ([sys.executable, "--core"] if getattr(sys, "frozen", False)
                   else [sys.executable, str(app.ROOT / "core/main.py")])
        if args.text_only:
            command.append("--server")
        process = subprocess.Popen(command, cwd=app.ROOT, stdout=stream, stderr=stream, start_new_session=True)
        deadline = time.monotonic() + 25
        while not core_authenticated(WS_PORT, token):
            if process.poll() is not None or time.monotonic() > deadline:
                if process.poll() is None:
                    process.terminate()
                raise RuntimeError(f"Ядро не запустилось; журнал: {logs / 'mac-launch.log'}")
            time.sleep(0.1)
    port_file = STATE_DIR / "core-port.json"
    port_file.write_text(json.dumps({"port": WS_PORT, "ui_pid": os.getpid()}), encoding="utf-8")
    os.chmod(port_file, 0o600)
    api = make_api(app)(token)
    api._mac_menu_spec = make_macos_menu(api)
    api._ensure_hud()
    if args.settings or getattr(args, "request_microphone", False) or not app.SETTINGS_PATH.exists():
        api.open_settings("sound" if getattr(args, "request_microphone", False) else "", extra)

    def ready():
        # Native status item stays available when the prompter window is hidden or closed.
        # pywebview calls its ready callback from a worker thread, while AppKit
        # requires NSStatusItem/NSMenu creation on the Cocoa main thread.
        if not getattr(api, "_meeting_status", None) and not getattr(api, "_meeting_status_pending", False):
            api._meeting_status_pending = True
            from PyObjCTools import AppHelper
            def install_status_on_main():
                try:
                    api._meeting_status = install_meeting_status(api)
                except Exception as e:  # noqa: BLE001 — status bar must not stop the assistant
                    print(f"Статус встречи не установлен: {e}", file=sys.stderr)
                finally:
                    api._meeting_status_pending = False
            AppHelper.callAfter(install_status_on_main)
        report = os.environ.get("DEWIAR_MAC_SMOKE_REPORT")
        if not report and wanted_voice and api.microphone_permission().get("granted"):
            app._core_call(token, "audio_enable", timeout=15)
        if getattr(args, "request_microphone", False) and not report:
            permission = api.request_microphone_access()
            if permission["state"] in ("denied", "restricted"):
                api.open_microphone_settings()
        if report:
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    state = json.loads(api._hud.evaluate_js("JSON.stringify({title:document.title,body:document.body.innerText.length,bridge:!!window.pywebview.api,connected:!!window.core?.connected,text_mode:document.getElementById('say')?.textContent === 'Текстовый режим',mic_disabled:!!document.getElementById('b-mic')?.disabled,mic_switch:document.getElementById('b-mic')?.title.startsWith('Включить голосовой режим')})"))
                    state["studio"] = bool(api._studio and api._studio.evaluate_js(
                        "!!window.core?.connected && !!window.pywebview?.api && document.body.innerText.length > 100"))
                    state["mic_permission"] = api.microphone_permission()["state"]
                    state["contacts_permission"] = api.contacts_permission()["state"]
                    state["mic_permission_ui"] = bool(api._studio and api._studio.evaluate_js("document.getElementById('mac-mic-access').style.display === 'block'"))
                    state["subscription_ui"] = bool(api._studio and api._studio.evaluate_js("document.querySelectorAll('#subscription-cards [data-a=use]').length === 2 && document.querySelectorAll('#subscription-cards [data-a=model]').length === 2"))
                    if state.get("connected") and state["studio"] and state["text_mode"] and (state["mic_disabled"] or state["mic_switch"]) and state["mic_permission_ui"]:
                        break
                    time.sleep(0.1)
                # Exercise real WebKit controls with a metadata fixture, never a real subscription.
                fixture = Path(__file__).resolve().parents[1] / "tools" / "subscription_ui_smoke.js"
                if fixture.exists() and api._studio:
                    api._studio.evaluate_js(fixture.read_text())
                    until = time.monotonic() + 10
                    while time.monotonic() < until:
                        result = api._studio.evaluate_js("window.__subscriptionSmoke || {}")
                        if result.get("done"):
                            state["subscription_flow"] = result
                            break
                        time.sleep(.1)
                # Exercise the real HUD controls in both network modes without changing settings.
                api._hud.evaluate_js("""window.__hudControlsSmoke = null; (async () => {
                    const savedModels = hudModels, savedCall = core.call, savedApi = window.api;
                    const savedLocal = document.getElementById('corner-l').classList.contains('lo');
                    const visited = []; const result = {};
                    try {
                        window.api = () => ({open_settings: page => visited.push(page)});
                        setLocal(false);
                        const shield = document.getElementById('b-lo'), brain = document.getElementById('b-model');
                        result.external_shield = !shield.hidden && shield.title.includes('разрешены') && getComputedStyle(document.getElementById('corner-l')).opacity === '1';
                        shield.click(); setLocal(true);
                        result.local_shield = !shield.hidden && shield.title.includes('запрещены');
                        setModelSettings({active:'fixture',profiles:{fixture:{model:'fixture-model',name:'Local'}}});
                        core.call = async () => ({automatic:false});
                        await refreshModelHint();
                        result.model_hint = brain.title.includes('fixture-model');
                        brain.click(); result.navigation = visited.join(',') === 'safety,brain';
                        hudModels.auto_select = true;
                        core.call = async () => ({automatic:true,next:null,selected:{model:'old-model'}});
                        await refreshModelHint(); result.unavailable = brain.title.includes('нет доступной модели') && brain.title.includes('Последний запрос: old-model');
                        window.__hudControlsSmoke = result;
                    } catch (error) {
                        window.__hudControlsSmoke = {failed:false};
                    } finally {
                        window.api = savedApi; core.call = savedCall; setLocal(savedLocal);
                        setModelSettings(savedModels); refreshModelHint();
                    }
                })()""")
                until = time.monotonic() + 5
                while time.monotonic() < until:
                    hud_result = api._hud.evaluate_js('window.__hudControlsSmoke')
                    if hud_result:
                        break
                    time.sleep(.1)
                state['hud_controls'] = hud_result or {'timeout':False}
                if not all(state['hud_controls'].values()):
                    raise RuntimeError('HUD security/model controls failed: '+str(state['hud_controls']))
                api._hud.evaluate_js("document.getElementById('b-model').click()")
                until = time.monotonic() + 5
                while time.monotonic() < until:
                    if api._studio.evaluate_js("!!document.querySelector('#p-brain.cur')"):
                        state['hud_controls']['native_models_navigation'] = True
                        break
                    time.sleep(.1)
                if not state['hud_controls'].get('native_models_navigation'):
                    raise RuntimeError('HUD did not open Models settings')
                if os.environ.get("DEWIAR_MAC_SMOKE_FEATURES"):
                    def loaded(window):
                        until = time.monotonic() + 12
                        while time.monotonic() < until:
                            if window.evaluate_js("!!window.core?.connected && !!window.pywebview?.api && document.body.innerText.length > 50"):
                                return True
                            time.sleep(0.1)
                        return False

                    api.show_capture()
                    state["capture"] = loaded(api._capture)
                    api._capture.evaluate_js("document.getElementById('t').value='Mac video acceptance idea'; document.getElementById('go').click()")
                    until = time.monotonic() + 8
                    while time.monotonic() < until:
                        if api._capture.evaluate_js("document.getElementById('done').classList.contains('on')"):
                            break
                        time.sleep(0.1)
                    idea_root = Path(os.environ["DEWIAR_WORKSPACE_DIR"]) / "Заметки"
                    state["capture_saved"] = any("Mac video acceptance idea" in p.read_text(encoding="utf-8") for p in idea_root.rglob("*.md"))
                    # Wait out the page's delayed hide before testing reopen.
                    time.sleep(1.5)
                    api.close_capture()
                    api.show_capture()
                    state["capture_reopened"] = loaded(api._capture) and api._capture_shown
                    api.close_capture()
                    api.pad_open()
                    state["pad"] = loaded(api._pad)
                    api.pad_hide()
                    api.pad_open()
                    state["pad_reopened"] = loaded(api._pad) and api.pad_state()["open"]
                    api.open_prompter()
                    state["prompter"] = loaded(api._prompter)
                    api.show_alarm({"text": "Mac video acceptance reminder", "repeat_text": "каждый день", "late": True})
                    state["alarm"] = loaded(api._alarm) and api._alarm.evaluate_js("document.getElementById('text').textContent === 'Mac video acceptance reminder' && document.getElementById('sub').textContent.includes('каждый день') && document.getElementById('kind').textContent === 'Пропущенное напоминание'")
                    api.set_zoom(1.2)
                    until = time.monotonic() + 3
                    while api._hud.width != app.SIZES["card"][0] and time.monotonic() < until:
                        time.sleep(0.1)
                    state["zoom"] = api._hud.width == app.SIZES["card"][0]
                    state["zoom_size"] = [api._hud.width, api._hud.height, list(app.SIZES["card"])]
                    api.set_zoom(0.75)
                    until = time.monotonic() + 3
                    while api._hud.width != app.SIZES["card"][0] and time.monotonic() < until:
                        time.sleep(0.1)
                    state["zoom_small"] = api._hud.width == app.SIZES["card"][0]
                state = json.dumps(state)
                Path(report).write_text(str(state), encoding="utf-8")
            finally:
                api.quit_all()
    try:
        webview.start(ready, gui="cocoa", debug=bool(os.environ.get("DEWIAR_UI_DEBUG")))
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if stream:
            stream.close()
        port_file.unlink(missing_ok=True)
        held.close()
