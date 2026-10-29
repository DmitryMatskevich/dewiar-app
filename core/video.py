"""Текст видео по ссылке — для конспекта (18.10, комментарий под видео: «вставить ссылку на YouTube или Rutube, сделать
конспект и сохранить вместе со ссылкой»). Страница YouTube отдаёт только описание — текст ролика лежит в субтитрах.
Берём их через yt-dlp (без скачивания видео): свои субтитры, иначе автоматические; русские, иначе английские, иначе
любые. Длинное видео — равномерные отрезки с метками времени, чтобы конспект охватил всё, а не только начало.

Встроено в read_webpage (tools.py): модель и так открывает ссылки им — новых инструментов и правил в подсказке нет.
Нет yt-dlp, нет субтитров — честно говорим, что есть (название, описание), и что конспект по звуку — фоновая задача."""
import json
import logging
import re

import httpx

log = logging.getLogger("dewiar.video")

VIDEO_RX = re.compile(r"^https?://(?:www\.|m\.|music\.)?(?:youtube\.com/(?:watch\?|shorts/|live/|embed/)|youtu\.be/|"
                      r"rutube\.ru/(?:video|shorts|play/embed)/|vk\.com/video|vkvideo\.ru/video)", re.I)
TEXT_MAX = 24000          # столько текста отдаём модели целиком; длиннее — отрезками по всему видео


class NoTranscript(Exception):
    """Субтитров нет (или yt-dlp нет): .info — что известно о видео (название, описание)."""

    def __init__(self, msg, info=None):
        super().__init__(msg)
        self.info = info or {}


def is_video(url):
    return bool(VIDEO_RX.match((url or "").strip()))


def _hms(sec):
    sec = int(sec or 0)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _pick(info):
    """-> (дорожка {ext, url}, язык, автоматические?) или None. Свои субтитры лучше автоматических, русские — лучше прочих."""
    own, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}

    def best(tracks):
        for ext in ("json3", "vtt", "srv3", "srt"):
            for t in tracks or []:
                if t.get("ext") == ext and t.get("url"):
                    return t
        return None
    order = []
    for lang_ok in (lambda k: k == "ru" or k.startswith("ru-"), lambda k: k == "en" or k.startswith("en-"), lambda k: True):
        order += [(own, k, False) for k in own if lang_ok(k) and k != "live_chat"]
        # автоматические «переведённые» (ru-en и т. п.) — хуже исходных: сначала язык самого видео (-orig)
        ks = sorted((k for k in auto if lang_ok(k)), key=lambda k: (not k.endswith("-orig"), len(k)))
        order += [(auto, k, True) for k in ks]
    for src, k, is_auto in order:
        t = best(src.get(k))
        if t:
            return t, k.replace("-orig", ""), is_auto
    return None


def _parse(body, ext):
    """-> [(секунда, текст)] без повторов (автоматические субтитры YouTube повторяют строку в соседних кусках)."""
    out = []
    if ext == "json3":
        try:
            d = json.loads(body)
        except ValueError:
            return out
        for e in d.get("events") or []:
            t = "".join(s.get("utf8", "") for s in e.get("segs") or []).replace("\n", " ").strip()
            if t:
                out.append(((e.get("tStartMs") or 0) / 1000, t))
    else:                                  # vtt / srt / srv3: метки времени и текст без тегов
        cur = 0.0
        for line in body.splitlines():
            m = re.match(r"\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s+-->", line)
            if m:
                cur = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                continue
            t = re.sub(r"<[^>]+>", "", line).strip()
            if not t or t.isdigit() or t.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or "-->" in t:
                continue
            out.append((cur, t))
    clean = []
    for sec, t in out:
        t = re.sub(r"\s+", " ", t)
        if clean and (t == clean[-1][1] or clean[-1][1].endswith(t)):
            continue
        clean.append((sec, t))
    return clean


def transcript(url, timeout=25):
    """-> {title, channel, duration, lang, auto, lines: [(сек, текст)]}. NoTranscript — субтитров нет или нет yt-dlp."""
    try:
        import yt_dlp
    except ImportError:
        raise NoTranscript("на этом компьютере нет yt-dlp — без него субтитры видео не достать")
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "socket_timeout": timeout}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=False) or {}
    meta = {"title": info.get("title") or "", "channel": info.get("channel") or info.get("uploader") or "",
            "duration": info.get("duration") or 0, "description": (info.get("description") or "")[:1500]}
    got = _pick(info)
    if not got:
        raise NoTranscript("у этого видео нет субтитров", meta)
    track, lang, auto = got
    r = httpx.get(track["url"], timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    lines = _parse(r.text, track.get("ext"))
    if not lines:
        raise NoTranscript("субтитры пустые", meta)
    return dict(meta, lang=lang, auto=auto, lines=lines)


def for_model(url, tr):
    """Текст для модели: шапка, весь текст или равномерные отрезки с метками времени, подсказка про конспект."""
    head = f"Видео «{tr['title']}»" + (f" — {tr['channel']}" if tr["channel"] else "") + \
           (f", {_hms(tr['duration'])}" if tr["duration"] else "") + f". Ссылка: {url}\n" + \
           f"Текст ролика — по субтитрам ({tr['lang']}, {'автоматические' if tr['auto'] else 'авторские'}):\n"
    full = " ".join(t for _, t in tr["lines"])
    if len(full) <= TEXT_MAX:
        body = "\n".join(f"[{_hms(s)}] {t}" for s, t in _group(tr["lines"], 600))
        note = ""
    else:                                  # длинное: отрезки по всему ролику, а не одно начало
        groups = _group(tr["lines"], 300)
        step = -(-len(groups) // (TEXT_MAX // 320))       # столько отрывков, сколько влезет, — поровну по всему ролику
        picked = groups[::max(1, step)]
        body, n = [], 0
        for s, t in picked:
            if n + len(t) > TEXT_MAX:
                break
            body.append(f"[{_hms(s)}] {t}")
            n += len(t)
        body = "\n…\n".join(body)
        note = ("\n\n(Ролик длинный — выше отрывки по всему видео с метками времени. Для подробной расшифровки целиком "
                "можно поручить фоновую задачу.)")
    return head + body + note + "\n\nНужен конспект — сделай его по этому тексту; попросят сохранить — сохрани заметкой вместе со ссылкой."


def _group(lines, size):
    """Склеить короткие строки субтитров в абзацы примерно по size символов (метка — начало абзаца)."""
    out, cur, at = [], [], None
    for s, t in lines:
        if at is None:
            at = s
        cur.append(t)
        if sum(len(x) for x in cur) >= size:
            out.append((at, " ".join(cur))); cur, at = [], None
    if cur:
        out.append((at, " ".join(cur)))
    return out


def no_transcript_text(url, e):
    i = getattr(e, "info", {}) or {}
    out = f"Текст этого видео не достать: {e}."
    if i.get("title"):
        out += f"\nВидео «{i['title']}»" + (f" — {i['channel']}" if i.get("channel") else "") + (f", {_hms(i['duration'])}" if i.get("duration") else "") + "."
    if i.get("description"):
        out += "\nОписание автора:\n" + i["description"]
    return out + ("\n\nСкажи честно: конспект можно сделать только по описанию; полный — если поручить фоновую задачу "
                  "(скачать звук и расшифровать). Не выдумывай содержание ролика.")
