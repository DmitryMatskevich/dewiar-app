"""План дня и отметки «сделано» — без модели (отзыв 17.10: «Запланируй на сегодня: в 13 позвонить в банк, в 16
тренировка, в 19 купить подарок маме» — Лира ответила «записала», а в «Делах» пусто: модель писала в календарь, а
«запланируй» не узнавала ни одна проверка). Такой план — это дела дня: на каждое — своё напоминание, и потом «я позвонил
в банк» закрывает его и считается в достижениях (done.py).

parse_plan(text, now) -> {"items": [{"text", "at", "moved"}], "day": date, "untimed": n} или None (не план — решает модель).
  Начало — «запланируй / спланируй / план на сегодня / мои дела на завтра / добавь в план…», или «напомни…» с двумя и
  больше делами со своим временем. Дела — через запятую, «;» или «и» перед временем. Дело без времени — на 20:00 того же
  дня (вечером спросить, сделано ли). «В календарь», «встреча» — это календарь: не сюда.
  Прошедшее время сегодня — на завтра (moved), как у обычного напоминания.
parse_done(text) -> {"items": [строки], "explicit": bool} или None.
  explicit — сказано прямо: «отметь…», «готово, …», «…— сделано»: отмечаем, даже если такого напоминания нет.
  Иначе («я позвонил в банк», «купил подарок маме») — только если каждое дело совпало с напоминанием (это решает engine).
То же на телефоне — planQuick/doneQuick в android/assets/www/app.js (меняешь здесь — меняй и там)."""
import re
from datetime import datetime, timedelta

PLAN_RX = re.compile(
    r"^\s*(?:[а-яё]+,\s*)?(?:(?:а|и|ну|пожалуйста|слушай)\s+)*"
    r"(?P<trig>(?:за|с)планируй(?:те)?(?:\s+(?:мне|нам))?(?:\s+(?:дела|задачи))?"
    r"|(?:составь|запиши|сделай|создай)(?:те)?\s+(?:мне\s+)?план(?:\s+(?:дел|дня))?"
    r"|(?:добавь|внеси|поставь|запиши)(?:те)?\s+(?:мне\s+)?в\s+(?:мой\s+)?план(?:\s+(?:дел|дня|на\s+день))?"
    r"|(?:мой\s+|мои\s+)?(?:план|планы|дела|задачи)\s+(?=на\s+(?:сегодня|завтра|послезавтра|день)))"
    r"(?P<rest>.*)$", re.I | re.S)
NOT_PLAN = re.compile(r"календар|встреч|созвон с|совещан|мероприят|отпуск|поездк|путешеств|бюджет|ремонт|свадьб|меню\b", re.I)
REMIND_RX = re.compile(r"^\s*(?:[а-яё]+,\s*)?(?:напомни(?:те)?|поставь(?:те)?\s+(?:мне\s+)?напоминани[яе])\s+(?P<rest>.*)$", re.I | re.S)
TIME_RX = re.compile(r"(?<![а-яё])(?:в|к|на)\s+(?:\d{1,2}(?:[:.]\d{2})?|полдень|половин[ау]\s+\w+)(?:\s*(?:час[а-я]*|ч\b))?"
                     r"(?:\s+(?:утра|дня|вечера|ночи))?|(?<![а-яё])(?:утром|днём|днем|вечером)(?![а-яё])", re.I)
DAY_RX = re.compile(r"(?<![а-яё])(?:на\s+)?(сегодня|завтра|послезавтра|день)(?![а-яё])", re.I)
EVENING = 20


def _segments(body):
    """Дела по одному: через запятую, «;», перенос строки, «и / потом / затем» перед временем."""
    parts = re.split(r"\s*(?:[,;\n]|\.\s+|\s—\s)\s*|\s+(?:и|а\s+потом|потом|затем|а)\s+(?=(?:в|к)\s+\d)", body)
    out = []
    for p in parts:
        p = (p or "").strip(" .:—-")
        if p:
            out += _by_time(p)
    return out


_T = r"(?:в|к)\s+\d{1,2}(?:[:.]\d{2})?(?:\s+(?:часов|часа|час))?(?:\s+(?:утра|дня|вечера|ночи))?"


def _by_time(p):
    """Распознавание речи запятых не ставит: «в 13 позвонить в банк в 16 тренировка в 19 купить подарок маме» — делим по
    времени. Время впереди дела — режем перед каждым «в N»; время после дела («позвонить в банк в 13 тренировка
    в 16») — после каждого «в N»."""
    marks = list(re.finditer(r"(?<![а-яё])" + _T + r"(?![\d:])", p))
    if len(marks) < 2:
        return [p]
    if marks[0].start() == 0:
        cuts = [m.start() for m in marks[1:]]
    else:
        cuts = [m.end() for m in marks[:-1]]
    pieces, last = [], 0
    for c in cuts:
        pieces.append(p[last:c]); last = c
    pieces.append(p[last:])
    return [x.strip(" .:—-,") for x in pieces if x.strip(" .:—-,")]


def parse_plan(text, now=None):
    from when import parse as parse_when
    now = (now or datetime.now()).replace(microsecond=0)
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw or raw.endswith("?") or "\n\nВложения:" in raw:
        return None
    t = raw.lower().replace("ё", "е")
    m = PLAN_RX.match(t)
    remind = False
    if not m:
        m = REMIND_RX.match(t)
        remind = True
        if not m:
            return None
    rest = m.group("rest")
    if NOT_PLAN.search(rest):
        return None
    day_word = ""
    dm = DAY_RX.search(rest[:40])
    if dm:
        day_word = "" if dm.group(1) == "день" else dm.group(1)
        rest = rest[:dm.start()] + " " + rest[dm.end():]
    rest = re.sub(r"^\s*(?:мне|нам|такие|следующие|вот|дела|задачи)\s+", " ", rest)
    rest = rest.strip(" :—-,.")
    segs = _segments(rest)
    if not segs or len(segs) > 12:
        return None
    timed = [s for s in segs if TIME_RX.search(s)]
    if remind and len(timed) < 2:
        return None                       # одно «напомни…» — обычное напоминание (engine_answer, when.parse)
    if not remind and not timed and len(segs) < 2:
        return None                       # «запланируй отпуск» — не план дня
    base = now.date() + timedelta(days={"завтра": 1, "послезавтра": 2}.get(day_word, 0))
    items, untimed = [], 0
    for s in segs:
        if TIME_RX.search(s):
            # «в 3 забрать детей» в плане дня — 15:00, а не 3 ночи (без «утра» и «ночи»; 1–7)
            s = re.sub(r"(?<![а-яё])(в|к)\s+([1-7])(?![\d:.])(?!\s*(?:час[а-я]*\s+)?(?:утра|ночи))", lambda mm: f"{mm.group(1)} {int(mm.group(2)) + 12}", s)
            r = parse_when(f"напомни {day_word} {s}", now) if day_word else parse_when(f"напомни {s}", now)
            moved = False
            if r is None and day_word == "сегодня":          # «сегодня в 13», а уже 22:38 — на завтра, и скажем об этом
                r = parse_when(f"напомни {s}", now)
                moved = r is not None and r["at"].date() > now.date()
            if r is None or r.get("repeat") or r.get("kind") != "reminder" or not r.get("text"):
                return None
            at = r["at"]
            if not day_word and at.date() > now.date():
                moved = True
            from when import _restore_case
            what = _restore_case(r["text"], raw)
            items.append({"text": what[:1].upper() + what[1:], "at": at, "moved": moved})
        else:
            what = re.sub(r"^(?:и|а|еще|ещё|также)\s+", "", s).strip()
            if len(what) < 3 or len(what.split()) > 10:
                return None
            at = datetime.combine(base, datetime.min.time()).replace(hour=EVENING)
            moved = False
            if at <= now:
                at += timedelta(days=1)
                moved = True
            from when import _restore_case
            what = _restore_case(what, raw)
            items.append({"text": what[:1].upper() + what[1:], "at": at, "moved": moved, "untimed": True})
            untimed += 1
    return {"items": items, "day": base, "untimed": untimed}


def _hm(d):
    return f"{d:%H:%M}"


def plan_say(p, now=None):
    """Подтверждение плана одной-двумя фразами (мужской род — engine._g переводит для героинь)."""
    now = now or datetime.now()
    loose = [x for x in p["items"] if x.get("untimed")]
    items = sorted((x for x in p["items"] if not x.get("untimed")), key=lambda x: x["at"])
    days = {x["at"].date() for x in items + loose}
    low = lambda x: x["text"][:1].lower() + x["text"][1:] if not x["text"][1:2].isupper() else x["text"]  # noqa: E731

    def dname(d):
        dd = (d - now.date()).days
        return {0: "сегодня", 1: "завтра", 2: "послезавтра"}.get(dd, f"{d:%d.%m}")
    lst = "; ".join([f"{_hm(x['at'])} — {low(x)}" for x in items] + ([", ".join(low(x) for x in loose)] if loose else []))
    if len(days) == 1:
        d = next(iter(days))
        head = f"Записал план на {dname(d)}" if len(items) + len(loose) > 1 else f"Записал в план на {dname(d)}"
        if any(x["moved"] for x in items + loose) and p["day"] == now.date():
            head = f"Сегодня это время уже прошло — записал план на {dname(d)}"
        out = f"{head}: {lst}."
    else:
        by = {}
        for x in items + loose:
            by.setdefault(x["at"].date(), []).append(x)
        out = "Записал план: " + ". ".join(
            f"{dname(d)[:1].upper() + dname(d)[1:]} — " + "; ".join(f"{_hm(x['at'])} {x['text'][:1].lower() + x['text'][1:]}" for x in v)
            for d, v in sorted(by.items())) + "."
        if any(x["moved"] for x in items + loose):
            out += " Что уже прошло сегодня — перенёс на завтра."
    if p.get("untimed"):
        out += f" {'Дела' if len(loose) > 1 else 'Дело'} без времени — напомню в {EVENING}:00."
    return out + " Сделаете — скажите, и я отмечу."


# ---------- «сделано» ----------
_PAST = r"[а-яё]+(?:л|ла|ли|лся|лась|лись)"
DONE_EXPLICIT = [
    re.compile(r"^(?:отметь(?:те)?|отмечай|поставь(?:те)?\s+(?:галочку|отметку))(?:\s+(?:мне|пожалуйста))?(?:\s*,?\s*что\s+)?"
               r"(?:\s+(?:как|что)?\s*(?:сделанн[а-я]*|выполненн[а-я]*|исполненн[а-я]*|готов[а-я]*|сделано|выполнено))?"
               r"\s*[:,—-]?\s*(?:что\s+)?(?P<x>.+)$"),
    re.compile(r"^(?:готово|сделано|выполнено|галочка)\s*[,:—-]?\s+(?P<x>.+)$"),
    re.compile(r"^(?P<x>.+?)\s*[—:-]?\s+(?:готово|сделано|выполнено)$"),
]
DONE_IMPLICIT = re.compile(r"^(?:(?:ну|так|всё|все|итак|кстати)\s*,?\s+)?(?:я\s+)?(?:уже\s+|только\s+что\s+|сегодня\s+)?(?P<x>" + _PAST + r"(?:\s.+)?)$")
DONE_NOT = re.compile(r"(?<![а-яё])(?:не|вчера|завтра|хочу|хотел[аи]?|надо|нужно|буду|будем|собираюсь|планирую|может|если|"
                      r"забыл[аи]?|напомни|когда|зачем|почему|сколько|отмени|сними|сними|убери)(?![а-яё])")


def parse_done(text):
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    t = raw.lower().replace("ё", "е").rstrip(" .!")
    t = re.sub(r"^[а-я]+,\s*(?=\S)", lambda m: "" if m.group(0)[:-2].strip(", ") in _NAMES else m.group(0), t)
    if not t or t.endswith("?") or len(t) > 160 or "\n" in t:
        return None
    if re.match(r"^(?:отметь|отмечай|поставь)", t) and re.search(r"(?<![а-яё])(?:не|ошибк|сним)|календар|карт[еуа]|заметк|будильник|напоминани", t):
        return None                                   # «отметь как не сделанное», «сними отметку» — модели (undo)
    explicit, x = False, None
    for rx in DONE_EXPLICIT:
        m = rx.match(t)
        if m and m.group("x").strip():
            explicit, x = True, m.group("x")
            break
    if x is None:
        if DONE_NOT.search(t) or len(t.split()) > 12:
            return None
        m = DONE_IMPLICIT.match(t)
        if not m:
            return None
        x = m.group("x")
    elif re.search(r"(?<![а-яё])(?:не|завтра|буду|надо|нужно)(?![а-яё])", x):
        return None
    parts = re.split(r"\s*[,;]\s*|\s+(?:и|а\s+ещ[её]|а\s+также|потом|ещ[её])\s+(?=(?:я\s+)?" + _PAST + r"(?![а-яё]))", x)
    items = []
    for p in parts:
        p = re.sub(r"^(?:я\s+|и\s+|уже\s+|еще\s+|ещ[её]\s+|также\s+)+", "", p.strip(" .:—-"))
        p = re.sub(r"\s+(?:уже|сегодня|только что|готово|сделано)$", "", p).strip(" .:—-")
        if len(p) < 3:
            continue
        items.append(_restore(p, raw))
    if not items:
        return None
    return {"items": items, "explicit": explicit}


_NAMES = {"лира", "эва", "патрик", "ассистент", "dewiar", "девиар"}


def _restore(p, raw):
    from when import _restore_case
    p = _restore_case(p, raw)
    return p[:1].upper() + p[1:]


def done_say(r, closed=()):
    """После отметки — коротко и тепло: что отмечено, сколько сегодня, рубеж или новый значок (мужской род)."""
    import done as D
    s = r["stats"]
    names = ", ".join(f"«{x['title']}»" for x in r["items"])
    n = s["today"]
    out = f"Отметил {names}."
    if r["fresh"]:
        a = r["fresh"][-1]
        out += f" Сегодня уже {n} {D.dela(n)} — новое достижение «{a[4]}»!"
    elif r["mile"]:
        out += f" Уже {n} {D.dela(n)} за сегодня — так держать!"
    else:
        out += f" Сегодня {'это первое дело' if n == 1 else f'уже {n} {D.dela(n)}'}."
    if s["streak"] >= 2 and (r["fresh"] or r["mile"] or n == 1):
        out += f" Серия — {s['streak']} {D.dney(s['streak'])} подряд."
    return out
