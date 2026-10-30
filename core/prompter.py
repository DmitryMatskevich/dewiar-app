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
import os
import re
import shutil
import subprocess
import threading
import time
import wave
from urllib.parse import quote
import hashlib
import json
import queue
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
    "Если в расшифровке человек обращается к собеседнику по имени или участник прямо представляется, добавь "
    "## Участники — список только явно названных имён; не угадывай имена по контексту.\n"
    "## Коротко — 2–3 предложения.\n## Договорились — пункты.\n## Кто что делает — пункты (сроки, если звучали).\n"
    "## Открытые вопросы — пункты.\nТолько то, что было сказано. Раздела нет по сути — напиши «нет».")

NOTES_SYSTEM = (
    "Это часть расшифровки длинной деловой встречи (реплики с таймкодами «Собеседник», «Вы», «Экран»). "
    "Выпиши кратко, без вступлений, только сказанное в ЭТОЙ части: темы; договорённости; задачи (кто, что, срок); "
    "цифры, цены и сроки; открытые вопросы; явно названные имена участников. Указывай таймкод [мм:сс] у договорённостей "
    "и задач. Нет пункта — не пиши его.")

MAX_FILE = 30 * 1024 * 1024
TEXT_EXTS = {".md", ".txt", ".html", ".htm", ".pdf", ".docx", ".odt", ".csv"}


def system_timezone():
    """Часовой пояс из системных настроек, независимо от переменной TZ."""
    names = []
    for raw in (os.path.realpath("/etc/localtime"), os.path.realpath("/var/db/timezone/zoneinfo/local")):
        marker = "/zoneinfo/"
        if marker in raw:
            names.append(raw.split(marker, 1)[1])
    try:
        value = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if value:
            names.append(value)
    except OSError:
        pass
    for name in names:
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 — неизвестная зона: используем системный fallback
            pass
    return datetime.now().astimezone().tzinfo


def local_datetime(timestamp):
    return datetime.fromtimestamp(float(timestamp), tz=system_timezone())


def local_text(timestamp):
    return local_datetime(timestamp).strftime("%d.%m.%Y %H:%M")


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


class _Segmenter:
    """Нарезка потока кадров 32 мс на фразы по вероятности речи VAD.

    Начало — 2 кадра речи подряд (плюс 10 кадров предыстории), конец — end_frames
    кадров тишины или 20 с фразы; хвост тишины обрезается до 0,3 с. Общая для
    дорожек «Собеседник» и «Вы», чтобы обе резались одинаково.
    """

    def __init__(self, end_frames, max_s=20.0, min_s=0.5):
        self.end_frames, self.max_s, self.min_s = end_frames, max_s, min_s
        self.pre, self.utt, self.in_speech, self.starts, self.silence = [], [], False, 0, 0

    def feed(self, frame, p):
        if not self.in_speech:
            self.pre = (self.pre + [frame])[-10:]
            self.starts = self.starts + 1 if p > 0.5 else 0
            if self.starts >= 2:
                self.in_speech, self.utt, self.silence = True, list(self.pre), 0
            return None
        self.utt.append(frame)
        self.silence = self.silence + 1 if p < 0.35 else 0
        if self.silence >= self.end_frames or len(self.utt) * FRAME_S > self.max_s:
            self.in_speech, self.starts = False, 0
            pcm = b"".join(self.utt[:len(self.utt) - max(0, self.silence - 10)])
            self.utt = []
            if len(pcm) / 2 / 16000 >= self.min_s:
                return pcm
        return None

    def flush(self):
        """Выдать последнюю фразу, когда запись остановили раньше паузы VAD."""
        pcm = b"".join(self.utt) if self.in_speech else b""
        self.pre, self.utt, self.in_speech, self.starts, self.silence = [], [], False, 0, 0
        return pcm if len(pcm) / 32000 >= self.min_s else None


class Prompter:
    SCHEDULE_GRACE_S = 15 * 60       # без времени окончания: опоздание больше 15 минут — встреча пропущена
    SILENCE_PEAK = 24                # int16: ниже — цифровая тишина (BlackHole без маршрута звука)
    SILENCE_WARN_S = 20.0            # столько секунд тишины на дорожке собеседника — предупреждение
    MIC_MAX_LAG = 12                 # кадров по 32 мс: дальше расхождение часов микрофона и BlackHole обрезаем
    SUMMARY_PART = 18000             # знаков расшифровки на один заход модели (длинные встречи — по частям)

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
        self._summary_path = ""
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
        self._fr_prev = None         # каскад автокадров: прошлый такт, сцены, с какого момента экран стоит
        self._fr_scenes = []
        self._fr_still_since = 0.0
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
        # Встреча — это две дорожки: «Вы» (микрофон) и «Собеседник» (звук
        # компьютера). Они пишутся в отдельные WAV рядом с общим (смешанным)
        # файлом, поэтому расшифровка знает, кто говорит, без угадывания.
        self._track_waves = {}   # {"me"|"them": wave.Wave_write}
        self._track_paths = {}   # {"me"|"them": str}
        self._levels = {}        # {"me"|"them": {"peak": int, "signal_t": float}}
        self._silence_warned = False
        self._base_name = ""     # «2026-10-30 0946 Склад» — общее имя WAV/TXT/MD одной встречи
        self._session = 0        # номер сессии: потоки прошлой встречи не пишут в новую
        self._stt_thread = None
        self._capture_done = threading.Event()
        self._capture_done.set()
        self._me_voice_t = 0.0
        self._underruns = 0
        self._mic_error = ""
        self._system_kind = ""   # tap | blackhole | monitor | wasapi | file — каким способом пишем собеседника
        self._tap_warning = ""
        self._post_busy = False  # идёт фон после остановки (расшифровка/итог) — новую встречу не начинаем
        self._post_thread = None
        self._ocr_q = queue.Queue(maxsize=20)
        self._ocr_thread = None
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
            if isinstance(raw, dict):
                if raw.get("version") != 1:
                    log.warning("🎬 Неизвестная версия расписания; файл сохранён без изменений")
                    return []
                raw = raw.get("items")
            rows = raw if isinstance(raw, list) else []
        except (OSError, ValueError, TypeError):
            return []
        return self._recover_schedules(rows)

    @staticmethod
    def _recover_schedules(rows, now=None):
        """Привести расписание в порядок после перезапуска/падения приложения.

        Состояния checking/starting/running живут только в памяти процесса.
        Если приложение закрылось посреди них, без этой нормализации строка
        навсегда зависала бы в «checking» (и не стартовала) или в «running».
        """
        now = time.time() if now is None else now
        for item in rows:
            if not isinstance(item, dict):
                continue
            if (item.get("preflight") or {}).get("state") == "checking":
                item.pop("preflight", None)
            state = item.get("state")
            if state == "starting":
                item["state"] = "scheduled"
            elif state == "running":
                item["state"] = "error"
                item["error"] = "приложение было закрыто во время записи — частичный итог мог не сохраниться"
        return [x for x in rows if isinstance(x, dict)]

    def _save_schedules(self):
        try:
            self._schedules_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._schedules_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "items": self._schedules}, ensure_ascii=False, indent=2), encoding="utf-8")
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
        return dt.replace(tzinfo=system_timezone()).timestamp()

    def schedules(self):
        with self._schedules_lock:
            rows = [dict(x) for x in self._schedules]
        for row in rows:
            if row.get("start_at"):
                row["start_local"] = local_text(row["start_at"])
            if row.get("end_at"):
                row["end_local"] = local_text(row["end_at"])
        return rows

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
        stop_running = False
        with self._schedules_lock:
            item = next((x for x in self._schedules if x.get("id") == sid), None)
            if item is None:
                raise ValueError("запись в расписании не найдена")
            # A running scheduled meeting must be stopped, not merely removed
            # from the list. The schedule id is attached to the active meeting
            # so a manual meeting can never be stopped by mistake.
            stop_running = bool(item.get("state") == "running" and self.on
                                and self.meeting.get("schedule_id") == sid)
            before = len(self._schedules)
            self._schedules = [x for x in self._schedules if x.get("id") != sid]
            if len(self._schedules) == before:
                raise ValueError("запись в расписании не найдена")
            self._save_schedules()
        if stop_running:
            self.stop(summary=True)
        self._emit("schedule", schedules=self.schedules())
        return {"cancelled": sid, "stopped": stop_running}

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
            missed = []
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
                        # Mac спал или Dewiar был закрыт: не начинать запись
                        # через несколько часов после назначенного времени.
                        end = float(item.get("end_at") or 0)
                        if (end and now >= end) or (not end and now - start > self.SCHEDULE_GRACE_S):
                            item["state"] = "missed"
                            item["error"] = "встреча пропущена: в назначенное время Dewiar не работал или Mac спал"
                            missed.append(dict(item))
                            continue
                        item["state"] = "starting"
                        due.append(dict(item))
                if checks or due or missed:
                    self._save_schedules()
            for item in missed:
                log.warning("🎬 Запланированная запись пропущена: %s", item.get("title"))
                self._emit("schedule", schedules=self.schedules())
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
                if self._post_busy and not self.on:
                    # Итог прошлой встречи ещё считается: подождём, а не
                    # отменяем — окно опоздания проверяется выше.
                    with self._schedules_lock:
                        for current in self._schedules:
                            if current.get("id") == item.get("id") and current.get("state") == "starting":
                                current["state"] = "scheduled"
                    continue
                try:
                    if self.on:
                        raise ValueError("другая встреча уже идёт")
                    opts = dict(item.get("options") or {})
                    opts.pop("title", None)
                    opts["scheduled_end"] = float(item.get("end_at") or 0) or None
                    self.start(title=item.get("title", "Встреча по расписанию"), schedule_id=item.get("id"), **opts)
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
                    self.stop(summary=True)        # stop() сам отметит строку расписания «done»

    def _finish_schedule(self, sid):
        """Отметить запущенную строку расписания завершённой (ручная остановка, конец по времени)."""
        if not sid:
            return
        changed = False
        with self._schedules_lock:
            for item in self._schedules:
                if item.get("id") == sid and item.get("state") == "running":
                    item["state"] = "done"
                    item["finished"] = time.time()
                    changed = True
            if changed:
                self._save_schedules()
        if changed:
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
                from audio_io import Capture
                if source == "system":
                    desc = self._loopback().probe()      # tap: без запуска записи и без системного запроса
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
        # Screen Recording is an optional part of a meeting.  Audio and the
        # transcript must still start when macOS has not granted this access;
        # only automatic frames/video are unavailable until the user enables it.
        needs_screen = bool(opts.get("auto_frames") or (opts.get("auto_first_frame") and record))
        screen_unavailable = False
        if needs_screen:
            try:
                from platform_info import MAC
                if MAC:
                    from Quartz import CGPreflightScreenCaptureAccess
                    allowed = bool(CGPreflightScreenCaptureAccess())
                    checks.append({"name": "запись экрана", "ok": True,
                                   "optional": True,
                                   "detail": "доступ разрешён" if allowed else
                                            "нет доступа — звук и расшифровка продолжатся без кадров"})
                    screen_unavailable = not allowed
            except ImportError:
                checks.append({"name": "запись экрана", "ok": True, "optional": True,
                               "detail": "проверка недоступна — встреча продолжится без кадров при необходимости"})
                screen_unavailable = True
            except Exception as exc:  # noqa: BLE001
                checks.append({"name": "запись экрана", "ok": True, "optional": True,
                               "detail": f"не проверен ({exc}); встреча продолжится без кадров"})
                screen_unavailable = True
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
        message = "Проверка пройдена; Dewiar запустит запись в назначенное время"
        if screen_unavailable:
            message += ". Запись экрана недоступна — звук и расшифровка продолжатся без кадров"
        return self._preflight_result("ok", checks, message)

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
                   "screenshots": len(self._screenshots),
                   "tracks": {k: Path(v).name for k, v in self._track_paths.items()},
                   "levels": {k: {"peak": int(v.get("peak", 0)), "silent_s": round(self._silent_for(k), 1)}
                              for k, v in list(self._levels.items())},
                   "underruns": self._underruns}
            rec["system_backend"] = self._system_kind
            rec["microphone_error"] = self._mic_error
            rec.update({"frame_interval": self._frame_interval, "frame_quality": self._frame_quality,
                        "video": bool(self._video_enabled), "video_frames": self._video_frames,
                        "video_path": Path(self._video_path).name if self._video_path else "",
                        "export_busy": self._video_export_busy,
                        "can_export": not self.on and bool(self._frame_events) and audio_ready})
            return {"on": self.on, "paused": self.paused, "processing": self._post_busy, "meeting": m, "status": self.status,
                    "files": [{"name": f["name"], "chars": f["chars"]} for f in self.files], "lines": self.lines[-30:],
                    "hints": self.hints[-20:], "sheet": self.sheet, "summary": self.summary,
                    "summary_path": self._summary_path,
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
    def _extract_participants(lines, summary=""):
        """Найти явно названных участников, когда поле не заполнили заранее.

        Основной сигнал — обращение с запятой: «Иван, пришлите договор» или
        «Спасибо, Анна». Дополнительно учитываем «меня зовут ...» и раздел
        ``Участники`` из итогового ответа модели, но принимаем из него только
        имена, которые действительно встречаются в расшифровке. Это не
        пытается определить человека по голосу и не заменяет введённый вручную
        список.
        """
        source = "\n".join(str(x.get("text") or "") for x in lines if x.get("who") != "screen")
        if not source.strip():
            return ""
        stop = {"вы", "я", "мы", "он", "она", "они", "коллега", "коллеги", "команда", "ребята",
                "друзья", "уважаемые", "давайте", "скажите", "слушайте", "извините", "спасибо",
                "пожалуйста", "хорошо", "добрый день", "добрый вечер", "доброе утро", "хороший вопрос"}
        found = []

        def add(value):
            value = re.sub(r"\s+", " ", str(value or "")).strip(" \t,.:;!?—–-«»\"")
            if not value or value.casefold() in stop or len(value) > 80:
                return
            # A direct address must look like a person name, not a sentence.
            if not re.fullmatch(r"[А-ЯЁ][а-яё]{2,}(?:\s+[А-ЯЁ][а-яё]{2,})?", value):
                return
            if value.casefold() not in {x.casefold() for x in found}:
                found.append(value)

        # «Иван, продолжайте» / «Иван Петров, добрый день».
        for match in re.finditer(r"(?<![\wА-ЯЁ])([А-ЯЁ][а-яё]{2,}(?:\s+[А-ЯЁ][а-яё]{2,})?)(?=\s*[,!:])", source):
            add(match.group(1))
        # «Спасибо, Анна» / «..., Анна?».
        for match in re.finditer(r",\s*([А-ЯЁ][а-яё]{2,}(?:\s+[А-ЯЁ][а-яё]{2,})?)(?=\s*[.!?])", source):
            add(match.group(1))
        # «Меня зовут Дмитрий» and «Это Анна, ...».
        for match in re.finditer(r"(?i)(?:меня\s+зовут|это|с\s+вами)\s+([А-ЯЁ][а-яё]{2,}(?:\s+[А-ЯЁ][а-яё]{2,})?)", source):
            add(match.group(1))

        # The model may recognize a name in a less regular phrase.  Trust it
        # only when the exact name is present in the transcript, to prevent
        # hallucinated participants from appearing in the protocol header.
        section = re.search(r"(?ims)^##\s*Участники\s*\n(.*?)(?=^##\s|\Z)", str(summary or ""))
        if section:
            for line in section.group(1).splitlines():
                candidate = re.sub(r"^\s*[-•*]\s*", "", line).split("—", 1)[0].split("–", 1)[0].strip()
                if candidate and re.search(rf"(?<![А-ЯЁа-яё]){re.escape(candidate)}(?![А-ЯЁа-яё])", source, re.I):
                    add(candidate)
        return ", ".join(found)[:1000]

    @staticmethod
    def _summary_preview(m):
        started = local_datetime(m.get("started") or time.time())
        return {"when": started.strftime("%d.%m.%Y %H:%M"),
                "participants": m.get("participants") or "не указаны",
                "topics": m.get("topics") or m.get("goal") or "не указаны",
                "decisions": m.get("decisions") or "будут определены после расшифровки"}

    def _meeting_dirs(self):
        """Каталоги одной встречи: (Встречи, Аудио, Расшифровки); при сбое рабочей папки — резервные."""
        try:
            root = self.eng.workspace.ensure() / "Встречи"
            audio, text = root / "Аудио", root / "Расшифровки"
            audio.mkdir(parents=True, exist_ok=True)
            text.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001 — протокол всё равно можно сохранить в резервный каталог
            root = self._dir
            audio, text = root / "recordings", root / "transcripts"
            audio.mkdir(parents=True, exist_ok=True)
            text.mkdir(parents=True, exist_ok=True)
        return root, audio, text

    def _meeting_base(self, title):
        """Одно имя на все файлы встречи: «2026-10-30 0946 Склад» или «… (2)».

        Раньше WAV и MD собирались из времени до минуты без проверки на
        занятость: две встречи с одним названием в одну минуту перезаписывали
        аудио и протокол друг друга, а TXT получал «(2)» — ссылки расходились.
        Теперь свободное имя ищется сразу по всем видам файлов встречи.
        """
        if self._base_name:
            return self._base_name
        root, audio, text = self._meeting_dirs()
        started = local_datetime((self.meeting or {}).get("started") or time.time())
        safe = re.sub(r'[\\/:*?"<>|]+', " ", str(title or "")).strip()[:60] or "Встреча"
        stem, n = f"{started:%Y-%m-%d %H%M} {safe}", 2
        base = stem
        while ((audio / f"{base}.wav").exists() or (text / f"{base}.txt").exists()
               or (root / f"{base}.md").exists() or (audio / base).exists()):
            base = f"{stem} ({n})"
            n += 1
        self._base_name = base
        return base

    @staticmethod
    def _new_wave(path):
        w = wave.open(str(path), "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        return w

    def _record_open(self, title, tracks=()):
        """Создать WAV до запуска потока: запись не теряется, если встреча закончится сразу.

        tracks — ("them", "me"): отдельные дорожки собеседника и ваша —
        ``Аудио/<имя>/Собеседник.wav`` и ``Вы.wav``. Общий WAV — смесь обеих
        (для прослушивания и видео), дорожки — для расшифровки с правильными
        подписями «Вы»/«Собеседник» без угадывания по голосу.
        """
        _root, audio, _text = self._meeting_dirs()
        base = self._meeting_base(title)
        path = audio / f"{base}.wav"
        with self._record_lock:
            self._record_path = str(path)
            self._record_wave = self._new_wave(path)
            self._track_waves, self._track_paths = {}, {}
            if tracks:
                folder = audio / base
                folder.mkdir(parents=True, exist_ok=True)
                for key in tracks:
                    p = folder / ("Вы.wav" if key == "me" else "Собеседник.wav")
                    self._track_paths[key] = str(p)
                    self._track_waves[key] = self._new_wave(p)

    def _transcript_open(self, title):
        """Открыть обычный UTF-8 .txt, который пополняется во время встречи."""
        _root, _audio, root = self._meeting_dirs()
        started = local_datetime((self.meeting or {}).get("started") or time.time())
        path = root / f"{self._meeting_base(title)}.txt"
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
        """Записать кадр в общий WAV (один источник, без дорожек)."""
        with self._record_lock:
            if self._record_wave is not None:
                try:
                    self._record_wave.writeframes(pcm)
                except (OSError, ValueError) as e:
                    log.warning("🎬 Запись встречи: %s", e)

    @staticmethod
    def _mix(a, b):
        """Сложить два кадра int16 одинаковой длины с ограничением, без переполнения."""
        import numpy as np
        x = np.frombuffer(a, dtype=np.int16).astype(np.int32)
        y = np.frombuffer(b, dtype=np.int16).astype(np.int32)
        n = min(len(x), len(y))
        return np.clip(x[:n] + y[:n], -32768, 32767).astype(np.int16).tobytes()

    def _record_write_tracks(self, them, me=None):
        """Один такт 32 мс: дорожки «Собеседник»/«Вы» и их смесь в общий WAV.

        Все файлы получают одинаковое число отсчётов — общий WAV остаётся
        единственными часами встречи (по ним считаются кадры и видео).
        """
        with self._record_lock:
            try:
                w = self._track_waves.get("them")
                if w is not None:
                    w.writeframes(them)
                w = self._track_waves.get("me")
                if w is not None:
                    w.writeframes(me if me is not None else b"\0" * len(them))
                if self._record_wave is not None:
                    self._record_wave.writeframes(self._mix(them, me) if me is not None else them)
            except (OSError, ValueError) as e:
                log.warning("🎬 Запись встречи: %s", e)

    def _record_close(self):
        with self._record_lock:
            waves = [self._record_wave] + list(self._track_waves.values())
            self._record_wave, self._track_waves = None, {}
            for w in waves:
                if w is not None:
                    try:
                        w.close()
                    except OSError:
                        pass

    # ---------- уровни и тишина ----------

    def _level(self, key, frame):
        """Пик кадра; запоминаем, когда на дорожке последний раз был настоящий сигнал."""
        try:
            import numpy as np
            peak = int(np.abs(np.frombuffer(frame, dtype=np.int16).astype(np.int32)).max()) if frame else 0
        except Exception:  # noqa: BLE001
            return 0
        lv = self._levels.setdefault(key, {"peak": 0, "signal_t": 0.0, "since": time.monotonic()})
        lv["peak"] = int(lv["peak"] * 0.9) if peak < lv["peak"] else peak     # медленный спад, для индикатора
        if peak >= self.SILENCE_PEAK:
            lv["signal_t"] = time.monotonic()
        return peak

    def _silent_for(self, key):
        lv = self._levels.get(key)
        if not lv:
            return 0.0
        return max(0.0, time.monotonic() - (lv.get("signal_t") or lv.get("since") or time.monotonic()))

    def _check_silence(self):
        """BlackHole отдаёт нули, если звук звонка не направлен в него (не выбран Multi-Output).

        Раньше такая встреча считалась успешной («WAV больше заголовка»), а
        пользователь узнавал о пустой записи только после часа разговора.
        """
        if not self.meeting.get("record") or self.meeting.get("audio_source") != "system":
            return
        silent = self._silent_for("them")
        if silent >= self.SILENCE_WARN_S and not self._silence_warned:
            self._silence_warned = True
            self.status = "звук собеседника не приходит уже %d с: %s" % (silent, self._silence_hint())
            log.warning("🎬 %s", self.status)
            self._emit("warning", code="system_silence", text=self.status)
            self._state()
        elif silent < 1.0 and self._silence_warned:
            self._silence_warned = False
            self.status = "звук собеседника снова идёт — записываю"
            self._state()

    def _silence_hint(self):
        """Что делать, если на дорожке собеседника тишина — зависит от способа записи."""
        if self._system_kind == "tap":
            return ("приложение звонка не воспроизводит звук, либо Dewiar не разрешена «Запись системного звука» "
                    "(Конфиденциальность и безопасность → Запись экрана и системного звука)")
        return ("в «Системных настройках → Звук → Выход» выберите Multi-Output Device с BlackHole "
                "(или источник в Loopback), иначе собеседник не запишется")

    @staticmethod
    def _wav_is_silent(path, peak=None):
        """Весь файл — цифровая тишина? (проверка кусками, без чтения всего WAV в память)."""
        import numpy as np
        peak = Prompter.SILENCE_PEAK if peak is None else peak
        try:
            with wave.open(str(path), "rb") as w:
                while True:
                    data = w.readframes(16000 * 30)
                    if not data:
                        return True
                    if int(np.abs(np.frombuffer(data, dtype=np.int16).astype(np.int32)).max()) >= peak:
                        return False
        except (OSError, EOFError, wave.Error):
            return False

    def _loopback(self, wav_path=None):
        """Источник звука компьютера по настройкам встречи.

        prompter.system_audio: auto (Core Audio tap, если есть хелпер и macOS 14.2+, иначе BlackHole),
        tap или blackhole; prompter.tap_target: all (всё, кроме Dewiar) или bundle id приложений звонка.
        """
        from audio_io import LoopbackCapture
        cfg = self.eng.s.get("prompter") if hasattr(self.eng.s, "get") else {}
        cfg = cfg if isinstance(cfg, dict) else {}
        return LoopbackCapture(wav_path=wav_path, backend=str(cfg.get("system_audio") or "auto"),
                               tap_target=str(cfg.get("tap_target") or "all"))

    def _probe_record_source(self, source):
        """Open and close the selected input before changing the meeting state."""
        from audio_io import Capture
        if source == "system":
            return self._loopback().probe()
        else:
            cfg = self.eng.s.get("audio") if hasattr(self.eng.s, "get") else {}
            device = cfg.get("input", "") if isinstance(cfg, dict) else ""
            cap = Capture(device=device or "", strict_device=bool(device))
        try:
            return cap.open()
        finally:
            cap.close()

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
              capture_source="screen", capture_region=None, capture_application="", scheduled_end=None, record_source=None,
              schedule_id=None, record_me=None):
        """Начать встречу. place: online — собеседник по звуку компьютера, вы — микрофон; live — очная встреча,
        один микрофон, кто говорит — по голосу (нужен запомненный голос владельца)."""
        if self.on:
            raise ValueError("Встреча уже идёт — сначала завершите её.")
        if self._video_export_busy:
            raise ValueError("Дождитесь создания видео встречи.")
        if self._post_busy:
            workers = (self._post_thread, self._stt_thread, self._loop_thread, self._frame_thread, self._ocr_thread)
            if self._post_thread is not None and not any(t and t.is_alive() for t in workers):
                self._post_busy = False  # поздно завершившийся I/O больше не удерживает приложение
        if self._post_busy:
            # Отложенная расшифровка/итог прошлой встречи ещё читает её файлы и
            # поля; новая встреча подменила бы их посреди обработки.
            raise ValueError("Дождитесь итога предыдущей встречи — расшифровка ещё идёт.")
        live = place == "live"
        if record and not consent:
            raise ValueError("для записи переговоров подтвердите согласие участников")
        if live and self.speaker.owner is None:
            raise ValueError("для очной встречи сначала запомните свой голос — «Запомнить мой голос»")
        screen_warning = ""
        # Screen capture is deliberately checked without requesting access.
        # A denied permission must never prevent an audio-only meeting from
        # starting.  If frames were requested, disable just that optional
        # part and explain the state in the live meeting window.
        if auto_frames or (auto_first_frame and record):
            try:
                from platform_info import MAC
                if MAC:
                    from Quartz import CGPreflightScreenCaptureAccess
                    if not bool(CGPreflightScreenCaptureAccess()):
                        if auto_frames:
                            auto_frames = False
                            frame_video = False
                        screen_warning = ("Встреча началась без записи экрана: разрешение macOS не дано. "
                                          "Аудио и расшифровка продолжаются; кадры можно включить после разрешения.")
            except ImportError:
                pass
        if self.enrolling is not None:
            self.enroll_stop(save=False)
        self._record_path = ""
        self._record_error = ""
        self._record_paused = False
        self._track_waves, self._track_paths, self._levels = {}, {}, {}
        self._silence_warned = False
        self._base_name = ""
        self._underruns = 0
        self._mic_error = ""
        self._system_kind = ""
        self._tap_warning = ""
        self._transcript_path = ""
        self._session_dir = None
        self._screenshots = []
        self._frame_events = []
        self._last_frame_sig = None
        self._fr_prev, self._fr_scenes, self._fr_still_since = None, [], 0.0
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
        # Онлайн-встреча с записью звука компьютера: собеседник — BlackHole,
        # вы — микрофон. Раньше микрофон в этом режиме выключался совсем, и в
        # WAV/протоколе были только реплики собеседника. Теперь обе дорожки
        # пишутся одновременно (record_me=False — прежнее поведение).
        if record_me is None:
            record_me = cfg.get("record_me", True)
        dual = bool(record and record_source == "system" and record_me)
        if record:
            try:
                self._probe_record_source(record_source)
            except Exception as exc:
                raise ValueError(f"запись не началась: {exc}") from exc
        with self._lock:
            self.meeting = {"title": (title or "").strip()[:120] or "Встреча", "goal": (goal or "").strip()[:2000],
                            "mode": "manual" if mode == "manual" else "auto", "them": bool(them) and not live, "me": bool(me) or live,
                            "place": "live" if live else "online", "nudge": bool(nudge),
                            "knowledge": bool(knowledge), "keep_text": bool(keep_text) or record,
                            "participants": self._meta_list(participants), "topics": self._meta_list(topics),
                            "record": record, "deferred": bool(deferred) if record else False,
                            "audio_source": record_source, "dual": dual,
                            "source": (source or "system")[:40], "auto_frames": bool(auto_frames), "started": time.time()}
            if scheduled_end:
                self.meeting["scheduled_end"] = float(scheduled_end)
            if schedule_id:
                self.meeting["schedule_id"] = str(schedule_id)
            self.lines, self.hints, self.sheet, self.summary, self.paused = [], [], [], "", False
            self._summary_path = ""
            # Новая сессия: потоки прошлой встречи (если join не дождался их)
            # видят чужой номер и ничего не пишут в эту встречу.
            self._session += 1
            session = self._session
            stop_ev = threading.Event()
            self._stop = stop_ev
            self.on = True
        self._transcript_open(self.meeting["title"])
        # Первый кадр — полезная точка отсчёта протокола. По умолчанию он
        # делается только для записываемой встречи или при включённых кадрах;
        # отказ в разрешении не отменяет саму запись.
        if auto_first_frame and (record or auto_frames) and not screen_warning:
            try:
                self.capture_frame(auto=False, ocr=True)
            except Exception as exc:  # noqa: BLE001
                self.status = "первый снимок не сделан: " + str(exc)[:160]
                log.info("🎬 Первый кадр не сохранён: %s", exc)
        if record:
            try:
                self._record_open(self.meeting["title"], tracks=("them", "me") if dual else ())
                # Оба источника открывает поток встречи; обычный захват
                # ассистента на это время отдаём ему, чтобы ассистент не
                # отвечал на реплики разговора.
                self._suspend_engine_mic(True)
            except Exception:
                self.on = False
                stop_ev.set()
                self._record_close()
                self._suspend_engine_mic(False)
                raise
            # Поток встречи сам слушает выбранные входы (и микрофон в dual).
            self.meeting["me"] = False
        self._await, self._pause_hint_t, self._them_voice_t, self._t0 = None, 0.0, 0.0, time.monotonic()
        self._me_voice_t = 0.0
        self._diar = self.speaker.diarizer() if self.speaker.owner is not None else None
        import queue
        q = queue.Queue(maxsize=200)
        self._q = q
        self._capture_done.set()
        if not live and self.meeting["them"]:
            self._capture_done.clear()
        self._stt_thread = threading.Thread(target=self._stt_loop, args=(stop_ev, q, session), name="prompter-stt", daemon=True)
        self._stt_thread.start()
        threading.Thread(target=self._watch, args=(stop_ev,), name="prompter-watch", daemon=True).start()
        if auto_frames:
            self._frame_thread = threading.Thread(target=self._frame_loop, args=(stop_ev,), name="prompter-frames", daemon=True)
            self._frame_thread.start()
        if not self.meeting["deferred"]:
            # Модель записи (GigaAM) грузится заранее: иначе первые фразы
            # ждали загрузки в очереди и могли потеряться.
            threading.Thread(target=self._preload_stt, name="prompter-stt-load", daemon=True).start()
        if live:
            self.status = "слушаю микрофон — вас и собеседника различаю по голосу"
        elif self.meeting["them"]:
            self._loop_thread = threading.Thread(target=self._them_loop, args=(stop_ev, q, session),
                                                 name="prompter-them", daemon=True)
            self._loop_thread.start()
        else:
            self.status = "собеседника не слушаю — подсказки по кнопке и на ваши фразы"
        if screen_warning:
            self.status = screen_warning
        if self.meeting["goal"] or self.files:
            threading.Thread(target=self._prepare_sheet, name="prompter-sheet", daemon=True).start()
        source_name = ("системный звук + ваш микрофон" if dual else
                       "системный звук" if record_source == "system" else "микрофон")
        log.info("🎬 Суфлёр включён: «%s» (%s, %s; собеседник: %s, я: %s; запись: %s)", self.meeting["title"], "вживую" if live else "онлайн",
                 "подсказки на вопросы" if self.meeting["mode"] == "auto" else "по кнопке",
                 "по голосу" if live else ("да" if self.meeting["them"] else "нет"), "да" if self.meeting["me"] or dual else "нет",
                 source_name if record else "нет")
        self.eng.set_state("idle")
        self._state()
        return self.public()

    def _preload_stt(self):
        try:
            self._meeting_stt()
        except Exception as exc:  # noqa: BLE001 — причина видна в статусе, встреча продолжается
            self.status = f"модель расшифровки не готова: {exc}"[:240]
            log.warning("🎬 %s", self.status)
            self._state()

    def pause(self, on=True):
        self.paused = bool(on)
        self._state()
        return self.public()

    def pause_recording_audio(self, on=True):
        """Приостановить только запись WAV, не останавливая встречу и кадры.

        На паузе в файл идёт тишина, а не пропуск: длина WAV остаётся равной
        времени встречи, поэтому кадры, реплики и видео не съезжают по времени.
        """
        if not self.on or not self.meeting.get("record"):
            raise ValueError("аудиозапись сейчас не включена")
        self._record_paused = bool(on)
        self.status = "аудиозапись на паузе" if self._record_paused else "аудиозапись продолжается"
        self._state()
        return self.public()

    def stop(self, summary=True):
        """Закончить встречу; summary — подвести итог (в фоне, событие summary).

        Сам вызов быстрый: потоки захвата останавливаются, файлы закрываются,
        а дочитывание очереди распознавания, сборка видео, отложенная
        расшифровка и итог идут в фоне. Раньше видео собиралось прямо здесь,
        и кнопка «Завершить» в меню macOS и поток расписания ждали ffmpeg.
        """
        with self._lock:
            if not self.on:
                return self.public()
            self.on = False
            self._post_busy = True
        self._frames_enabled = False
        stop_ev = self._stop
        stop_ev.set()
        self.meeting["ended"] = time.time()
        self._finish_schedule(self.meeting.get("schedule_id"))
        self._emit("summary", summary="", busy=True, phase="stopping")
        self._post_thread = threading.Thread(target=self._after_stop, args=(self._stt_thread, bool(summary and self.meeting)),
                                             name="prompter-after-stop", daemon=True)
        self._post_thread.start()
        self._state()
        return self.public()

    def _after_stop(self, stt_thread, summary):
        """Освободить захват и дождаться хвоста до обработки файлов; UI не ждёт I/O."""
        try:
            self._finish_capture()
            if stt_thread is not None and stt_thread is not threading.current_thread() and stt_thread.is_alive():
                stt_thread.join(timeout=60)
                if stt_thread.is_alive():
                    raise RuntimeError("Распознавание ещё занято; запись сохранена. Дождитесь завершения перед новой встречей.")
            if self._video_enabled:
                self._finalize_frame_video()
            if summary:
                if self.meeting.get("record") and self.meeting.get("deferred") and self._record_path:
                    self._post_transcribe()
                else:
                    self._summarize()
        except Exception as exc:  # ошибка не должна оставлять пустой экран итога
            self.status = str(exc)
            log.exception("🎬 Обработка после встречи")
            self._emit("summary", summary="Запись сохранена. Обработка не завершена: " + str(exc), busy=False)
        finally:
            # Старый STT не должен продолжать писать в новую встречу.
            self._post_busy = any(t and t.is_alive() for t in (stt_thread, self._loop_thread, self._frame_thread, self._ocr_thread))
            self._state()

    def _finish_capture(self):
        cap = self._loop_cap
        if cap is not None:
            try:
                cap.close()                         # sounddevice read должен выйти сразу после остановки
            except Exception:  # noqa: BLE001
                pass
        t = self._loop_thread
        if t is not None and t is not threading.current_thread() and t.is_alive():
            t.join(timeout=2.5)
        ft = self._frame_thread
        if ft is not None and ft is not threading.current_thread() and ft.is_alive():
            ft.join(timeout=max(1.0, self._frame_interval + 0.2))
        # OCR не тормозит захват кадров, но итог должен дождаться уже принятых кадров.
        if self._ocr_thread is not None:
            self._ocr_thread.join(timeout=30)
        self._record_close()
        if self.meeting.get("record"):
            self._suspend_engine_mic(False)
        if self._hint_cancel:
            self._hint_cancel.set()
        if ft is None or not ft.is_alive():
            self._frame_thread = None
        log.info("🎬 Суфлёр выключен: реплик %d, подсказок %d", len(self.lines), len(self.hints))
        # Даже тихая встреча должна оставить карточку в «Встречах»: иначе
        # пользователь не узнаёт, что запись стартовала, и не видит ошибку
        # источника звука.

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

    def _open_meeting_mic(self):
        """Ваш микрофон для второй дорожки онлайн-записи (тот же, что выбран в «Звуке»)."""
        from audio_io import Capture
        device = self.eng.s.get("audio", "input") if hasattr(self.eng.s, "get") else ""
        if isinstance(device, dict):
            device = device.get("input", "")
        mic = Capture(device=device or "", strict_device=bool(device))
        desc = mic.open()
        return mic, desc

    def _them_loop(self, stop_ev=None, q=None, session=None):
        """Поток prompter-them: звук компьютера (и в dual — ваш микрофон) → WAV-дорожки и фразы по VAD
        (начало — 2 кадра речи, конец — 0,6 с тишины, не длиннее 20 с) → очередь распознавания.

        Часы встречи — поток BlackHole: на каждый его кадр 32 мс берётся ровно
        один кадр микрофона (нет — тишина, накопилось больше MIC_MAX_LAG —
        лишние отбрасываются). Так дорожки не расходятся из-за разных часов
        устройств, а общий WAV совпадает со временем встречи.
        """
        import collections
        import os
        import queue
        from audio_io import Capture
        from listen import SileroVAD
        stop_ev = stop_ev or self._stop
        q = q if q is not None else self._q
        session = self._session if session is None else session
        if stop_ev.is_set() or session != self._session:
            self._capture_done.set()
            return
        wav_path = os.environ.get("DEWIAR_PROMPTER_WAV") or None
        record = bool(self.meeting.get("record"))
        deferred = bool(self.meeting.get("deferred"))
        if wav_path:
            cap = self._loopback(wav_path=wav_path)
        elif self.meeting.get("audio_source") == "microphone":
            device = self.eng.s.get("audio", "input") if hasattr(self.eng.s, "get") else ""
            cap = Capture(device=device or "", strict_device=bool(device))
        else:
            cap = self._loopback()
        self._loop_cap = cap
        try:
            self.status = "Подключаю источник звука; если macOS запросит доступ, разрешите запись."
            self._state()
            self.status = "слушаю: " + cap.open()
            self._system_kind = getattr(cap, "kind", "") or ""
        except Exception as e:  # noqa: BLE001
            self._capture_done.set()
            cap.close()
            self._record_error = str(e)[:240] if record else ""
            self.status = (f"запись не началась: {e}" if record
                           else f"звук компьютера не слышу ({e}) — подсказки по кнопке и на ваши фразы")
            log.warning("🎬 Суфлёр: %s", self.status)
            if record and session == self._session:
                self.stop(summary=True)           # итог с причиной ошибки — через обычный путь stop()
                return
            self._state()
            return
        mic, mic_buf, mic_thread = None, collections.deque(maxlen=400), None
        if self.meeting.get("dual") and not wav_path:
            try:
                mic, desc = self._open_meeting_mic()
                log.info("🎬 Вторая дорожка записи: %s", desc)
            except Exception as e:  # noqa: BLE001 — без микрофона запись собеседника всё равно идёт
                mic = None
                self._mic_error = f"ваш микрофон не открылся ({e}) — записываю только собеседника"[:240]
                log.warning("🎬 %s", self._mic_error)
        if mic is not None:
            def mic_reader():
                misses = 0
                while not stop_ev.is_set():
                    f = mic.read()
                    if f:
                        misses = 0
                        mic_buf.append(f)
                        continue
                    misses += 1
                    if misses >= 3 and not stop_ev.is_set():   # микрофон пропал (сон, отключили гарнитуру)
                        misses = 0
                        try:
                            mic.close()
                            log.warning("🎬 Переподключаю микрофон встречи: %s", mic.open())
                        except Exception as e:  # noqa: BLE001
                            log.warning("🎬 Микрофон встречи не переподключился: %s", e)
                    time.sleep(0.05)
            mic_thread = threading.Thread(target=mic_reader, name="prompter-mic", daemon=True)
            mic_thread.start()
        if record:
            self.status = ("записываю системный звук и ваш микрофон (две дорожки)" if mic is not None else
                           "записываю микрофон в WAV; микрофон Dewiar не используется"
                           if self.meeting.get("audio_source") == "microphone" else
                           "записываю системный звук; микрофон Dewiar не используется")
            if self._mic_error:
                self.status += "; " + self._mic_error
        self._state()
        try:
            vad = SileroVAD(DATA_DIR / "silero_vad.onnx") if not deferred else None
            vad_me = SileroVAD(DATA_DIR / "silero_vad.onnx") if (mic is not None and not deferred) else None
        except Exception as exc:
            self._record_error = "Не удалось загрузить VAD: " + str(exc)
            self._capture_done.set()
            stop_ev.set()
            cap.close()
            if mic is not None:
                mic.close()
            self.stop(summary=True)
            return
        seg = _Segmenter(int(0.6 / FRAME_S))
        seg_me = _Segmenter(int(0.6 / FRAME_S)) if vad_me is not None else None
        empty, n = 0, 0
        try:
            while not stop_ev.is_set():
                frame = cap.read()
                if not frame:
                    if wav_path:
                        break
                    empty += 1
                    # Сон Mac, смена устройства вывода, отключённые наушники:
                    # поток перестаёт отдавать кадры. Раньше цикл молча ждал
                    # вечно; теперь источник переоткрывается.
                    if empty >= 3 and not stop_ev.is_set():
                        empty = 0
                        try:
                            cap.close()
                            self.status = "переподключаю звук: " + cap.open()
                            log.warning("🎬 %s", self.status)
                        except Exception as e:  # noqa: BLE001
                            self.status = f"звук пропал ({e}) — пробую снова"[:240]
                        self._state()
                    time.sleep(0.2)
                    continue
                empty = 0
                if session != self._session:
                    break                              # встреча уже другая — этот поток устарел
                me_frame = None
                if mic is not None:
                    while len(mic_buf) > self.MIC_MAX_LAG:
                        mic_buf.popleft()
                    if mic_buf:
                        me_frame = mic_buf.popleft()
                    else:
                        self._underruns += 1
                    if me_frame is None or len(me_frame) != len(frame):
                        me_frame = b"\0" * len(frame)
                    self._level("me", me_frame)
                self._level("them", frame)
                n += 1
                if n % 31 == 0:                        # ≈ раз в секунду
                    self._check_silence()
                    warn = getattr(cap, "warning", "") or ""
                    if warn and warn != self._tap_warning:
                        self._tap_warning = warn       # например, нули из-за отсутствия разрешения
                        self.status = warn
                        self._emit("warning", code="tap", text=warn)
                        self._state()
                    self._state()                      # уровни дорожек и диагностика в живом окне
                if record:
                    silent = b"\0" * len(frame)
                    muted = self.paused or self._record_paused
                    if mic is not None or self._track_waves:
                        self._record_write_tracks(silent if muted else frame,
                                                  (silent if muted else me_frame) if mic is not None else None)
                    else:
                        self._record_write(silent if muted else frame)
                if self.paused:
                    continue
                if record and deferred:
                    continue
                p = vad.prob(frame)
                if p > 0.5:
                    self._them_voice_t = time.monotonic()
                pcm = seg.feed(frame, p)
                if pcm:
                    # Время фразы — по номеру отсчёта в потоке (= позиция в WAV), а не по
                    # моменту распознавания: очередь STT не сдвигает реплики во времени.
                    t_start = max(0.0, n * FRAME_S - len(pcm) / 32000.0 - 0.3)
                    try:
                        q.put_nowait(("them", pcm, session, t_start))
                    except Exception:  # noqa: BLE001
                        log.warning("🎬 Очередь распознавания полна — фраза собеседника пропущена")
                if seg_me is not None:
                    pm = vad_me.prob(me_frame)
                    if pm > 0.5:
                        self._me_voice_t = time.monotonic()
                    pcm = seg_me.feed(me_frame, pm)
                    if pcm:
                        t_start = max(0.0, n * FRAME_S - len(pcm) / 32000.0 - 0.3)
                        try:
                            q.put_nowait(("me", pcm, session, t_start))
                        except Exception:  # noqa: BLE001
                            log.warning("🎬 Очередь распознавания полна — ваша фраза пропущена")
        finally:
            if not deferred and not self.paused and session == self._session and q is not None:
                for who, segment in (("them", seg), ("me", seg_me)):
                    pcm = segment.flush() if segment is not None else None
                    if pcm:
                        try:
                            q.put_nowait((who, pcm, session, max(0.0, n * FRAME_S - len(pcm) / 32000)))
                        except queue.Full:
                            log.warning("🎬 Последняя фраза не поместилась в очередь; исходное аудио сохранено")
            self._capture_done.set()
            cap.close()
            if mic is not None:
                try:
                    mic.close()
                except Exception:  # noqa: BLE001
                    pass
            if self._loop_cap is cap:
                self._loop_cap = None
            if self._loop_thread is threading.current_thread():
                self._loop_thread = None

    @staticmethod
    def _is_echo(text, t, them_lines, window=4.0, ratio=0.6):
        """Реплика микрофона — эхо собеседника из колонок? (почти тот же текст в пределах window секунд)."""
        import difflib
        norm = lambda v: " ".join(re.findall(r"[а-яёa-z0-9]+", str(v or "").lower()))
        mine = norm(text)
        if len(mine) < 6:
            return False
        for x in them_lines:
            if abs(float(x.get("t") or 0) - float(t or 0)) <= window:
                theirs = norm(x.get("text"))
                if theirs and (mine in theirs or difflib.SequenceMatcher(None, mine, theirs).ratio() >= ratio):
                    return True
        return False

    def _transcribe_wav(self, path):
        if hasattr(self.eng, "current_stt"):
            return self.eng.transcribe_file(path, stt=self._meeting_stt())
        return self.eng.transcribe_file(path)  # компактные тестовые/внешние движки используют старую сигнатуру

    def _post_transcribe(self):
        """После встречи: закрытая запись -> транскрипция (по дорожкам, если они есть) -> итоговый протокол."""
        path = self._record_path
        self._emit("summary", summary="", busy=True, phase="transcribe", recording=Path(path).name if path else "")
        try:
            if not path or not Path(path).is_file() or Path(path).stat().st_size <= 44:
                raise RuntimeError(self._record_error or "аудиофайл пустой — проверьте источник записи и разрешение микрофона/звука")
            tracks = {k: v for k, v in self._track_paths.items() if Path(v).is_file() and Path(v).stat().st_size > 44}
            notes = []
            lines = []
            if tracks:
                for who in ("them", "me"):
                    p = tracks.get(who)
                    if not p:
                        continue
                    if self._wav_is_silent(p):
                        notes.append("звук собеседника не записан (тишина): " + self._silence_hint() if who == "them" else
                                     "ваш микрофон записал тишину — проверьте разрешение «Микрофон» для Dewiar")
                        continue
                    lines += [{"who": who, "text": text, "t": round(float(start), 1)}
                              for start, _end, text in self._transcribe_wav(p) if str(text or "").strip()]
                # Без наушников микрофон слышит собеседника из колонок: такие
                # «ваши» реплики повторяют дорожку собеседника — убираем эхо.
                them_lines = [x for x in lines if x["who"] == "them"]
                lines = [x for x in lines if x["who"] != "me" or not self._is_echo(x["text"], x["t"], them_lines)]
                lines.sort(key=lambda x: (x["t"], 0 if x["who"] == "them" else 1))
            else:
                if self.meeting.get("audio_source") == "system" and self._wav_is_silent(path):
                    notes.append("звук собеседника не записан (тишина): " + self._silence_hint())
                else:
                    lines = [{"who": "them", "text": text, "t": round(float(start), 1)}
                             for start, _end, text in self._transcribe_wav(path) if str(text or "").strip()]
            if notes:
                self._record_error = "; ".join(filter(None, [self._record_error] + notes))[:600]
            with self._lock:
                screens = [x for x in self.lines if x.get("who") == "screen"]
                self.lines = sorted(lines + screens, key=lambda x: float(x.get("t") or 0))
            # Отложенная расшифровка создаёт тот же живой .txt, который был бы
            # заполнен в реальном времени.
            try:
                p = Path(self._transcript_path)
                p.write_text(f"# {self.meeting.get('title', 'Встреча')}\n# Отложенная расшифровка\n\n", encoding="utf-8")
            except OSError:
                pass
            for line in self.lines:
                self._transcript_write(line)
                if line.get("who") != "screen":
                    self._emit("line", line=line)
            self.status = f"расшифровано фраз: {len(lines)}" + (f"; {'; '.join(notes)}" if notes else "")
        except Exception as e:  # noqa: BLE001 — протокол всё равно будет создан с сообщением об ошибке
            self.status = "расшифровка не получилась: " + str(e)[:180]
            log.warning("🎬 Расшифровка записи: %s", e)
        self._summarize()

    def _stt_loop(self, stop_ev=None, q=None, session=None):
        """Распознавание фраз встречи. После остановки очередь дочитывается (до 45 с), а не бросается:
        иначе последние реплики разговора не попадали в протокол."""
        stop_ev = stop_ev or self._stop
        q = q if q is not None else self._q
        session = self._session if session is None else session
        deadline = None
        while True:
            if stop_ev.is_set():
                if deadline is None:
                    deadline = time.monotonic() + 45
                if (q.empty() and self._capture_done.is_set()) or time.monotonic() > deadline or session != self._session:
                    break
            try:
                item = q.get(timeout=0.5)
            except Exception:  # noqa: BLE001 — пусто
                continue
            who, pcm = item[0], item[1]
            if len(item) > 2 and item[2] != session:
                continue
            t_start = item[3] if len(item) > 3 else None
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
                self.add_line(who, text, session=session, t=t_start)

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

    MAX_LINES = 20000   # вся встреча целиком: протокол строится по всей расшифровке, а не по хвосту

    def add_line(self, who, text, session=None, t=None):
        if session is not None and session != self._session:
            return                                     # фраза прошлой встречи, дочитанная после старта новой
        if t is None:
            t = time.time() - self.meeting.get("started", time.time())
        line = {"who": who, "text": text, "t": round(float(t), 1)}
        if who == "me" and self.meeting.get("dual"):
            with self._lock:
                recent = [x for x in self.lines[-8:] if x.get("who") == "them"]
            if self._is_echo(text, line["t"], recent, window=6.0):
                log.info("🎬 Суфлёр: эхо собеседника в микрофоне — пропускаю")
                return
        with self._lock:
            self.lines.append(line)
            self.lines = self.lines[-self.MAX_LINES:]
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
        return max(self._them_voice_t, self._me_voice_t, getattr(self.eng, "mic_voice_t", 0.0) or 0.0)

    def _watch(self, stop_ev=None):
        """Поток prompter-watch: «замешкались» после вопроса и долгая пауза в разговоре."""
        stop_ev = stop_ev or self._stop
        while not stop_ev.wait(0.5):
            m = self.meeting
            if self.paused or m.get("mode") != "auto" or not m.get("nudge", True):
                continue
            now = time.monotonic()
            a = self._await
            if a:
                mic = max(getattr(self.eng, "mic_voice_t", 0.0) or 0.0, self._me_voice_t)
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

    # Каскад автокадров (идея из raliev/audiotranscripts, autoframes.py): дешёвое сравнение
    # пикселей уменьшенного кадра решает почти всё. Кадр-кандидат — только когда экран
    # постоял неподвижно FRAME_STABLE_S после изменения: переходы слайдов, прокрутка и набор
    # текста в протокол не попадают. Новая сцена — если кандидат заметно отличается от
    # текущей; возврат к одному из недавних слайдов — повтор, а не новый кадр.
    FRAME_W, FRAME_H = 96, 54
    FRAME_PIX = 18            # 0..255: насколько должен измениться пиксель, чтобы считаться изменённым
    FRAME_STILL = 0.004       # доля изменённых пикселей между тактами, ниже которой экран «стоит»
    FRAME_STABLE_S = 1.5
    FRAME_SCENE = 0.03        # доля изменённых пикселей против текущей сцены → новая сцена
    FRAME_SPARSE = 0.5        # …или изменилось больше половины «содержимого» редкого слайда
    FRAME_SPARSE_MIN = 0.004
    FRAME_REVISIT = 12        # сколько недавних сцен проверять на «вернулись к прошлому слайду»

    @staticmethod
    def _frame_signature(data):
        """Уменьшенный серый кадр (numpy uint8) для сравнения сцен; None — кадр не читается."""
        try:
            import numpy as np
            from PIL import Image, ImageFilter
            im = Image.open(io.BytesIO(data)).convert("L").resize((Prompter.FRAME_W, Prompter.FRAME_H), Image.BOX)
            return np.asarray(im.filter(ImageFilter.BoxBlur(1)), dtype=np.int16)
        except Exception:  # noqa: BLE001 — плохой кадр всё равно можно сохранить вручную
            return None

    @classmethod
    def _changed(cls, a, b):
        import numpy as np
        if a is None or b is None or a.shape != b.shape:
            return 1.0
        return float(np.mean(np.abs(a - b) > cls.FRAME_PIX))

    @classmethod
    def _new_scene(cls, scene, sig):
        """Кандидат — новая сцена относительно scene? Учитывает «редкие» слайды (тёмный фон, мало текста)."""
        import numpy as np
        moved = cls._changed(scene, sig)
        if moved >= cls.FRAME_SCENE:
            return True
        if moved < cls.FRAME_SPARSE_MIN:
            return False
        bg = np.median(scene)
        content = float(np.mean(np.abs(scene - bg) > cls.FRAME_PIX))
        return content > 0 and moved / content > cls.FRAME_SPARSE

    def _frame_triage(self, sig):
        """-> ("new", None) | ("skip", причина) | ("revisit", путь к уже сохранённой сцене)."""
        now = time.monotonic()
        prev, self._fr_prev = self._fr_prev, sig
        if not self._fr_scenes:
            self._fr_still_since = now
            return "new", None
        if prev is None or self._changed(prev, sig) > self.FRAME_STILL:
            self._fr_still_since = now                 # экран ещё меняется — ждём, пока успокоится
            return "skip", "экран меняется"
        if now - self._fr_still_since < max(self.FRAME_STABLE_S, self._frame_interval) - 0.05:
            return "skip", "жду, пока экран постоит"
        if not self._new_scene(self._fr_scenes[-1][0], sig):
            return "skip", "экран почти не изменился"
        for old, path in reversed(self._fr_scenes[-self.FRAME_REVISIT:-1]):
            if not self._new_scene(old, sig):
                return "revisit", path
        return "new", None

    def _remember_scene(self, sig, path):
        if sig is not None:
            self._fr_scenes.append((sig, path))
            self._fr_scenes = self._fr_scenes[-64:]

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
        # С аудиодорожкой видео строится по событиям сцен. Отдельные JPEG
        # каждого такта там не используются и занимали гигабайты в час.
        if auto and self._video_enabled and not self.meeting.get("record"):
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
        if auto and sig is not None:
            verdict, info = self._frame_triage(sig)
            if verdict == "skip":
                return {"skipped": True, "reason": info}
            if verdict == "revisit":
                # Вернулись к прошлому слайду: новый файл не нужен, но в шкалу времени
                # (и в видео) сцена возвращается в текущий момент.
                self._remember_frame(info, stamp)
                self._remember_scene(sig, info)
                existing = next((x for x in self._screenshots if x.get("path") == info), {"path": info})
                return dict(existing, revisit=True)
        digest = hashlib.sha1(data).hexdigest()
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
        path = frames / f"{n:04d}-{digest[:12]}{suffix}"
        write_frame(path, data, stamp, max(40, min(100, int(quality or self._frame_quality))) if auto else None)
        text = ""
        if ocr and not auto:
            try:
                from ocr import recognize
                text = recognize(data)
            except Exception as exc:  # noqa: BLE001
                log.info("🎬 OCR кадра не выполнился: %s", exc)
        # Время строки — по часам записи (как у реплик), иначе по часам встречи.
        t = round(audio_time if self.meeting.get("record") and self._record_path else elapsed, 1)
        rel = str(path.relative_to(Path(self._transcript_path).parent)) if self._transcript_path else str(path)
        line = {"who": "screen", "kind": "screen", "text": text or "Снимок экрана", "t": t,
                "path": rel, "ocr": text}
        entry = {"path": rel, "ocr": text, "sha1": digest, "t": t, **stamp}
        self._screenshots.append(entry)
        self._remember_scene(sig, rel)
        self._remember_frame(rel, stamp)
        with self._lock:
            self.lines.append(line)
            self.lines = self.lines[-self.MAX_LINES:]
        self._transcript_write(line)
        self._emit("line", line=line)
        if ocr and auto:
            try:
                self._ocr_q.put_nowait((data, entry, line, self._session))
                if self._ocr_thread is None or not self._ocr_thread.is_alive():
                    self._ocr_thread = threading.Thread(target=self._ocr_loop, args=(self._stop,), name="meeting-ocr", daemon=True)
                    self._ocr_thread.start()
            except queue.Full:
                log.warning("🎬 OCR не успевает: кадр сохранён без текста")
        self._state()
        return dict(entry)

    def _ocr_loop(self, stop_ev):
        while not stop_ev.is_set() or not self._ocr_q.empty():
            try:
                data, entry, line, session = self._ocr_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                from ocr import recognize
                text = recognize(data)
                if text and session == self._session:
                    with self._lock:
                        entry["ocr"] = text
                        line.update(text=text, ocr=text)
                    self._transcript_write(dict(line, text="OCR: " + text))
                    self._state()
            except Exception as exc:
                log.info("🎬 OCR кадра не выполнился: %s", exc)
            finally:
                self._ocr_q.task_done()

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

    def _frame_loop(self, stop_ev=None):
        """Проверять экран с заданным интервалом; неизменившиеся сцены отбрасывать."""
        stop_ev = stop_ev or self._stop
        failures = 0
        while self.on and self._frames_enabled and not stop_ev.wait(self._frame_interval):
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
        if not self._frame_events or not self._session_dir:
            return ""
        try:
            root = Path(self._session_dir)
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
            if not self._video_dir or self._video_frames < 2:
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

    @staticmethod
    def _talk_lines(lines):
        """Реплики для модели с таймкодами: «[12:05] Собеседник: …» — по ним в протоколе видно, когда договорились."""
        out = []
        for x in lines:
            sec = max(0, int(float(x.get("t") or 0)))
            who = "Собеседник" if x["who"] == "them" else "Вы" if x["who"] == "me" else "Экран (OCR)"
            out.append(f"[{sec // 60:02d}:{sec % 60:02d}] {who}: {x['text']}")
        return out

    def _screen_reference(self, budget=4000):
        """Текст с экрана (OCR кадров) — справочник для модели: как пишутся имена, термины, цифры."""
        seen, out, size = set(), [], 0
        for x in self._screenshots:
            text = re.sub(r"\s+", " ", str(x.get("ocr") or "")).strip()
            if len(text) < 8 or text in seen:
                continue
            seen.add(text)
            sec = max(0, int(float(x.get("t") or 0)))
            row = f"[{sec // 60:02d}:{sec % 60:02d}] {text[:800]}"
            if size + len(row) > budget:
                break
            out.append(row)
            size += len(row) + 1
        return "\n".join(out)

    @classmethod
    def _split_parts(cls, lines, talk):
        """Разбить расшифровку на части не длиннее SUMMARY_PART по самой длинной паузе в конце части
        (а не посреди обмена репликами): вопрос и ответ на него оказываются в одной части."""
        parts, cur, size = [], [], 0
        for x, ln in zip(lines, talk):
            ln = ln[: cls.SUMMARY_PART]
            if cur and size + len(ln) + 1 > cls.SUMMARY_PART:
                lo = max(1, int(len(cur) * 0.75))
                gaps = [(float(cur[i][0].get("t") or 0) - float(cur[i - 1][0].get("t") or 0), i)
                        for i in range(lo, len(cur))]
                cut = max(gaps)[1] if gaps else len(cur)
                parts.append([c[1] for c in cur[:cut]])
                cur = cur[cut:]
                size = sum(len(c[1]) + 1 for c in cur)
            cur.append((x, ln))
            size += len(ln) + 1
        if cur:
            parts.append([c[1] for c in cur])
        return parts

    def _summary_text(self, m, lines):
        """Итог по ВСЕЙ встрече. Короткая — одним запросом; длинная — по частям (заметки по каждой части),
        затем общий протокол по заметкам. Раньше в модель шли только последние 24 000 знаков, и у часовой
        встречи начало (а с ним договорённости первых 40 минут) в протокол не попадало.
        Текст с экрана идёт отдельным справочником: модель исправляет по нему имена и термины, которые
        распознавание речи пишет с ошибками (идея «Beautify» из raliev/audiotranscripts)."""
        head = f"ВСТРЕЧА: {m.get('title', '')}\nЦЕЛЬ: {m.get('goal') or '—'}\n"
        if m.get("participants"):
            head += f"УЧАСТНИКИ (указаны заранее): {m['participants']}\n"
        ref = self._screen_reference()
        if ref:
            head += ("\nТЕКСТ С ЭКРАНА (OCR; справочник для написания имён, терминов и цифр — "
                     "это не реплики участников):\n" + ref + "\n")
        speech = [x for x in lines if x.get("who") != "screen"]
        talk = self._talk_lines(speech)
        if sum(len(x) + 1 for x in talk) <= self.SUMMARY_PART:
            return self._ask_model(SUMMARY_SYSTEM, head + "\nРАСШИФРОВКА:\n" + "\n".join(talk), max_tokens=1200)
        parts = self._split_parts(speech, talk)
        notes = []
        for i, part in enumerate(parts, 1):
            self._emit("summary", summary="", busy=True, phase="summary_part", part=i, parts=len(parts))
            note = self._ask_model(NOTES_SYSTEM, head + f"\nЧАСТЬ {i} ИЗ {len(parts)}:\n" + "\n".join(part), max_tokens=900)
            notes.append(f"### Часть {i} ({part[0][1:6]}–{part[-1][1:6]})\n{note or '(модель не ответила)'}")
        return self._ask_model(SUMMARY_SYSTEM, head + "\nВстреча длинная, расшифровка разбита на части. ЗАМЕТКИ ПО ЧАСТЯМ "
                               "(используй их как расшифровку):\n\n" + "\n\n".join(notes), max_tokens=1500)

    def _save_raw_lines(self, lines):
        """Сырые реплики рядом с .txt: итог можно пересобрать другой моделью без повторной расшифровки."""
        if not self._transcript_path:
            return ""
        try:
            p = Path(self._transcript_path).with_suffix(".lines.json")
            meta = {k: self.meeting.get(k) for k in ("title", "goal", "participants", "topics", "started", "ended",
                                                      "audio_source", "dual", "place")}
            p.write_text(json.dumps({"version": 1, "meeting": meta, "tracks": dict(self._track_paths),
                                     "system_audio": self._system_kind, "lines": lines},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
            return str(p)
        except (OSError, TypeError, ValueError) as exc:
            log.info("🎬 Сырые реплики не сохранены: %s", exc)
            return ""

    def _summarize(self):
        m = dict(self.meeting)
        record_path = self._record_path
        with self._lock:
            lines = list(self.lines)
        self._save_raw_lines(lines)
        # A manually entered list always wins.  Otherwise infer only names
        # explicitly used in the transcript; the model gets a second chance
        # below for natural phrases that do not match the comma pattern.
        if not m.get("participants"):
            inferred = self._extract_participants(lines)
            if inferred:
                m["participants"] = inferred
                with self._lock:
                    self.meeting["participants"] = inferred
        self._emit("summary", summary="", busy=True)
        try:
            text = self._summary_text(m, lines)
        except Exception as e:  # noqa: BLE001
            text = ""
            log.info("🎬 Итог встречи не получился: %s", e)
        if not m.get("participants"):
            inferred = self._extract_participants(lines, text)
            if inferred:
                m["participants"] = inferred
                with self._lock:
                    self.meeting["participants"] = inferred
        started = local_datetime(m.get("started") or time.time())
        # Длительность — по часам встречи, а не по времени последней реплики
        # (тихий хвост или отложенная расшифровка без речи давали «1 мин»).
        duration = float(m.get("ended") or time.time()) - float(m.get("started") or time.time())
        if record_path and Path(record_path).is_file():
            try:
                from meeting_video import audio_duration
                duration = max(duration if m.get("ended") else 0.0, audio_duration(record_path))
            except Exception:  # noqa: BLE001
                pass
        mins = max(1, round(duration / 60))
        meeting_root = (self.eng.workspace.ensure() / "Встречи").resolve()

        def meeting_link(value, label=None, relative_to=None):
            """Create a safe relative link into this meeting's folder."""
            path = Path(value)
            if not path.is_absolute():
                path = (Path(relative_to) / path) if relative_to else (meeting_root / path)
            try:
                rel = path.resolve().relative_to(meeting_root)
            except ValueError:
                return str(label or path.name)
            shown = str(label or rel).replace("\\", "/")
            target = quote(str(rel).replace("\\", "/"), safe="/._-")
            return f"[{shown}](dwr-file:{target})"

        transcript_root = Path(self._transcript_path).parent if self._transcript_path else meeting_root
        record_link = meeting_link(record_path, f"Аудио/{Path(record_path).name}") if record_path else ""
        track_links = ", ".join(
            meeting_link(v, "Вы" if k == "me" else "Собеседник")
            for k, v in sorted(self._track_paths.items(), key=lambda kv: kv[0] != "me")
            if Path(v).is_file() and Path(v).stat().st_size > 44)
        transcript_link = meeting_link(self._transcript_path, f"Расшифровки/{Path(self._transcript_path).name}") if self._transcript_path else ""
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
                + (f"**Аудиозапись:** {record_link}\n\n" if record_link else "")
                + (f"**Дорожки:** {track_links}\n\n" if track_links else "")
                + (f"**Статус аудиозаписи:** {self._record_error}\n\n" if self._record_error else "")
                + (f"**Микрофон:** {self._mic_error}\n\n" if self._mic_error else "")
                + (f"**Расшифровка:** {transcript_link}\n\n" if transcript_link else "")
                + (text or "_Итог не получился — модель не ответила._")
                + ("\n\n## Материалы\n" + "\n".join(f"- {f['name']}" for f in self.files) if self.files else "")
                + ("\n\n## Расшифровка\n" + "\n".join(
                    ("**Собеседник:** " if x["who"] == "them" else "**Вы:** " if x["who"] == "me" else "**Экран:** ") + x["text"]
                    for x in lines)
                   if m.get("keep_text") else ""))
        if self._screenshots:
            body += "\n\n## Кадры экрана\n" + "\n".join(
                f"- {meeting_link(x['path'], relative_to=transcript_root)}" + (f" — {x['ocr']}" if x.get("ocr") else "") for x in self._screenshots)
        if self._video_path:
            body += f"\n\n## Видео из кадров\n- {meeting_link(self._video_path, Path(self._video_path).name)} — таймлапс, кадр каждые {self._frame_interval:g} с"
        path = ""
        try:
            d = self.eng.workspace.ensure() / "Встречи"
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"{self._meeting_base(m.get('title', 'Встреча'))}.md"
            f.write_text(body, encoding="utf-8")
            path = str(f)
        except OSError as e:
            log.warning("🎬 Итог встречи не сохранён: %s", e)
        self.summary = body
        self._summary_path = path
        log.info("🎬 Итог встречи: %s", path or "(не сохранён)")
        self._emit("summary", summary=body, path=path, busy=False)

