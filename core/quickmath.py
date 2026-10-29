"""Мгновенный счёт без модели: «сколько будет 17% от 2350», «сколько будет 125 * 4», «сколько дней до Нового года».

parse(text, today) → {"expr", "say"} или None. Только когда вся фраза — счёт и числа цифрами (так пишет человек и
большинство распознавателей); словами, со словами-действиями («…и поставь таймер») — None: пусть решает модель
(у неё есть калькулятор calc, tools.calc_eval). Сам счёт — tools.calc_eval, тот же, что у модели.
"""
import datetime as dt
import re

NUM = r"\d[\d\s]*(?:[.,]\d+)?"
MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8, "сентябр": 9,
          "октябр": 10, "ноябр": 11, "декабр": 12}
OPS = [(r"\s*(?:плюс|\+)\s*", " + "), (r"\s*(?:умножить|умноженное|помножить|умножь)\s+на\s*|\s*[x×*]\s*", " * "),
       (r"\s*(?:разделить|делить|поделить|деленное|поделенное)\s+на\s*|\s*[/÷:]\s*", " / "), (r"\s*(?:минус|−)\s*|\s+-\s+", " - ")]
LEAD = r"^(?:(?:а|ну|и|слушай|скажи)\s+)*(?:[а-я]+,\s*)?"


def _num(s):
    return re.sub(r"\s", "", s).replace(",", ".")


def _fmt(x):
    if isinstance(x, float):
        x = round(x, 4)
        return (f"{x:,.4f}".rstrip("0").rstrip(".")).replace(",", " ").replace(".", ",")
    return f"{x:,}".replace(",", " ")


def _plural(n, forms):
    n = abs(int(n)) % 100
    return forms[2] if 11 <= n <= 14 else forms[0] if n % 10 == 1 else forms[1] if 2 <= n % 10 <= 4 else forms[2]


def parse(text, today=None):
    from tools import ToolError, calc_eval
    today = today or dt.date.today()
    t = re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip(" ?!.")
    t = re.sub(LEAD, "", t)
    if not re.search(r"\bсот|\bсто\b|тысяч|миллион|двест|трист|четырест|\w+сот\b", t):
        import when
        t = when._num_words(t)                    # «два плюс два» — так пишет распознавание речи; большие числа словами — модели
    m = re.match(r"^(?:сколько\s+(?:будет\s+|это\s+)?|посчитай\s+|вычисли\s+)?(" + NUM + r")\s*(?:%|процент\w*)\s+от\s+(" + NUM + r")$", t)
    if m:
        p, base = _num(m.group(1)), _num(m.group(2))
        try:
            r = calc_eval(f"{base} * {p} / 100")
        except ToolError:
            return None
        pb = _fmt(float(base)) if '.' in base else _fmt(int(base))
        return {"expr": f"{base} * {p} / 100", "how": f"{pb} × {_fmt(float(p)) if '.' in p else p} / 100 = {_fmt(r)}, калькулятором",
                "say": f"{_fmt(float(p)) if '.' in p else p}% от {pb} — это {_fmt(r)}."}
    m = re.match(r"^сколько\s+(?:сейчас\s+)?(?:осталось\s+)?дн(?:ей|я)\s+(?:осталось\s+)?до\s+(.+)$", t)
    if m:
        w = m.group(1).strip()
        d = None
        if re.fullmatch(r"(?:нового|новог)\s+года", w):
            d, name = dt.date(today.year + 1, 1, 1), "Нового года"
        else:
            mm = re.fullmatch(r"(\d{1,2})\s+([а-я]+)", w)
            if mm:
                k = next((k for k in sorted(MONTHS, key=len, reverse=True) if mm.group(2).startswith(k)), None)
                if k:
                    try:
                        d = dt.date(today.year, MONTHS[k], int(mm.group(1)))
                    except ValueError:
                        return None
                    name = w
            mm = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?", w)
            if mm:
                y = int(mm.group(3)) if mm.group(3) else today.year
                try:
                    d = dt.date(y + 2000 if y < 100 else y, int(mm.group(2)), int(mm.group(1)))
                except ValueError:
                    return None
                name = d.strftime("%d.%m.%Y")
            if d and d < today and not (mm and mm.group(3)):
                d = d.replace(year=d.year + 1)
        if not d:
            return None
        n = (d - today).days
        if n < 0:
            return None
        if n == 0:
            return {"expr": f'days_until("{d}")', "say": f"{name[:1].upper() + name[1:]} — сегодня."}
        return {"expr": f'days_until("{d}")', "how": f"от {today:%d.%m.%Y} до {d:%d.%m.%Y} — {n} дн., по календарю",
                "say": f"До {name} {_plural(n, ('остался', 'осталось', 'осталось'))} {n} {_plural(n, ('день', 'дня', 'дней'))}."}
    m = re.match(r"^(?:сколько\s+будет|посчитай|вычисли|сколько)\s+(.+)$", t)
    if m:
        e = m.group(1)
        for rx, sym in OPS:
            e = re.sub(rx, sym, e)
        e = re.sub(r"\s+", " ", e).strip()
        if not re.fullmatch(r"[\d\s.,()+\-*/]+", e) or not re.search(r"\d\s*[+\-*/]\s*[\d(]", e):
            return None
        try:
            r = calc_eval(e)
        except ToolError:
            return None
        said = e
        for sym, word in ((" + ", " плюс "), (" - ", " минус "), (" * ", " умножить на "), (" / ", " разделить на ")):
            said = said.replace(sym, word)
        return {"expr": e, "how": f"{e.replace('*', '×')} = {_fmt(r)}, калькулятором",
                "say": f"{said[:1].upper() + said[1:]} — будет {_fmt(r)}."}   # словами: так и озвучка прочтёт верно
    return None
