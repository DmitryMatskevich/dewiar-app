"""Суфлёр: беззвучные подсказки на встрече (окно ui/prompter.html, открывает ui/app.py → open_prompter).

Как работает:
  • Онлайн-встреча — два источника речи. Собеседник — звук компьютера (audio_io.LoopbackCapture → свой VAD → фразы),
    вы — обычный микрофон ядра: пока суфлёр включён, Engine._on_utterance отдаёт фразы сюда (feed_me), ассистент
    молчит и на имя не откликается (_meeting_ignores). Если голос владельца запомнен — фразы микрофона с чужим голосом
    (собеседник из колонок просочился в микрофон) отбрасываем: их уже слышно по звуку компьютера.
  • Очная встреча — один микрофон на всех: кто говорит, решает голос (speaker.py: вектор голоса фразы против
    отпечатка владельца; голоса собеседников копятся по ходу встречи). Отпечаток — один раз, «Запомнить мой голос»:
    четыре фразы вслух (enroll_*), звук не хранится.
  • Каждая фраза → распознавание тем же движком, что у ассистента (current_stt) → строка расшифровки в окно.
  • Подсказка — когда собеседник спросил или возразил (wants_hint), когда вы сами тянете время («хороший вопрос»,
    «сейчас уточню», «э-э, ну, как бы»), или по кнопке «Подсказать» в окне. Новая подсказка отменяет недописанную.
  • «Замешкались» (поток prompter-watch): собеседник спросил, а вы молчите дольше STUCK_S — подсказка к вопросу
    выделяется («Начните так»), а если её не было — пишется. Молчат оба дольше PAUSE_S — тихая подсказка «что сказать
    дальше» к цели встречи (не чаще раза в полторы минуты). Только в режиме «на вопросы и возражения».
  • Ответ модели (активный «Модели», потоком, без действий) — короткой карточкой: первая строка — главное, 2–4 пункта,
    «Источник: файл». Материалы встречи (файлы, которые положили в окно) режутся на отрывки и ищутся по словам
    (BM25 из knowledge.py); по желанию — ещё и в «Знаниях». Пока модель пишет, мгновенно показываем ответ из
    шпаргалки, если вопрос на неё похож.
  • Шпаргалка (вероятные вопросы и ответы) готовится в фоне сразу после старта, если есть цель или материалы.
  • Стоп → итог встречи (договорились, кто что делает, открытые вопросы) — в окно и файлом в «Встречи/».
По умолчанию звук не сохраняется. В онлайн-встрече можно явно включить запись системного звука без захвата микрофона Dewiar;
после остановки запись транскрибируется целиком и превращается в протокол в «Встречи/».
Событие окну одно — «prompter» {kind: state | line | hint | sheet | summary | open | note, …}."""
import math
import io
import re
import shutil
import subprocess
import threading
import time
import wave
import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

from engine_base import DATA_DIR, FRAME_S, STATE_DIR, log

# вопрос или возражение собеседника — повод подсказать
Q_RX = re.compile(r"\?\s*$|^(?:ну\s+|а\s+|и\s+|так\s+)?(?:как|что|сколько|почему|зачем|когда|где|куда|откуда|какой|какая|какое|какие|каким|"
                  r"какую|кто|чей|чем|можно ли|можете|могли бы|расскажите|объясните|поясните|уточните|а если|правда ли|"
                  r"есть ли|будет ли|насколько|в чём|в чем)\b", re.I)
OBJ_RX = re.compile(r"\b(?:дорого\w*|не уверен\w*|сомнева\w*|у других|у конкурент\w*|конкурент\w*|подума(?:ем|ть|ю)|"
                    r"не (?:нужно|интересно|подходит|готовы|потянем)|скидк\w*|гаранти\w*|риск\w*|сроки|бюджет\w*|слишком)\b", re.I)
# вы сами тянете время — тоже повод
STALL_RX = re.compile(r"\b(?:хороший вопрос|дайте подумать|сейчас уточню|надо уточнить|секундочку|минуточку|"
                      r"как бы (?:это|вам) сказать|не готов сразу|сложно сказать|затрудняюсь)\b", re.I)
# слова-паразиты: много их в одной фразе — человек подбирает слова
FILLERS = {"э", "ээ", "эээ", "эм", "эмм", "мм", "ммм", "м", "ну", "короче", "типа", "вот", "значит", "получается",
           "собственно", "какбы", "вобщем", "таксказать"}
STUCK_S = 5.0                   # собеседник спросил, вы молчите — столько секунд, и подсказка выделяется
PAUSE_S = 10.0                  # молчат оба — столько секунд, и тихая подсказка «что сказать дальше»
PAUSE_AFTER = 60.0              # …но не в первую минуту встречи (знакомятся, настраивают звук)
ENROLL_PHRASES = ["Добрый день! Сегодня у меня рабочее настроение.", "Давайте обсудим условия, сроки и стоимость проекта.",
                  "Я предлагаю начать с короткого пилота на месяц.", "Если появятся вопросы, пишите мне в любое время."]

HINT_SYSTEM = (
    "Ты — незаметный суфлёр на деловой встрече. Человек сейчас разговаривает с собеседником и одним взглядом читает "
    "твою подсказку на экране. Подскажи ЕМУ, что ответить или на что опереться.\n"
    "Формат строго:\n"
    "1-я строка — главное, что сказать: до 14 слов, без вступлений, без «Вы можете сказать».\n"
    "Затем 2–4 строки, каждая начинается с «• », по 3–12 слов: цифры, факты, аргументы, встречный вопрос.\n"
    "Последняя строка — «Источник: <имя файла>», если опирался на МАТЕРИАЛЫ.\n"
    "Цифры, цены, сроки и условия — только из МАТЕРИАЛОВ. Нет ответа в материалах — опирайся на общие знания и начни "
    "первую строку с «≈ ». Не выдумывай имён и цен. Если подсказывать нечего (реплика — не вопрос и не возражение) — "
    "ответь одним символом «—». Пиши по-русски.")
SHEET_SYSTEM = (
    "Подготовь шпаргалку к деловой встрече: 6–10 вопросов или возражений, которые вероятнее всего прозвучат от "
    "собеседника, и короткий ответ на каждый — по материалам и цели. Строго парами строк:\n"
    "В: вопрос собеседника\nО: что ответить, до 25 слов\nЦифры — только из материалов. Без вступлений и заключений.")
SUMMARY_SYSTEM = (
    "Ты пишешь итог деловой встречи по расшифровке (реплики «Собеседник» и «Вы»). Markdown, без вступлений. "
    "## Коротко — 2–3 предложения.\n## Договорились — пункты.\n## Кто что делает — пункты (сроки, если звучали).\n"
    "## Открытые вопросы — пункты.\nТолько то, что было сказано. Раздела нет по сути — напиши «нет».")

MAX_FILE = 30 * 1024 * 1024
TEXT_EXTS = {".md", ".txt", ".html", ".htm", ".pdf", ".docx", ".odt", ".csv"}


def wants_hint(text, who="them"):
    """Повод подсказать: вопрос или возражение собеседника; у вас — «хороший вопрос», «сейчас уточню»."""
    t = (text or "").strip()
    if len(t.split()) < (2 if who == "me" else 3):
        return False
    if who == "me":
        return bool(STALL_RX.search(t)) or stalling(t)
    return bool(Q_RX.search(t) or OBJ_RX.search(t))


def stalling(text):
    """Фраза из слов-паразитов: «ну, э-э, как бы, вот…» — не меньше трёх и не меньше половины слов."""
    t = re.sub(r"\b(как) (бы)\b|\b(в) (общем)\b|\b(так) (сказать)\b", lambda m: "".join(g for g in m.groups() if g), (text or "").lower())
    ws = re.findall(r"[а-яё]+", t)
    n = sum(1 for w in ws if w in FILLERS)
    return n >= 3 and n / max(1, len(ws)) >= 0.5


def parse_sheet(text):
    """«В: …» / «О: …» -> [{q, a}]."""
    out, q = [], None
    for ln in (text or "").splitlines():
        ln = ln.strip().lstrip("-•* ").strip()
        m = re.match(r"^(?:В|Вопрос)\s*[:.)]\s*(.+)$", ln)
        if m:
            q = m.group(1).strip()
            continue
        m = re.match(r"^(?:О|Ответ)\s*[:.)]\s*(.+)$", ln)
        if m and q:
            out.append({"q": q, "a": m.group(1).strip()})
            q = None
    return out[:14]


class Prompter:
    def __init__(self, eng):
        self.eng = eng
        self.on = False
        self.paused = False
        self.meeting = {}
        self.files = []          # [{name, chars, chunks}]
        self.lines = []          # [{who, text, t}]
        self.hints = []          # [{id, q, text, done, instant, src, ts, why}]
        self.sheet = []
        self.summary = ""
        self.status = ""         # что слушаем / почему не слышим собеседника
        self._lock = threading.RLock()
        self._q = None
        self._stop = threading.Event()
        self._hint_cancel = None
        self._n = 0
        self.enrolling = None    # запись голоса: [pcm, …] или None
        self._speaker = None
        self._diar = None
        self._await = None       # вопрос собеседника, на который ждём ответа: {t, q, hint}
        self._them_voice_t = 0.0
        self._pause_hint_t = 0.0
        self._t0 = 0.0
        self._record_wave = None
        self._record_path = ""
        self._record_error = ""
        # Ставится отдельно от общей паузы встречи: пользователь может
        # временно убрать звук из WAV, продолжая получать расшифровку и
        # автокадры.
        self._record_paused = False
        self._transcript_path = ""
        self._session_dir = None
        self._screenshots = []
        self._frame_events = []
        self._last_frame_sig = None
        self._frame_thread = None
        self._frames_enabled = False
        self._frame_interval = 0.5
        self._frame_quality = 85
        self._video_enabled = False
        self._video_dir = None
        self._video_frames = 0
        self._video_path = ""
        self._video_export_busy = False
        self._record_lock = threading.RLock()
        self._loop_cap = None
        self._loop_thread = None
        self._meeting_stt_obj = None
        # Настройки и расписание записи хранятся отдельно от общей истории:
        # закрытие окна суфлёра не отменяет фоновую встречу.
        cfg = self.eng.s.get("prompter") if hasattr(self.eng.s, "get") else {}
        cfg = cfg or {}
        self._capture_source = str(cfg.get("capture_source") or "screen")
        self._capture_region = dict(cfg.get("capture_region") or {})
        self._capture_application = str(cfg.get("capture_application") or "")
        self._schedules_lock = threading.RLock()
        self._schedules_path = STATE_DIR / "prompter-schedules.json"
        self._schedules = self._load_schedules()
        self._schedule_thread = threading.Thread(target=self._schedule_loop, name="prompter-scheduler", daemon=True)
        self._schedule_thread.start()

    def _load_schedules(self):
        try:
            raw = json.loads(self._schedules_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else []
        except (OSError, ValueError, TypeError):
            return []

    def _save_schedules(self):
        try:
            self._schedules_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._schedules_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._schedules, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._schedules_path)
        except OSError as exc:
            log.warning("🎬 Расписание записи не сохранено: %s", exc)

    @staticmethod
    def _schedule_ts(value):
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or "").strip().replace("Z", "+00:00")
        if not text:
            raise ValueError("укажите время начала")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            return dt.timestamp()
        return dt.timestamp()

    def schedules(self):
        with self._schedules_lock:
            return [dict(x) for x in self._schedules]

    def schedule(self, start_at, end_at=None, title="Встреча по расписанию", options=None):
        start = self._schedule_ts(start_at)
        # An empty end time means "until I stop it".  This is useful for a
        # background meeting whose duration is not known in advance; the menu
        # bar/controller can always finish it explicitly.
        end = self._schedule_ts(end_at) if end_at else None
        if end is not None and end <= start:
            raise ValueError("время окончания должно быть позже начала")
        if start < time.time() - 2:
            raise ValueError("время начала уже прошло")
        opts = dict(options or {})
        if not bool(opts.get("consent")):
            raise ValueError("для фоновой записи подтвердите согласие участников")
        item = {"id": uuid.uuid4().hex, "start_at": start, "end_at": end,
                "title": str(title or "Встреча по расписанию")[:120], "options": opts,
                "state": "scheduled", "created": time.time()}
        with self._schedules_lock:
            self._schedules.append(item)
            self._schedules.sort(key=lambda x: float(x.get("start_at") or 0))
            self._save_schedules()
        self._emit("schedule", schedules=self.schedules())
        return item

    def cancel_schedule(self, sid):
        with self._schedules_lock:
            before = len(self._schedules)
            self._schedules = [x for x in self._schedules if x.get("id") != sid]
            if len(self._schedules) == before:
                raise ValueError("запись в расписании не найдена")
            self._save_schedules()
        self._emit("schedule", schedules=self.schedules())
        return {"cancelled": sid}

    def retry_schedule(self, sid):
        """Вернуть ошибочную встречу в расписание после исправления доступа."""
        with self._schedules_lock:
            item = next((x for x in self._schedules if x.get("id") == sid), None)
            if item is None:
                raise ValueError("запись в расписании не найдена")
            if item.get("state") not in ("error", "done"):
                raise ValueError("повторная проверка доступна только для завершённой или ошибочной записи")
            if float(item.get("start_at") or 0) <= time.time() + 2:
                raise ValueError("время начала уже прошло — создайте новую запись")
            item["state"] = "scheduled"
            item.pop("error", None)
            item.pop("preflight", None)
            self._save_schedules()
            result = dict(item)
        self._emit("schedule", schedules=self.schedules())
        return result

    def _schedule_loop(self):
        while not getattr(self.eng, "stopping", threading.Event()).wait(0.5):
            now = time.time()
            checks = []
            due = []
            with self._schedules_lock:
                for item in self._schedules:
                    start = float(item.get("start_at") or 0)
                    # Проверяем запись заранее, но не просим macOS разрешения
                    # автоматически. Так ошибка видна за пять минут, а не в
                    # момент, когда встреча уже должна была начаться.
                    if (item.get("state") == "scheduled" and 0 < start - now <= 300
                            and not (item.get("preflight") or {}).get("checked_at")):
                        item["preflight"] = {"state": "checking", "started_at": now}
                        checks.append(dict(item))
                    if (item.get("state") == "scheduled"
                            and (item.get("preflight") or {}).get("state") != "checking"
                            and start <= now):
                        item["state"] = "starting"
                        due.append(dict(item))
                if checks or due:
                    self._save_schedules()
            for item in checks:
                result = self._schedule_preflight(item)
                item["preflight"] = result
                if result.get("state") == "error":
                    item["state"] = "error"
                    item["error"] = result.get("message", "предварительная проверка не пройдена")[:240]
                self._schedule_update(item)
                self._emit("schedule_check", schedule=item, result=result)
                self._emit("schedule", schedules=self.schedules())
                self._notify_schedule_check(item, result)
            for item in due:
                try:
                    if self.on:
                        raise ValueError("другая встреча уже идёт")
                    opts = dict(item.get("options") or {})
                    opts.pop("title", None)
                    opts["scheduled_end"] = float(item.get("end_at") or 0) or None
                    self.start(title=item.get("title", "Встреча по расписанию"), **opts)
                    item["state"] = "running"
                    item["started"] = time.time()
                    log.info("🎬 Запланированная встреча началась: %s", item.get("title"))
                except Exception as exc:  # расписание не должно останавливать ядро
                    item["state"], item["error"] = "error", str(exc)[:240]
                    log.warning("🎬 Запланированная запись не началась: %s", exc)
                    try:
                        self.eng.push("Ошибка обработки персональный агент",
                                      f"Запись «{item.get('title', 'Встреча')}» не началась: {item['error']}",
                                      critical=True, telegram=False)
                    except Exception:
                        pass
                self._schedule_update(item)
                self._emit("schedule", schedules=self.schedules())
            if self.on:
                end = self.meeting.get("scheduled_end")
                if end and now >= float(end):
                    self.stop(summary=True)
                    with self._schedules_lock:
                        for item in self._schedules:
                            if item.get("state") == "running" and item.get("started"):
                                item["state"] = "done"
                        self._save_schedules()
                    self._emit("schedule", schedules=self.schedules())

    def _schedule_update(self, item):
        """Атомарно обновить одну строку расписания и сразу сохранить её."""
        with self._schedules_lock:
            for current in self._schedules:
                if current.get("id") == item.get("id"):
                    current.update(item)
                    break
            self._save_schedules()

    @staticmethod
    def _preflight_result(state, checks, message=""):
        return {"state": state, "checked_at": time.time(), "checks": checks,
                "message": str(message or "")[:400]}

    def _schedule_preflight(self, item):
        """Проверить обязательные ресурсы фоновой записи за пять минут до старта.

        Проверка открывает и тут же закрывает выбранный аудиовход, поэтому
        ошибка BlackHole/микрофона обнаруживается заранее. Системные доступы
        только читаются: неожиданный системный запрос в фоне был бы хуже,
        чем понятное уведомление с причиной.
        """
        opts = dict(item.get("options") or {})
        checks = []
        if not opts.get("consent"):
            return self._preflight_result("error", [{"name": "согласие", "ok": False}],
                                          "не подтверждено согласие участников")
        if self.on:
            return self._preflight_result("error", [{"name": "ядро", "ok": False}],
                                          "другая встреча уже идёт")
        record = bool(opts.get("record", True))
        source = str(opts.get("record_source") or opts.get("audio_source") or "system").lower()
        if record:
            try:
                from audio_io import Capture, LoopbackCapture
                if source == "system":
                    cap = LoopbackCapture()
                else:
                    cfg = self.eng.s.get("audio") if hasattr(self.eng.s, "get") else {}
                    device = cfg.get("input", "") if isinstance(cfg, dict) else ""
                    cap = Capture(device=device or "", strict_device=bool(device))
                try:
                    desc = cap.open()
                finally:
                    cap.close()
                checks.append({"name": "аудио", "ok": True, "detail": desc})
            except Exception as exc:  # noqa: BLE001 — причина должна попасть в карточку и уведомление
                return self._preflight_result("error", checks + [{"name": "аудио", "ok": False, "detail": str(exc)}],
                                              f"источник аудио недоступен: {exc}")
        needs_screen = bool(opts.get("auto_frames") or opts.get("auto_first_frame"))
        if needs_screen:
            try:
                from platform_info import MAC
                if MAC:
                    from Quartz import CGPreflightScreenCaptureAccess
                    allowed = bool(CGPreflightScreenCaptureAccess())
                    checks.append({"name": "запись экрана", "ok": allowed})
                    if not allowed:
                        return self._preflight_result(
                            "error", checks,
                            "Нужен доступ к записи экрана macOS")
            except ImportError:
                checks.append({"name": "запись экрана", "ok": True, "detail": "проверка недоступна на этой системе"})
            except Exception as exc:  # noqa: BLE001
                return self._preflight_result("error", checks + [{"name": "запись экрана", "ok": False}], str(exc))
        checks.append({"name": "согласие", "ok": True})
        cfg = self.eng.s.get("prompter") if hasattr(self.eng.s, "get") else {}
        cfg = cfg or {}
        stt_engine = str(cfg.get("stt_engine") or "inherit").lower() if isinstance(cfg, dict) else "inherit"
        if stt_engine == "gigaam":
            model = str(cfg.get("stt_model") or "gigaam-v3-e2e-rnnt")
            try:
                from engine_audio import GigaAMSTT
                cached = bool(GigaAMSTT(model).cached())
            except Exception as exc:  # noqa: BLE001
                cached = False
                log.info("🎬 Предпроверка модели записи: %s", exc)
            checks.append({"name": "модель расшифровки", "ok": cached, "detail": model})
            if not cached:
                return self._preflight_result("error", checks, f"модель записи «{model}» не скачана — скачайте её в настройках «Встречи»")
        else:
            checks.append({"name": "модель расшифровки", "ok": True,
                           "detail": "будет использована активная модель Dewiar"})
        return self._preflight_result("ok", checks, "Проверка пройдена; Dewiar запустит запись в назначенное время")

    def _notify_schedule_check(self, item, result):
        title = "Проверка записи встречи"
        name = item.get("title", "Встреча")
        if result.get("state") == "ok":
            text = f"«{name}»: всё готово. Запись начнётся в назначенное время."
            critical = False
        else:
            text = f"«{name}» не будет запущена без исправления: {result.get('message', 'проверьте настройки')}"
            critical = True
        try:
            self.eng.push(title, text, critical=critical, telegram=False)
        except Exception:  # тестовый/внешний движок может не иметь push
            log.info("🎬 %s: %s", title, text)

    @property
    def speaker(self):
        if self._speaker is None:
            from speaker import Speaker
            self._speaker = Speaker()
        return self._speaker

    def voice_info(self):
        sp = self.speaker
        return {"has": sp.owner is not None, "n": sp.owner_n, "model": sp.ready(), "enrolling": self.enrolling is not None}

    # ---------- состояние для окна ----------

    def public(self):
        with self._lock:
            m = dict(self.meeting)
            preview = self._summary_preview(m) if m else {}
            audio_path = Path(self._record_path) if self._record_path else None
            audio_bytes = audio_path.stat().st_size if audio_path and audio_path.is_file() else 0
            audio_ready = audio_bytes > 44  # Один WAV-заголовок ещё не является записью.
            rec = {"enabled": bool(m.get("record")), "deferred": bool(m.get("deferred")),
                   "name": Path(self._record_path).name if self._record_path else "",
                   "ready": audio_ready,
                   "bytes": audio_bytes,
                   "audio_source": m.get("audio_source") or "system",
                   "audio_paused": bool(self._record_paused),
                   "error": self._record_error,
                   "transcript": Path(self._transcript_path).name if self._transcript_path else "",
                   "screenshots": len(self._screenshots)}
            rec.update({"frame_interval": self._frame_interval, "frame_quality": self._frame_quality,
                        "video": bool(self._video_enabled), "video_frames": self._video_frames,
                        "video_path": Path(self._video_path).name if self._video_path else "",
                        "export_busy": self._video_export_busy,
                        "can_export": not self.on and bool(self._frame_events) and audio_ready})
            return {"on": self.on, "paused": self.paused, "meeting": m, "status": self.status,
                    "files": [{"name": f["name"], "chars": f["chars"]} for f in self.files], "lines": self.lines[-30:],
                    "hints": self.hints[-20:], "sheet": self.sheet, "summary": self.summary,
                    "summary_preview": preview, "recording": rec, "schedules": self.schedules(),
                    "brain": bool(self.eng.s.active_llm()), "loopback": True, "voice": self.voice_info()}

    def _emit(self, kind, **kw):
        self.eng.emit("prompter", kind=kind, **kw)

    def _state(self):
        self._emit("state", state=self.public())

    # ---------- материалы ----------

    @property
    def _dir(self):
        d = STATE_DIR / "prompter"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def add_file(self, name="", data=None, path=""):
        """Файл встречи (из окна — данные, из диалога — путь): текст и отрывки для поиска. -> {name, chars}."""
        from knowledge import chunks, extract
        src = Path(path) if path else None
        name = Path(str(name or (src.name if src else ""))).name.strip()
        if not name or Path(name).suffix.lower() not in TEXT_EXTS:
            raise ValueError("подходят PDF, Word, ODT, текст, Markdown, HTML и CSV")
        dst = self._dir / name
        if src is not None:
            if not src.is_file():
                raise ValueError("файл не найден")
            if src.stat().st_size > MAX_FILE:
                raise ValueError("файл больше 30 МБ")
            shutil.copyfile(src, dst)
        else:
            if data is None or len(data) > MAX_FILE:
                raise ValueError("файл пустой или больше 30 МБ")
            dst.write_bytes(data)
        try:
            text = extract(dst)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"не прочитался: {e}") from e
        if not text.strip():
            raise ValueError("в файле нет текста (скан без распознавания?)")
        with self._lock:
            self.files = [f for f in self.files if f["name"] != name] + [{"name": name, "chars": len(text), "chunks": chunks(text)}]
        log.info("🎬 Суфлёр: материал «%s» — %d знаков", name, len(text))
        self._state()
        return {"name": name, "chars": len(text)}

    def remove_file(self, name):
        with self._lock:
            self.files = [f for f in self.files if f["name"] != name]
        try:
            (self._dir / Path(name).name).unlink(missing_ok=True)
        except OSError:
            pass
        self._state()
        return True

    def _materials(self, query, limit=4, budget=3600):
        """Отрывки материалов к вопросу: все, если их мало; иначе лучшие по словам (BM25, как в «Знаниях»)."""
        from knowledge import words
        with self._lock:
            docs = [(f["name"], c) for f in self.files for c in f["chunks"]]
        if not docs:
            return []
        if sum(len(c) for _, c in docs) <= budget:
            return docs
        q = [w for w in words(query) if len(w) > 2]
        if not q:
            return docs[:limit]
        toks = [words(n + " " + c) for n, c in docs]
        n, avg = len(docs), sum(len(t) for t in toks) / len(docs) or 1
        df = {w: sum(1 for t in toks if w in t) for w in set(q)}
        scored = []
        for (name, c), t in zip(docs, toks):
            s = 0.0
            for w in set(q):
                f = t.count(w)
                if f:
                    s += math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5)) * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * len(t) / avg))
            if s > 0:
                scored.append((s, name, c))
        scored.sort(key=lambda x: -x[0])
        out, size = [], 0
        for _, name, c in scored[:limit]:
            if size + len(c) > budget and out:
                break
            out.append((name, c))
            size += len(c)
        return out or docs[:2]

    # ---------- включить / выключить ----------

    @staticmethod
    def _meta_list(value):
        return ", ".join(x.strip() for x in re.split(r"[,;\n]+", str(value or "")) if x.strip())[:2000]

    @staticmethod
    def _summary_preview(m):
        started = datetime.fromtimestamp(m.get("started") or time.time())
        return {"when": started.strftime("%d.%m.%Y %H:%M"),
                "participants": m.get("participants") or "не указаны",
                "topics": m.get("topics") or m.get("goal") or "не указаны",
                "decisions": m.get("decisions") or "будут определены после расшифровки"}

    def _record_open(self, title):
        """Создать WAV до запуска потока: запись не теряется, если встреча закончится сразу."""
        try:
            d = self.eng.workspace.ensure() / "Встречи" / "Аудио"
            d.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001 — протокол всё равно можно сохранить в резервный каталог
            d = self._dir / "recordings"
            d.mkdir(parents=True, exist_ok=True)
        # Use the same timestamp as the Markdown summary.  Using a second
        # datetime.now() here could cross a minute boundary and leave the
        # audio orphaned from its meeting row.
        started = datetime.fromtimestamp((self.meeting or {}).get("started") or time.time())
        safe = re.sub(r'[\\/:*?"<>|]+', " ", title).strip()[:60] or "Встреча"
        path = d / f"{started:%Y-%m-%d %H%M} {safe}.wav"
        with self._record_lock:
            self._record_path = str(path)
            self._record_wave = wave.open(str(path), "wb")
            self._record_wave.setnchannels(1)
            self._record_wave.setsampwidth(2)
            self._record_wave.setframerate(16000)

    def _transcript_open(self, title):
        """Открыть обычный UTF-8 .txt, который пополняется во время встречи."""
        try:
            root = self.eng.workspace.ensure() / "Встречи" / "Расшифровки"
            root.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            root = self._dir / "transcripts"
            root.mkdir(parents=True, exist_ok=True)
        started = datetime.fromtimestamp((self.meeting or {}).get("started") or time.time())
        safe = re.sub(r'[\\/:*?"<>|]+', " ", title).strip()[:60] or "Встреча"
        path = root / f"{started:%Y-%m-%d %H%M} {safe}.txt"
        n = 2
        while path.exists():
            path = root / f"{started:%Y-%m-%d %H%M} {safe} ({n}).txt"
            n += 1
        self._transcript_path = str(path)
        path.write_text(f"# {title}\n# Начало: {started:%d.%m.%Y %H:%M}\n\n", encoding="utf-8")

    def _transcript_write(self, line):
        path = Path(self._transcript_path) if self._transcript_path else None
        if path is None:
            return
        try:
            who = {"them": "Собеседник", "me": "Вы", "screen": "Экран"}.get(line.get("who"), line.get("who", ""))
            seconds = max(0, int(float(line.get("t") or 0)))
            stamp = f"{seconds // 60:02d}:{seconds % 60:02d}"
            text = str(line.get("text") or "").strip()
            with path.open("a", encoding="utf-8") as f:
                f.write(f"[{stamp}] {who}: {text}\n")
                f.flush()
        except OSError as exc:
            log.info("🎬 Текст расшифровки не записан: %s", exc)

    def _record_write(self, pcm):
        with self._record_lock:
            if self._record_wave is not None:
                try:
                    self._record_wave.writeframes(pcm)
                except (OSError, ValueError) as e:
                    log.warning("🎬 Запись встречи: %s", e)

    def _record_close(self):
        with self._record_lock:
            w, self._record_wave = self._record_wave, None
            if w is not None:
                try:
                    w.close()
                except OSError:
                    pass

    def _suspend_engine_mic(self, on):
        """Запись системного микса не должна конкурировать с Zoom/Meet за микрофон."""
        flag = getattr(self.eng, "audio_suspended", None)
        if flag is None:
            return
        if on:
            flag.set()
            cap = getattr(self.eng, "capture", None)
            if cap is not None:
                try:
                    cap.close()
                except Exception:  # noqa: BLE001
                    pass
            getattr(self.eng, "restart_audio", threading.Event()).set()
        else:
            flag.clear()
            getattr(self.eng, "restart_audio", threading.Event()).set()

    def _meeting_stt(self):
        """Распознаватель именно для записи встречи.

        ``inherit`` оставляет обычный выбор Dewiar. Для локальной модели можно
        выбрать другой вариант GigaAM, не меняя короткие голосовые команды.
        Модель загружается один раз на встречу и не выгружается между фразами.
        """
        cfg = self.eng.s.get("prompter") if hasattr(self.eng.s, "get") else {}
        cfg = cfg or {}
        engine = str(cfg.get("stt_engine") or "inherit").lower()
        if engine in ("inherit", "remote"):
            return self.eng.current_stt()
        if engine == "vosk":
            return getattr(self.eng, "vosk_stt", None) or self.eng.current_stt()
        if engine != "gigaam":
            return self.eng.current_stt()
        model = str(cfg.get("stt_model") or "gigaam-v3-e2e-rnnt")
        obj = self._meeting_stt_obj
        if obj is None or getattr(obj, "model_name", "") != model:
            from engine_audio import GigaAMSTT
            obj = self.eng.gigaam if getattr(self.eng, "gigaam", None) is not None and self.eng.gigaam.model_name == model else GigaAMSTT(model)
            self._meeting_stt_obj = obj
        if not getattr(obj, "ready", False):
            if not obj.cached():
                raise RuntimeError(f"модель записи «{model}» не скачана — скачайте её в настройках Суфлёра")
            if not obj.load():
                raise RuntimeError(obj.error or f"модель записи «{model}» не загрузилась")
        return obj

    def start(self, title="", goal="", mode="auto", them=True, me=True, knowledge=False, keep_text=False, place="online", nudge=True,
              participants="", topics="", record=False, deferred=True, source="system", consent=False, auto_frames=False,
              auto_interval=0.5, frame_quality=85, frame_video=False, auto_first_frame=True,
              capture_source="screen", capture_region=None, capture_application="", scheduled_end=None, record_source=None):
        """Начать встречу. place: online — собеседник по звуку компьютера, вы — микрофон; live — очная встреча,
        один микрофон, кто говорит — по голосу (нужен запомненный голос владельца)."""
        if self._video_export_busy:
            raise ValueError("Дождитесь создания видео встречи.")
        live = place == "live"
        if record and not consent:
            raise ValueError("для записи переговоров подтвердите согласие участников")
        if live and self.speaker.owner is None:
            raise ValueError("для очной встречи сначала запомните свой голос — «Запомнить мой голос»")
        if auto_frames:
            try:
                from platform_info import MAC
                if MAC:
                    from macsys import request_screen_capture_permission
                    if not request_screen_capture_permission():
                        raise ValueError("Разрешите Dewiar запись экрана в Системных настройках → Конфиденциальность и безопасность → Запись экрана, затем повторите запуск автоматических кадров")
            except ImportError:
                pass
        if self.enrolling is not None:
            self.enroll_stop(save=False)
        if self.on:
            self.stop(summary=False)
        self._record_path = ""
        self._record_error = ""
        self._record_paused = False
        self._transcript_path = ""
        self._session_dir = None
        self._screenshots = []
        self._frame_events = []
        self._last_frame_sig = None
        try:
            self._frame_interval = max(0.5, min(5.0, float(auto_interval or 0.5)))
        except (TypeError, ValueError):
            self._frame_interval = 0.5
        try:
            self._frame_quality = max(40, min(100, int(frame_quality or 85)))
        except (TypeError, ValueError):
            self._frame_quality = 85
        self._video_enabled = bool(frame_video) and bool(auto_frames)
        self._frames_enabled = bool(auto_frames)
        self._video_dir = None
        self._video_frames = 0
        self._video_path = ""
        cfg = self.eng.s.get("prompter") if hasattr(self.eng.s, "get") else {}
        cfg = cfg or {}
        if capture_source in (None, "", "screen") and cfg.get("capture_source"):
            capture_source = cfg.get("capture_source")
        if capture_region is None:
            capture_region = cfg.get("capture_region") or {}
        if not capture_application:
            capture_application = cfg.get("capture_application") or ""
        self._capture_source = str(capture_source or "screen").lower()
        if self._capture_source not in ("screen", "region", "application"):
            self._capture_source = "screen"
        self._capture_region = dict(capture_region or {})
        self._capture_application = str(capture_application or "")[:160]
        record = bool(record) and not live and bool(them)
        record_source = str((cfg.get("audio_source") if record_source is None else record_source) or "system").lower()
        if record_source not in ("system", "microphone"):
            record_source = "system"
        with self._lock:
            self.meeting = {"title": (title or "").strip()[:120] or "Встреча", "goal": (goal or "").strip()[:2000],
                            "mode": "manual" if mode == "manual" else "auto", "them": bool(them) and not live, "me": bool(me) or live,
                            "place": "live" if live else "online", "nudge": bool(nudge),
                            "knowledge": bool(knowledge), "keep_text": bool(keep_text) or record,
                            "participants": self._meta_list(participants), "topics": self._meta_list(topics),
                            "record": record, "deferred": bool(deferred) if record else False,
                            "audio_source": record_source,
                            "source": (source or "system")[:40], "auto_frames": bool(auto_frames), "started": time.time()}
            if scheduled_end:
                self.meeting["scheduled_end"] = float(scheduled_end)
            self.lines, self.hints, self.sheet, self.summary, self.paused = [], [], [], "", False
            self.on = True
        self._transcript_open(self.meeting["title"])
        # Первый кадр — полезная точка отсчёта протокола. По умолчанию он
        # делается только для записываемой встречи или при включённых кадрах;
        # отказ в разрешении не отменяет саму запись.
        if auto_first_frame and (record or auto_frames):
            try:
                self.capture_frame(auto=False, ocr=True)
            except Exception as exc:  # noqa: BLE001
                self.status = "первый снимок не сделан: " + str(exc)[:160]
                log.info("🎬 Первый кадр не сохранён: %s", exc)
        if record:
            try:
                self._record_open(self.meeting["title"])
                # Both sources are opened by the meeting worker. Pause the
                # normal assistant capture so the WAV is not corrupted by a
                # second PortAudio stream.
                self._suspend_engine_mic(True)
            except Exception:
                self.on = False
                self._stop.set()
                self._record_close()
                self._suspend_engine_mic(False)
                raise
            # The meeting worker owns the selected input while recording.
            self.meeting["me"] = False
        self._stop = threading.Event()
        self._await, self._pause_hint_t, self._them_voice_t, self._t0 = None, 0.0, 0.0, time.monotonic()
        self._diar = self.speaker.diarizer() if self.speaker.owner is not None else None
        import queue
        self._q = queue.Queue(maxsize=40)
        threading.Thread(target=self._stt_loop, name="prompter-stt", daemon=True).start()
        threading.Thread(target=self._watch, name="prompter-watch", daemon=True).start()
        if auto_frames:
            self._frame_thread = threading.Thread(target=self._frame_loop, name="prompter-frames", daemon=True)
            self._frame_thread.start()
        if live:
            self.status = "слушаю микрофон — вас и собеседника различаю по голосу"
        elif self.meeting["them"]:
            self._loop_thread = threading.Thread(target=self._them_loop, name="prompter-them", daemon=True)
            self._loop_thread.start()
        else:
            self.status = "собеседника не слушаю — подсказки по кнопке и на ваши фразы"
        if self.meeting["goal"] or self.files:
            threading.Thread(target=self._prepare_sheet, name="prompter-sheet", daemon=True).start()
        source_name = "системный звук" if record_source == "system" else "микрофон"
        log.info("🎬 Суфлёр включён: «%s» (%s, %s; собеседник: %s, я: %s; запись: %s)", self.meeting["title"], "вживую" if live else "онлайн",
                 "подсказки на вопросы" if self.meeting["mode"] == "auto" else "по кнопке",
                 "по голосу" if live else ("да" if self.meeting["them"] else "нет"), "да" if self.meeting["me"] else "нет",
                 source_name if record else "нет")
        self.eng.set_state("idle")
        self._state()
        return self.public()

    def pause(self, on=True):
        self.paused = bool(on)
        self._state()
        return self.public()

    def pause_recording_audio(self, on=True):
        """Приостановить только запись WAV, не останавливая встречу и кадры."""
        if not self.on or not self.meeting.get("record"):
            raise ValueError("аудиозапись сейчас не включена")
        self._record_paused = bool(on)
        self.status = "аудиозапись на паузе" if self._record_paused else "аудиозапись продолжается"
        self._state()
        return self.public()

    def stop(self, summary=True):
        """Закончить встречу; summary — подвести итог (в фоне, событие summary)."""
        if not self.on:
            return self.public()
        self.on = False
        self._frames_enabled = False
        self._stop.set()
        cap = self._loop_cap
        if cap is not None:
            try:
                cap.close()                         # sounddevice read должен выйти сразу после остановки
            except Exception:  # noqa: BLE001
                pass
        t = self._loop_thread
        if t is not None and t is not threading.current_thread() and t.is_alive():
            t.join(timeout=2.0)
        ft = self._frame_thread
        if ft is not None and ft is not threading.current_thread() and ft.is_alive():
            ft.join(timeout=max(1.0, self._frame_interval + 0.2))
        self._record_close()
        if self.meeting.get("record"):
            self._suspend_engine_mic(False)
        if self._hint_cancel:
            self._hint_cancel.set()
        self._frame_thread = None
        if self._video_enabled:
            self._finalize_frame_video()
        log.info("🎬 Суфлёр выключен: реплик %d, подсказок %d", len(self.lines), len(self.hints))
        # Даже тихая встреча должна оставить карточку в «Встречах»: иначе
        # пользователь не узнаёт, что запись стартовала, и не видит ошибку
        # источника звука.
        if summary and self.meeting:
            if self.meeting.get("record") and self.meeting.get("deferred") and self._record_path:
                threading.Thread(target=self._post_transcribe, name="prompter-transcribe", daemon=True).start()
            else:
                threading.Thread(target=self._summarize, name="prompter-summary", daemon=True).start()
        self._state()
        return self.public()

    def feed_me(self, pcm):
        """Фраза с микрофона (Engine._on_utterance) — пока суфлёр включён или записывается голос."""
        if self.enrolling is not None:
            self._enroll_feed(pcm)
            return
        if self.on and not self.paused and self.meeting.get("me") and self._q is not None:
            try:
                self._q.put_nowait(("mic", pcm))
            except Exception:  # noqa: BLE001 — очередь полна: распознавание не успевает, старое важнее не терять
                pass

    # ---------- голос владельца ----------

    def enroll_start(self):
        """Запомнить голос: окно показывает фразы, человек читает их вслух; модель качается, если её нет."""
        if self.on:
            raise ValueError("сначала закончите встречу")
        self.enrolling = []
        self._emit("voice", step="model" if not self.speaker.ready() else "listen", n=0, need=len(ENROLL_PHRASES),
                   phrases=ENROLL_PHRASES)
        threading.Thread(target=self._enroll_model, name="prompter-voice", daemon=True).start()
        return {"phrases": ENROLL_PHRASES}

    def _enroll_model(self):
        sp = self.speaker
        if sp.ready():
            return
        try:
            m = self.eng._mirror("speaker") if hasattr(self.eng, "_mirror") else None
            sp.mirror = m
            last = [0.0]

            def prog(frac):
                if time.monotonic() - last[0] > 0.3:
                    last[0] = time.monotonic()
                    self._emit("voice", step="model", pct=round(100 * frac))
            sp.ensure(prog)
            sp._session()
        except Exception as e:  # noqa: BLE001
            self.enrolling = None
            log.warning("🎬 Модель голоса: %s", e)
            self._emit("voice", step="error", error=str(e)[:200])
            return
        if self.enrolling is not None:
            self._emit("voice", step="listen", n=len(self.enrolling), need=len(ENROLL_PHRASES), phrases=ENROLL_PHRASES)

    def _enroll_feed(self, pcm):
        if len(pcm) / 2 / 16000 < 1.0 or self.enrolling is None:
            return                                         # «ага», кашель — не образец
        self.enrolling.append(pcm)
        n = len(self.enrolling)
        log.info("🎬 Голос: образец %d из %d", n, len(ENROLL_PHRASES))
        if n >= len(ENROLL_PHRASES):
            threading.Thread(target=self.enroll_stop, name="prompter-voice", daemon=True).start()
        else:
            self._emit("voice", step="listen", n=n, need=len(ENROLL_PHRASES), phrases=ENROLL_PHRASES)

    def enroll_stop(self, save=True):
        """Закончить запись: save — запомнить по тому, что успели сказать (нужно хотя бы две фразы)."""
        pcms, self.enrolling = self.enrolling or [], None
        if not save:
            self._emit("voice", step="cancel")
            return self.voice_info()
        self._emit("voice", step="saving")
        try:
            r = self.speaker.enroll(pcms)
        except Exception as e:  # noqa: BLE001
            self._emit("voice", step="error", error=str(e)[:200])
            return self.voice_info()
        self._emit("voice", step="done", **r)
        self._state()
        return self.voice_info()

    def enroll_forget(self):
        self.speaker.forget()
        self._state()
        return self.voice_info()

    # ---------- звук собеседника ----------

    def _them_loop(self):
        """Поток prompter-them: звук компьютера → фразы по VAD (как в _listen: начало — 2 кадра речи, конец — 0,6 с
        тишины, не длиннее 20 с) → очередь распознавания."""
        import os
        from audio_io import Capture, LoopbackCapture
        from listen import SileroVAD
        wav_path = os.environ.get("DEWIAR_PROMPTER_WAV") or None
        if wav_path:
            cap = LoopbackCapture(wav_path=wav_path)
        elif self.meeting.get("audio_source") == "microphone":
            device = self.eng.s.get("audio", "input") if hasattr(self.eng.s, "get") else ""
            cap = Capture(device=device or "", strict_device=bool(device))
        else:
            cap = LoopbackCapture()
        self._loop_cap = cap
        try:
            self.status = "слушаю: " + cap.open()
        except Exception as e:  # noqa: BLE001
            self._record_error = str(e)[:240] if self.meeting.get("record") else ""
            self.status = (f"запись не началась: {e}" if self.meeting.get("record")
                           else f"звук компьютера не слышу ({e}) — подсказки по кнопке и на ваши фразы")
            log.warning("🎬 Суфлёр: %s", self.status)
            self._state()
            return
        if self.meeting.get("record"):
            self.status = ("записываю микрофон в WAV; микрофон Dewiar не используется" if self.meeting.get("audio_source") == "microphone"
                           else "записываю системный звук; микрофон Dewiar не используется")
        self._state()
        vad = SileroVAD(DATA_DIR / "silero_vad.onnx")
        end_frames = int(0.6 / FRAME_S)
        pre, utt, in_speech, starts, silence = [], [], False, 0, 0
        try:
            while not self._stop.is_set():
                frame = cap.read()
                if not frame:
                    if os.environ.get("DEWIAR_PROMPTER_WAV"):
                        break
                    time.sleep(0.2)
                    continue
                if self.paused:
                    continue
                if self.meeting.get("record"):
                    if not self._record_paused:
                        self._record_write(frame)
                    if self.meeting.get("deferred"):
                        continue
                p = vad.prob(frame)
                if p > 0.5:
                    self._them_voice_t = time.monotonic()
                if not in_speech:
                    pre = (pre + [frame])[-10:]
                    starts = starts + 1 if p > 0.5 else 0
                    if starts >= 2:
                        in_speech, utt, silence = True, list(pre), 0
                    continue
                utt.append(frame)
                silence = silence + 1 if p < 0.35 else 0
                if silence >= end_frames or len(utt) * FRAME_S > 20:
                    in_speech, starts = False, 0
                    pcm = b"".join(utt[:len(utt) - max(0, silence - 10)])  # хвост слова тихий — оставляем 0,3 с
                    if len(pcm) / 2 / 16000 >= 0.5:
                        try:
                            self._q.put_nowait(("them", pcm))
                        except Exception:  # noqa: BLE001
                            pass
                    utt = []
        finally:
            cap.close()
            if self._loop_cap is cap:
                self._loop_cap = None
            if self._loop_thread is threading.current_thread():
                self._loop_thread = None

    def _post_transcribe(self):
        """После встречи: закрытая запись -> транскрипция -> обычный итоговый протокол."""
        path = self._record_path
        self._emit("summary", summary="", busy=True, phase="transcribe", recording=Path(path).name if path else "")
        try:
            if not path or not Path(path).is_file() or Path(path).stat().st_size <= 44:
                raise RuntimeError(self._record_error or "аудиофайл пустой — проверьте источник записи и разрешение микрофона/звука")
            if hasattr(self.eng, "current_stt"):
                segments = self.eng.transcribe_file(path, stt=self._meeting_stt())
            else:  # компактные тестовые/внешние движки используют старую сигнатуру
                segments = self.eng.transcribe_file(path)
            lines = [{"who": "them", "text": text, "t": round(float(start), 1)}
                     for start, _end, text in segments if str(text or "").strip()]
            with self._lock:
                self.lines = lines[-400:]
            # Отложенная расшифровка создаёт тот же живой .txt, который был бы
            # заполнен в реальном времени.
            try:
                p = Path(self._transcript_path)
                p.write_text(f"# {self.meeting.get('title', 'Встреча')}\n# Отложенная расшифровка\n\n", encoding="utf-8")
            except OSError:
                pass
            for line in self.lines:
                self._transcript_write(line)
                self._emit("line", line=line)
            self.status = f"расшифровано фраз: {len(self.lines)}"
        except Exception as e:  # noqa: BLE001 — протокол всё равно будет создан с сообщением об ошибке
            self.status = "расшифровка не получилась: " + str(e)[:180]
            log.warning("🎬 Расшифровка записи: %s", e)
        self._summarize()

    def _stt_loop(self):
        while not self._stop.is_set():
            try:
                who, pcm = self._q.get(timeout=0.5)
            except Exception:  # noqa: BLE001 — пусто
                continue
            if who == "mic":
                who = self._whose(pcm)
                if who is None:
                    continue
            try:
                text = (self._meeting_stt().transcribe(pcm) or "").strip()
            except Exception as e:  # noqa: BLE001
                log.info("🎬 Суфлёр: не распознал фразу: %s", e)
                continue
            if text:
                self.add_line(who, text)

    def _whose(self, pcm):
        """Фраза микрофона: чья? Очная встреча — по голосу; онлайн — ваша, а явно чужой голос (собеседник из колонок
        в микрофоне) — None: его слышно по звуку компьютера (а если звук компьютера выключен — это собеседник).
        Голос не запомнен — всегда ваша."""
        if self._diar is None:
            return "me"
        try:
            if self.meeting.get("place") == "live":
                who, s = self._diar.who(pcm)
                log.debug("🎬 голос: %s (%.2f)", who, s)
                return who
            from speaker import OTHER, cos
            v = self.speaker.embed(pcm)
            if v is not None and cos(v, self.speaker.owner) < OTHER:
                if not self.meeting.get("them"):
                    return "them"                          # звук компьютера не слушаем — собеседник только из микрофона
                log.info("🎬 Суфлёр: чужой голос в микрофоне — пропускаю (его слышно по звуку компьютера)")
                return None
        except Exception as e:  # noqa: BLE001 — модель голоса не загрузилась: считаем, что говорите вы
            log.info("🎬 Суфлёр: голос не определил: %s", e)
            return "them" if self.meeting.get("place") == "live" else "me"
        return "me"

    # ---------- расшифровка и поводы ----------

    def add_line(self, who, text):
        line = {"who": who, "text": text, "t": round(time.time() - self.meeting.get("started", time.time()), 1)}
        with self._lock:
            self.lines.append(line)
            self.lines = self.lines[-400:]
        self._emit("line", line=line)
        self._transcript_write(line)
        log.info("🎬 %s: %s", "Собеседник" if who == "them" else "Я", text[:120])
        if not (self.on and not self.paused and self.meeting.get("mode") == "auto"):
            return
        if who == "me":
            self._await = None                             # вы заговорили — значит, отвечаете
        if wants_hint(text, who):
            r = self.hint(q=text if who == "them" else self._last_them_q(), why="them" if who == "them" else "me")
            if who == "them" and Q_RX.search(text):
                self._await = {"t": time.monotonic(), "q": text, "id": r["id"]}

    def _last_them_q(self):
        with self._lock:
            for x in reversed(self.lines[-6:]):
                if x["who"] == "them":
                    return x["text"]
        return ""

    def _voice_t(self):
        """Когда последний раз кто-то говорил (monotonic): звук компьютера, микрофон."""
        return max(self._them_voice_t, getattr(self.eng, "mic_voice_t", 0.0) or 0.0)

    def _watch(self):
        """Поток prompter-watch: «замешкались» после вопроса и долгая пауза в разговоре."""
        while not self._stop.wait(0.5):
            m = self.meeting
            if self.paused or m.get("mode") != "auto" or not m.get("nudge", True):
                continue
            now = time.monotonic()
            a = self._await
            if a:
                mic = getattr(self.eng, "mic_voice_t", 0.0) or 0.0
                if mic > a["t"] + 0.3:
                    self._await = None                     # микрофон слышит речь — человек отвечает
                elif now - a["t"] > STUCK_S:
                    self._await = None
                    self._stuck(a)
                continue
            last_hint = self.hints[-1] if self.hints else None
            busy = last_hint is not None and not last_hint.get("done")
            if (self.lines and not busy and now - self._t0 > PAUSE_AFTER and now - self._voice_t() > PAUSE_S
                    and now - self._pause_hint_t > 90 and (last_hint is None or time.time() - last_hint["ts"] > 20)):
                self._pause_hint_t = now
                log.info("🎬 Суфлёр: пауза в разговоре — подсказываю, что сказать дальше")
                try:
                    self.hint(why="pause")
                except ValueError:
                    pass

    def _stuck(self, a):
        """Собеседник спросил, вы молчите: выделить подсказку к вопросу или написать её."""
        log.info("🎬 Суфлёр: на вопрос молчат %.0f с — выделяю подсказку", STUCK_S)
        with self._lock:
            h = next((x for x in self.hints if x["id"] == a["id"]), None)
        if h is not None and not h.get("empty") and not h.get("error"):
            h["stuck"] = True
            self._emit("hint", hint=dict(h))
            return
        try:
            self.hint(q=a["q"], why="stuck")
        except ValueError:
            pass


    def hint(self, q="", why="button"):
        """Подсказка: на реплику q (или по кнопке — по последним репликам). Новая отменяет недописанную."""
        if not self.on and why != "test":
            raise ValueError("суфлёр выключен — начните встречу")
        if not self.eng.s.active_llm():
            raise ValueError("нужен «Модели» — выберите модель в студии")
        if self._hint_cancel:
            self._hint_cancel.set()
        cancel = threading.Event()
        self._hint_cancel = cancel
        self._n += 1
        h = {"id": self._n, "q": q, "text": "", "done": False, "instant": False, "why": why, "ts": time.time(), "lines_at": len(self.lines)}
        with self._lock:
            self.hints.append(h)
            self.hints = self.hints[-60:]
        threading.Thread(target=self._hint_run, args=(h, cancel), name="prompter-hint", daemon=True).start()
        return {"id": h["id"]}

    def _transcript(self, n=14):
        with self._lock:
            ls = self.lines[-n:]
        return "\n".join(("Собеседник: " if x["who"] == "them" else "Вы: ") + x["text"] for x in ls)

    def _sheet_match(self, q):
        """Готовый ответ из шпаргалки, если вопрос похож. Основы сравниваем по началу («стои» и «сто» — одно слово):
        устная речь и написанное заранее редко совпадают буква в букву."""
        from knowledge import words
        qs = {w for w in words(q) if len(w) > 2}
        best, score = None, 0.0
        for it in self.sheet:
            ws = {w for w in words(it["q"]) if len(w) > 2}
            if not ws or not qs:
                continue
            same = sum(1 for w in qs if any(w.startswith(v) or v.startswith(w) for v in ws))
            s = same / max(3, min(len(qs), len(ws)))
            if s > score:
                best, score = it, s
        return best if score >= 0.5 else None

    @staticmethod
    def _ask_line(why, q):
        if why == "pause":
            return ("В разговоре повисла пауза. Что человеку сказать или спросить сейчас, чтобы продвинуться к цели? "
                    "Первая строка — готовая фраза, которую можно произнести.")
        if why in ("me", "stuck"):
            return ((f"Собеседник спросил: «{q}». " if q else "") + "Человек замешкался с ответом. Первая строка — "
                    "готовая фраза, с которой начать ответ; дальше — опоры.")
        if q:
            return f"ПОДСКАЖИ, что ответить на: «{q}»"
        return "Человек нажал «Подсказать»: что ему сказать или спросить дальше?"

    def _hint_run(self, h, cancel):
        from llm import LLMError, stream_events
        if h["why"] == "them":                         # собеседник мог договорить следующей фразой — подождём чуть-чуть
            cancel.wait(0.5)
            if cancel.is_set():
                return
            with self._lock:
                more = [x["text"] for x in self.lines[h["lines_at"]:] if x["who"] == "them"]
            if more:
                h["q"] = (h["q"] + " " + " ".join(more)).strip()
        q = h["q"]
        hit = self._sheet_match(q) if q and self.sheet else None
        if hit:                                        # из шпаргалки — сразу, пока модель пишет
            h.update(text=hit["a"], instant=True)
            self._emit("hint", hint=dict(h))
        mats = self._materials(q + " " + self._transcript(4))
        kn = []
        if self.meeting.get("knowledge"):
            try:
                kn = [(f"Знания/{r['path']}", r["text"]) for r in self.eng.knowledge.search(q or self._transcript(4), limit=2)]
            except Exception:  # noqa: BLE001
                kn = []
        m = self.meeting
        ctx = (f"ВСТРЕЧА: {m.get('title', '')}\nЦЕЛЬ ЧЕЛОВЕКА: {m.get('goal') or 'не указана'}\n\n"
               + ("МАТЕРИАЛЫ:\n" + "\n\n".join(f"[{n}]\n{c}" for n, c in mats + kn) + "\n\n" if mats or kn else "МАТЕРИАЛОВ НЕТ.\n\n")
               + ("ШПАРГАЛКА:\n" + "\n".join(f"В: {x['q']} — О: {x['a']}" for x in self.sheet[:10]) + "\n\n" if self.sheet else "")
               + "РАЗГОВОР (последние реплики):\n" + (self._transcript() or "(пока тихо)") + "\n\n"
               + self._ask_line(h["why"], q))
        prof = dict(self.eng.s.active_llm(), temperature=0.3, max_tokens=360)
        text, last, t0 = "", 0.0, time.monotonic()
        try:
            for kind, val in stream_events(prof, HINT_SYSTEM, [{"role": "user", "content": ctx}], cancel=cancel, tools=None):
                if cancel.is_set():
                    return
                if kind != "text":
                    continue
                if not text:
                    log.info("🎬 Подсказка: первые слова через %.1f с", time.monotonic() - t0)
                text += val
                if time.monotonic() - last > 0.08:
                    h.update(text=text.strip(), instant=False)
                    self._emit("hint", hint=dict(h))
                    last = time.monotonic()
        except LLMError as e:
            if not cancel.is_set():
                h.update(text=h["text"] if h["instant"] else "", done=True, error=str(e)[:200])
                self._emit("hint", hint=dict(h))
            return
        if cancel.is_set():
            return
        text = text.strip()
        if text in ("—", "-", "–") or not text:
            h.update(done=True, empty=not h["instant"], text=h["text"] if h["instant"] else "")
        else:
            h.update(text=text, done=True, instant=False)
        src = re.search(r"(?im)^\s*источник\s*:\s*(.+)$", h["text"])
        h["src"] = src.group(1).strip() if src else ""
        self._emit("hint", hint=dict(h))
        log.info("🎬 Подсказка №%d за %.1f с%s", h["id"], time.monotonic() - t0, " (пусто)" if h.get("empty") else "")

    @staticmethod
    def _frame_signature(data):
        """Небольшая серая матрица для отбрасывания мигания интерфейса в автокадрах."""
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(data)).convert("L").resize((32, 18))
            return list(im.getdata())
        except Exception:  # noqa: BLE001 — плохой кадр всё равно можно сохранить вручную
            return None

    def capture_frame(self, data=None, ocr=True, auto=False, quality=None):
        """Сохранить текущий экран и вставить его с OCR в расшифровку встречи.

        Кадр сохраняется локально рядом с .txt. Повторный одинаковый кадр не
        дублируется. ``data`` оставлен для тестов и для будущего выбора области.
        """
        if not self.on:
            raise ValueError("сначала начните встречу")
        if data is None:
            from eyes import EyesError, capture_screen
            from tools import desktop_env
            try:
                data = capture_screen(desktop_env(), source=self._capture_source,
                                      region=self._capture_region, application=self._capture_application)
            except (EyesError, PermissionError, OSError) as exc:
                raise ValueError(str(exc)) from exc
        from meeting_video import frame_stamp, write_frame
        captured = time.time()
        elapsed = max(0.0, captured - self.meeting.get("started", captured))
        with self._record_lock:
            audio_time = self._record_wave.tell() / 16000 if self._record_wave else 0.0
        stamp = frame_stamp(captured, elapsed, audio_time if self.meeting.get("record") else elapsed)
        # При включённой сборке видео сохраняем каждый такт отдельно, даже
        # если кадр не изменился: сцены в расшифровке по-прежнему дедуплицируются.
        if auto and self._video_enabled:
            if self._video_dir is None:
                root = Path(self._transcript_path).parent if self._transcript_path else self._dir
                self._video_dir = root / (Path(self._transcript_path).stem if self._transcript_path else "Встреча") / "Видео-кадры"
                self._video_dir.mkdir(parents=True, exist_ok=True)
            try:
                q = max(40, min(100, int(quality or self._frame_quality)))
                write_frame(self._video_dir / f"frame_{self._video_frames:06d}.jpg", data, stamp, q)
                self._video_frames += 1
            except Exception as exc:  # noqa: BLE001 — кадр сцены всё равно можно сохранить ниже
                log.info("🎬 Кадр для видео не сохранён: %s", exc)
        sig = self._frame_signature(data)
        if auto and sig is not None and self._last_frame_sig is not None:
            delta = sum(abs(a - b) for a, b in zip(sig, self._last_frame_sig)) / (255 * len(sig))
            if delta < 0.012:
                return {"skipped": True, "reason": "экран почти не изменился"}
        if sig is not None:
            self._last_frame_sig = sig
        digest = hashlib.sha1(data).hexdigest()[:12]
        if any(x.get("sha1") == digest for x in self._screenshots):
            existing = next(x for x in self._screenshots if x.get("sha1") == digest)
            self._remember_frame(existing["path"], stamp)
            return dict(existing)
        if self._session_dir is None:
            root = Path(self._transcript_path).parent if self._transcript_path else self._dir
            self._session_dir = root / (Path(self._transcript_path).stem if self._transcript_path else "Встреча")
        frames = self._session_dir / "Кадры"
        frames.mkdir(parents=True, exist_ok=True)
        n = len(self._screenshots) + 1
        suffix = ".jpg" if auto else ".png"
        path = frames / f"{n:04d}-{digest}{suffix}"
        write_frame(path, data, stamp, max(40, min(100, int(quality or self._frame_quality))) if auto else None)
        text = ""
        if ocr:
            try:
                from ocr import recognize
                text = recognize(data)
            except Exception as exc:  # noqa: BLE001
                log.info("🎬 OCR кадра не выполнился: %s", exc)
        t = round(elapsed, 1)
        rel = str(path.relative_to(Path(self._transcript_path).parent)) if self._transcript_path else str(path)
        line = {"who": "screen", "kind": "screen", "text": text or "Снимок экрана", "t": t,
                "path": rel, "ocr": text}
        entry = {"path": rel, "ocr": text, "sha1": digest, "t": t, **stamp}
        self._screenshots.append(entry)
        self._remember_frame(rel, stamp)
        with self._lock:
            self.lines.append(line)
            self.lines = self.lines[-400:]
        self._transcript_write(line)
        self._emit("line", line=line)
        self._state()
        return dict(entry)

    def _remember_frame(self, path, stamp):
        # Reappearing scenes must return to their current audio time.
        if self._frame_events and self._frame_events[-1]["path"] == path:
            return
        entry = {"path": path, **stamp}
        self._frame_events.append(entry)
        if self._session_dir:
            with (self._session_dir / "frames.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def export_meeting_video(self):
        with self._lock:
            if self.on:
                raise ValueError("Сначала завершите запись встречи.")
            if self._video_export_busy:
                return {"busy": True}
            if not self._frame_events or not self._record_path:
                raise ValueError("Нужны сохранённые кадры и аудиозапись встречи.")
            events = [dict(item) for item in self._frame_events]
            audio = self._record_path
            root = Path(self._transcript_path).parent
            output = self._session_dir / "Видео" / "Видео встречи.mp4"
            self._video_export_busy = True

        def build():
            try:
                from meeting_video import build_meeting_video
                from eyes import ffmpeg_path
                path = build_meeting_video(events, audio, root, output, ffmpeg_path())
                self._video_path = path
                self._emit("video", busy=False, path=path)
            except Exception as exc:
                log.exception("Видео встречи не создано")
                self._emit("video", busy=False, error=str(exc))
            finally:
                self._video_export_busy = False
                self._state()
        threading.Thread(target=build, name="meeting-video", daemon=True).start()
        return {"busy": True}

    def _frame_loop(self):
        """Проверять экран с заданным интервалом; неизменившиеся сцены отбрасывать."""
        failures = 0
        while self.on and self._frames_enabled and not self._stop.wait(self._frame_interval):
            try:
                self.capture_frame(auto=True)
                failures = 0
            except Exception as exc:  # noqa: BLE001 — отсутствие Screen Recording не останавливает встречу
                failures += 1
                if failures in (1, 5):
                    self.status = "автокадры недоступны: " + str(exc)[:160]
                    self._state()

    def _finalize_frame_video(self):
        """Собрать MP4 из кадров; при записи встречи добавить исходный WAV."""
        if not self._video_dir or not self._frame_events:
            return ""
        try:
            root = Path(self._video_dir).parent
            out = root / "Видео" / (Path(self._transcript_path).stem if self._transcript_path else "Встреча")
            out.parent.mkdir(parents=True, exist_ok=True)
            from eyes import ffmpeg_path
            ff = ffmpeg_path()
            if not ff:
                return ""
            audio = Path(self._record_path) if self._record_path else None
            if audio and audio.is_file() and audio.stat().st_size > 44:
                from meeting_video import build_meeting_video
                self._video_path = build_meeting_video(
                    self._frame_events, audio, Path(self._transcript_path).parent,
                    out.with_name("Видео встречи.mp4"), ff)
                return self._video_path
            if self._video_frames < 2:
                return ""
            result = subprocess.run([ff, "-y", "-framerate", str(round(1.0 / self._frame_interval, 4)),
                                     "-i", str(self._video_dir / "frame_%06d.jpg"), "-c:v", "libx264",
                                     "-pix_fmt", "yuv420p", str(out.with_suffix(".mp4"))],
                                    capture_output=True, text=True, timeout=120)
            if result.returncode == 0 and out.with_suffix(".mp4").is_file():
                self._video_path = str(out.with_suffix(".mp4"))
                return self._video_path
        except Exception as exc:  # noqa: BLE001 — протокол встречи не должен падать из-за видео
            log.info("🎬 Видео из кадров не собрано: %s", exc)
        return ""

    # ---------- шпаргалка и итог ----------

    def _ask_model(self, system, content, max_tokens=900):
        from llm import stream_events
        prof = self.eng.s.active_llm()
        if not prof:
            return ""
        prof = dict(prof, temperature=0.3, max_tokens=max_tokens)
        return "".join(v for k, v in stream_events(prof, system, [{"role": "user", "content": content}], tools=None) if k == "text").strip()

    def _prepare_sheet(self):
        m = self.meeting
        mats = self._materials(m.get("goal", "") + " " + m.get("title", ""), limit=6, budget=6000)
        content = (f"ВСТРЕЧА: {m.get('title', '')}\nЦЕЛЬ: {m.get('goal') or 'не указана'}\n\n"
                   + ("МАТЕРИАЛЫ:\n" + "\n\n".join(f"[{n}]\n{c}" for n, c in mats) if mats else "МАТЕРИАЛОВ НЕТ."))
        try:
            sheet = parse_sheet(self._ask_model(SHEET_SYSTEM, content))
        except Exception as e:  # noqa: BLE001
            log.info("🎬 Шпаргалка не собралась: %s", e)
            return
        if not self.on:
            return
        self.sheet = sheet
        log.info("🎬 Шпаргалка готова: %d вопросов", len(sheet))
        self._emit("sheet", sheet=sheet)

    def _summarize(self):
        m = dict(self.meeting)
        record_path = self._record_path
        with self._lock:
            lines = list(self.lines)
        talk = "\n".join(
            ("Собеседник: " if x["who"] == "them" else "Вы: " if x["who"] == "me" else "Экран (OCR): ") + x["text"]
            for x in lines)
        self._emit("summary", summary="", busy=True)
        try:
            text = self._ask_model(SUMMARY_SYSTEM, f"ВСТРЕЧА: {m.get('title', '')}\nЦЕЛЬ: {m.get('goal') or '—'}\n\nРАСШИФРОВКА:\n{talk[-24000:]}",
                                   max_tokens=1200)
        except Exception as e:  # noqa: BLE001
            text = ""
            log.info("🎬 Итог встречи не получился: %s", e)
        started = datetime.fromtimestamp(m.get("started") or time.time())
        mins = max(1, round(((lines[-1]["t"] if lines else 60)) / 60))
        decisions = "будут определены после расшифровки"
        found = re.search(r"(?ims)^##\s*Договорились\s*\n(.*?)(?=^##\s|\Z)", text or "")
        if found:
            got = " ".join(x.strip().lstrip("-•* ") for x in found.group(1).splitlines() if x.strip())
            if got:
                decisions = got[:600]
        m["decisions"] = decisions
        preview = self._summary_preview(m)
        body = (f"# {m.get('title', 'Встреча')}\n\n## Summary\n"
                f"- Когда: {preview['when']}\n- Участники: {preview['participants']}\n- Темы: {preview['topics']}\n"
                f"- Что решили: {preview['decisions']}\n\n"
                f"*{started:%d.%m.%Y %H:%M} · {mins} мин · Встречи Dewiar*\n\n"
                + (f"**Цель:** {m['goal']}\n\n" if m.get("goal") else "")
                + (f"**Аудиозапись:** `Аудио/{Path(record_path).name}`\n\n" if record_path else "")
                + (f"**Статус аудиозаписи:** {self._record_error}\n\n" if self._record_error else "")
                + (f"**Расшифровка:** `Расшифровки/{Path(self._transcript_path).name}`\n\n" if self._transcript_path else "")
                + (text or "_Итог не получился — модель не ответила._")
                + ("\n\n## Материалы\n" + "\n".join(f"- {f['name']}" for f in self.files) if self.files else "")
                + ("\n\n## Расшифровка\n" + "\n".join(
                    ("**Собеседник:** " if x["who"] == "them" else "**Вы:** " if x["who"] == "me" else "**Экран:** ") + x["text"]
                    for x in lines)
                   if m.get("keep_text") else ""))
        if self._screenshots:
            body += "\n\n## Кадры экрана\n" + "\n".join(
                f"- `{x['path']}`" + (f" — {x['ocr']}" if x.get("ocr") else "") for x in self._screenshots)
        if self._video_path:
            body += f"\n\n## Видео из кадров\n- `{Path(self._video_path).name}` — таймлапс, кадр каждые {self._frame_interval:g} с"
        path = ""
        try:
            d = self.eng.workspace.ensure() / "Встречи"
            d.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r'[\\/:*?"<>|]+', " ", m.get("title", "Встреча")).strip()[:60] or "Встреча"
            f = d / f"{started:%Y-%m-%d %H%M} {safe}.md"
            f.write_text(body, encoding="utf-8")
            path = str(f)
        except OSError as e:
            log.warning("🎬 Итог встречи не сохранён: %s", e)
        self.summary = body
        log.info("🎬 Итог встречи: %s", path or "(не сохранён)")
        self._emit("summary", summary=body, path=path, busy=False)

