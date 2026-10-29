"""Граф памяти ассистента — «второй мозг» в духе цеттелькастена.

Узлы: вы (центр), факты о вас, заметки, дни дневника, документы «Знаний», задачи, навыки, а ещё люди, места и темы,
которые встречаются в нескольких записях (так связи появляются сами). Рёбра: [[ссылки]] между заметками,
#теги, общие имена/темы, смысловая близость (общие редкие слова, основы — recall.words), дни дневника цепочкой.
Всё считается из файлов папки ассистента при каждом запросе — ничего не хранится отдельно, граф всегда честный.
Результат build() рисует студия через vis-network; поля узлов и рёбер — договорённость с её кодом.
"""

import datetime as dt
import re
from collections import Counter, defaultdict
from pathlib import Path

LINK = re.compile(r"\[\[([^\]|#]{1,120})(?:[|#][^\]]*)?\]\]")
TAG = re.compile(r"(?<![\w/])#([A-Za-zА-Яа-яЁё][\wЁё-]{1,40})")
# Имена собственные: слово с заглавной не в начале предложения (грубо, но на своих записях работает хорошо)
PROPER = re.compile(r"(?<=[^.!?\n]\s)([А-ЯЁ][а-яё]{2,}(?:\s[А-ЯЁ][а-яё]{2,})?)")
# слова с заглавной, которые не имена: начала фраз, обращения, названия папок и ролей
STOP = {"Это", "Если", "Когда", "Потом", "Также", "Сегодня", "Завтра", "Вчера", "Может", "Нужно", "Надо", "Ещё", "Еще",
        "Пользователь", "Ассистент", "Девиар", "Dewiar", "Агент", "Кодер", "Итог", "План", "Журнал", "Заметка", "Заметки", "Память",
        "Обо", "Мне", "Дневник", "Лаборатория", "Знания", "Задачи", "Навыки", "Вот", "Там", "Здесь", "Так", "Как",
        "Что", "Чтобы", "Для", "При", "Над", "Под", "Все", "Всё", "Спасибо", "Привет", "Хорошо", "Понял", "Готово",
        "Добрый", "День", "Вечер", "Утро", "Можно", "Сделай", "Нет", "Да", "Затем", "Сначала", "Далее", "Главное",
        "Важно", "Кроме", "Около", "После", "Перед", "Через", "Между", "Лучше"}
SKIP_DIRS = {"рабочая копия", ".git", "__pycache__", "node_modules"}
KIND = {"me": "Вы", "fact": "Факт", "note": "Заметка", "diary": "Дневник", "doc": "Документ", "task": "Задача",
        "skill": "Навык", "entity": "Имя / тема", "tag": "Тег", "hub": ""}


def _stem(w):
    """Грубая основа русского имени/слова: «Сергеем», «Сергею», «Сергей» → «серг»; «Ольги», «Ольгу» → «ольг».
    Окончание срезается дважды, но основа не короче 3 букв."""
    w = w.lower().replace("ё", "е")
    end = r"(ами|ями|ого|его|ому|ему|ой|ей|ем|ом|ам|ям|ах|ях|ую|юю|ы|и|а|я|у|ю|е|о|ь|й)$"
    s = re.sub(end, "", w)
    s2 = re.sub(end, "", s)                        # «Сергеем» → «серге» → «серг»; «Сергей» → «серге» → «серг»
    s = s2 if len(s2) >= 3 else s
    return s if len(s) >= 3 else w


def _base_form(counter):
    """Из форм одного слова — начальную: без падежного окончания, если такая есть, иначе самую частую."""
    for w, _ in counter.most_common():
        if re.search(r"[йаяьнртлсвкмд]$", w.lower()) and not re.search(r"(ой|ем|ом|ею|ую|ам)$", w.lower()):
            return w
    return counter.most_common(1)[0][0]


def _title(p: Path, text: str):
    """Заголовок записи: первый «# …» в тексте, иначе имя файла."""
    m = re.search(r"(?m)^#\s+(.+)$", text)
    return (m.group(1).strip() if m else p.stem)[:80]


def _snippet(text, n=220):
    t = re.sub(r"(?m)^#.*$", "", text)
    t = re.sub(r"[*_`>#\[\]]", "", t)
    return re.sub(r"\s+", " ", t).strip()[:n]


def build(ws_root: Path, skills=(), user_name="", knowledge_files=(), limit_notes=400):
    """-> {"nodes": [...], "edges": [...], "stats": {...}, "kinds": KIND} для vis-network. Только читает файлы.
    skills — [{name, title, description}], knowledge_files — пути внутри «Знаний»; limit_notes — на каждую папку."""
    root = Path(ws_root)
    nodes, edges = {}, []
    texts = {}                                   # id узла → текст (для общих имён)
    dates = []                                   # (дата, вид) — рост памяти по дням

    def node(nid, label, kind, **kw):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": label[:48], "kind": kind, "group": kind, **kw}
        return nid

    def edge(a, b, kind="link", w=1.0):
        if a != b and a in nodes and b in nodes:
            edges.append({"from": a, "to": b, "kind": kind, "w": w})

    me = node("me", user_name or "Вы", "me", title="Вы — центр памяти ассистента")

    # хабы разделов — граф не рассыпается на острова
    hubs = {}
    for key, label in (("notes", "Заметки"), ("diary", "Дневник"), ("docs", "Знания"), ("tasks", "Задачи"),
                       ("skills", "Навыки"), ("facts", "Обо мне")):
        hubs[key] = node(f"hub:{key}", label, "hub")
        edge(me, hubs[key], "hub", 0.5)

    # факты о пользователе: строки «- факт _(дд.мм.гггг)_» (формат workspace.remember)
    mem = root / "Память" / "Обо мне.md"
    if mem.is_file():
        for i, line in enumerate(mem.read_text(encoding="utf-8", errors="replace").splitlines()):
            m = re.match(r"^\s*[-*]\s+(.+?)(?:\s+_\((\d\d)\.(\d\d)\.(\d{4})\)_)?\s*$", line)
            if not m:
                continue
            fact = re.sub(r"\s+", " ", m.group(1)).strip()
            fid = node(f"fact:{i}", fact, "fact", title=fact)
            texts[fid] = fact
            edge(hubs["facts"], fid, "hub", 0.6)
            if m.group(4):
                try:
                    dates.append((dt.date(int(m.group(4)), int(m.group(3)), int(m.group(2))), "fact"))
                except ValueError:
                    pass

    # заметки (с подпапками), дневник, задачи
    by_title = {}
    def scan(folder, kind, hub, pattern="*.md"):
        base = root / folder
        if not base.is_dir():
            return []
        out = []
        for p in sorted(base.rglob(pattern))[:limit_notes]:
            if any(part in SKIP_DIRS for part in p.relative_to(base).parts):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = p.relative_to(root).as_posix()
            title = _title(p, text) if kind != "diary" else p.stem
            nid = node(f"{kind}:{rel}", title, kind, path=rel, title=_snippet(text) or title)
            texts[nid] = text
            by_title.setdefault(title.lower(), nid)
            by_title.setdefault(p.stem.lower(), nid)
            edge(hub, nid, "hub", 0.4)
            try:
                dates.append((dt.date.fromtimestamp(p.stat().st_mtime), kind))
            except OSError:
                pass
            out.append((nid, text, p))
        return out

    notes = scan("Заметки", "note", hubs["notes"])
    diary = scan("Дневник", "diary", hubs["diary"])
    for i in range(1, len(diary)):                      # дни дневника — цепочкой по времени
        edge(diary[i - 1][0], diary[i][0], "time", 0.3)
    tasks = []
    tdir = root / "Задачи"
    if tdir.is_dir():
        for d in sorted(x for x in tdir.iterdir() if x.is_dir())[:200]:
            f = next((d / n for n in ("итог.md", "план.md") if (d / n).is_file()), None)   # итог есть — задача готова
            if not f:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            nid = node(f"task:{d.name}", re.sub(r"^\d{4}-\d\d-\d\d\s*", "", d.name) or d.name, "task",
                       path=f.relative_to(root).as_posix(), title=_snippet(text), done=f.name == "итог.md")
            texts[nid] = text
            edge(hubs["tasks"], nid, "hub", 0.4)
            tasks.append((nid, text, f))
            by_title.setdefault(nodes[nid]["label"].lower(), nid)
            try:
                dates.append((dt.date.fromtimestamp(f.stat().st_mtime), "task"))
            except OSError:
                pass

    # документы «Знаний»
    for rel in knowledge_files:
        nid = node(f"doc:{rel}", Path(rel).stem, "doc", path=f"Знания/{rel}", title=rel)
        edge(hubs["docs"], nid, "hub", 0.4)

    # навыки
    for sk in skills:
        nid = node(f"skill:{sk['name']}", sk.get("title") or sk["name"], "skill", title=sk.get("description", ""))
        edge(hubs["skills"], nid, "hub", 0.4)

    # [[ссылки]] и #теги
    for nid, text, _ in notes + diary + tasks:
        for m in LINK.finditer(text):
            tgt = by_title.get(m.group(1).strip().lower())
            if tgt:
                edge(nid, tgt, "wikilink", 2.0)
            else:                                      # ссылка на ещё не написанную заметку — пунктиром
                ghost = node(f"note:?{m.group(1).strip().lower()}", m.group(1).strip(), "note", ghost=True,
                             title="Заметки пока нет — ссылка ждёт")
                edge(nid, ghost, "wikilink", 1.0)
        for m in TAG.finditer(text):
            tg = node(f"tag:{m.group(1).lower()}", "#" + m.group(1), "tag")
            edge(nid, tg, "tag", 1.2)

    # общие имена/места/темы: встречаются в 2+ записях — становятся узлами-связками
    mentions, forms = defaultdict(set), defaultdict(Counter)
    for nid, text in texts.items():
        for m in PROPER.finditer(" " + text):
            w = m.group(1).split()[0]                        # одно слово: «Сергей», «Сергеем» → одна основа
            if w in STOP or len(w) < 3 or re.search(r"(ть|ться|тся|ешь|ите)$", w):
                continue
            key = _stem(w)
            mentions[key].add(nid)
            forms[key][w] += 1
    common = sorted(((k, ids) for k, ids in mentions.items() if len(ids) >= 2), key=lambda x: -len(x[1]))[:40]
    me_key = _stem(user_name.split()[0]) if user_name else ""
    for key, ids in common:
        if key == me_key:                                   # имя самого пользователя — это центр, а не отдельный узел
            for nid in ids:
                edge(nid, me, "mention", 1.0)
            continue
        w = _base_form(forms[key])
        eid = node(f"ent:{key}", w, "entity", title=f"Встречается в записях: {len(ids)}")
        for nid in ids:
            edge(nid, eid, "mention", 1.0)

    # смысловые связи: записи об одном и том же (общие слова, взвешенные по редкости) — как нейроны, которые
    # «зажигаются» вместе. Каждой записи — до трёх самых близких соседей; толщина связи — насколько близки.
    try:
        from recall import words as _words
        import math
        vecs = {nid: Counter(_words(t)) for nid, t in texts.items() if t and nodes.get(nid, {}).get("kind") != "hub"}
        df = Counter(w for v in vecs.values() for w in v)
        n_docs = max(1, len(vecs))
        # слова из большинства записей не связывают ничего — выбрасываем; порог 0.12 ниже отсекает случайную близость
        idf = {w: math.log(1 + n_docs / c) for w, c in df.items() if c < max(3, n_docs * 0.6)}
        tf = {}
        for nid, v in vecs.items():
            vv = {w: (1 + math.log(c)) * idf[w] for w, c in v.items() if w in idf}
            norm = math.sqrt(sum(x * x for x in vv.values())) or 1
            tf[nid] = {w: x / norm for w, x in vv.items()}
        have = {(e["from"], e["to"]) for e in edges} | {(e["to"], e["from"]) for e in edges}
        ids = list(tf)
        best = defaultdict(list)
        for i, a in enumerate(ids):
            va = tf[a]
            for b in ids[i + 1:]:
                vb = tf[b]
                small, big = (va, vb) if len(va) < len(vb) else (vb, va)
                sim = sum(x * big.get(w, 0) for w, x in small.items())
                if sim >= 0.12:
                    best[a].append((sim, b))
                    best[b].append((sim, a))
        added = set()
        for a, lst in best.items():
            for sim, b in sorted(lst, reverse=True)[:3]:
                key = tuple(sorted((a, b)))
                if key in added or (a, b) in have:
                    continue
                added.add(key)
                edge(a, b, "similar", round(min(1.0, sim), 3))
    except Exception:  # noqa: BLE001 — граф строится и без смысловых связей
        pass

    # «вес» узла — сколько у него связей (для размера кружка)
    deg = Counter()
    for e in edges:
        deg[e["from"]] += 1
        deg[e["to"]] += 1
    for n in nodes.values():
        n["degree"] = deg[n["id"]]

    # статистика и рост
    today = dt.date.today()
    week = sum(1 for d, _ in dates if (today - d).days < 7)
    by_day = Counter(d.isoformat() for d, _ in dates if (today - d).days < 60)
    first = min((d for d, _ in dates), default=today)
    real = [n for n in nodes.values() if n["kind"] not in ("hub", "me") and not n.get("ghost")]
    stats = {"facts": sum(1 for n in real if n["kind"] == "fact"), "notes": len(notes), "diary": len(diary),
             "docs": len(knowledge_files), "tasks": len(tasks), "skills": len(list(skills)),
             "entities": len(common), "links": sum(1 for e in edges if e["kind"] != "hub"),
             "nodes": len(real), "week": week, "days": (today - first).days + 1,
             "growth": [{"day": k, "n": v} for k, v in sorted(by_day.items())]}
    return {"nodes": list(nodes.values()), "edges": edges, "stats": stats, "kinds": KIND}
