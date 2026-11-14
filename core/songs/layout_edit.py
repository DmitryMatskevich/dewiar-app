"""The user's layout of the chord sheet: blank lines, labels and line breaks.

Stored on the card as ``sheet_layout`` and never touched by reprocessing:
``breaks`` (a blank line before a line: true/false), ``notes`` (a label over
a line: «Припев», «×2», any short note — not sung, no timing), ``cuts`` (a
sung line starts at this word: true, or is joined to the previous: false).
A place is a sung word: its phrase and character (``sheet_lines.layout_key``)
with the word itself in ``anchors``, so an edit of the phrase text moves the
place with its word instead of putting the blank line elsewhere.
"""
import copy
import re

ACTIONS = {'break': 'breaks', 'note': 'notes', 'cut': 'cuts'}
# Words of the sheet are the text between spaces («Кто», «повeрит?», «–»).
WORD = re.compile(r'\S+')


def _norm(word):
    return re.sub(r'[^\w]+', '', str(word or '').casefold().replace('ё', 'е'))


def _word_span(text, char):
    """The word holding ``char`` (a click may name a letter after a quote)."""
    for match in WORD.finditer(text):
        if match.start() <= char < match.end():
            return match.start(), match.group()
    return None, None


def _word_at(text, char):
    start, word = _word_span(text, char)
    return word if start == char else None


def edit_layout(store, args):
    song_id = str(args.get('song_id') or '')
    if args.get('base_revision') is None:
        raise ValueError('Для сохранения нужна ревизия карточки')
    action = args.get('action')
    if action not in ACTIONS:
        raise ValueError('Неизвестная правка раскладки листа')
    key = str(args.get('key') or '')
    phrase_id, _, char = key.rpartition(':')
    if not phrase_id or not char.isdigit():
        raise ValueError('Не найдена строка листа')
    value = args.get('value')
    if action == 'note' and value is not False:
        # False hides the publication's label; text is the user's own label.
        value = re.sub(r'\s+', ' ', str(value or '')).strip()[:80] or None
    elif value is not None and not isinstance(value, bool):
        raise ValueError('Неверное значение правки')
    with store._lock:
        row = store.get(song_id)
        if not row:
            raise ValueError('Песня не найдена')
        phrase = next((p for p in row.get('phrases') or [] if p.get('phrase_id') == phrase_id), None)
        start, word = _word_span(str((phrase or {}).get('text') or ''), int(char))
        if word is None:
            raise ValueError('Строка уже изменилась — обновите карточку')
        key = f'{phrase_id}:{start}'
        layout = copy.deepcopy(row.get('sheet_layout') or {})
        bucket = layout.setdefault(ACTIONS[action], {})
        if value is None:
            bucket.pop(key, None)
        else:
            bucket[key] = value
        used = {k for name in ACTIONS.values() for k in layout.get(name) or {}}
        layout['anchors'] = {k: v for k, v in (layout.get('anchors') or {}).items() if k in used}
        if key in used:
            layout['anchors'][key] = word
        layout = {k: v for k, v in layout.items() if v}
        return store.patch(song_id, {'sheet_layout': layout}, args['base_revision'])


def resolve_layout(layout, phrases):
    """The stored layout with every place moved to where its word now is.

    A place whose word is gone from the phrase is dropped (not applied
    to whatever word took its character).
    """
    if not isinstance(layout, dict):
        return None
    texts = {p.get('phrase_id'): str(p.get('text') or '') for p in phrases or []}
    anchors = layout.get('anchors') or {}
    moved = {}

    def place(key):
        if key in moved:
            return moved[key]
        phrase_id, _, char = str(key).rpartition(':')
        text, want = texts.get(phrase_id), _norm(anchors.get(key))
        result = None
        if text is not None and char.isdigit():
            char = int(char)
            if not want or _norm(_word_at(text, char)) == want:
                result = key if _word_at(text, char) else None
            else:
                # The phrase was edited: the same word nearest to its old place.
                spots = [m.start() for m in WORD.finditer(text) if _norm(m.group()) == want]
                if spots:
                    result = f'{phrase_id}:{min(spots, key=lambda s: abs(s - char))}'
        moved[key] = result
        return result

    out = {}
    for name in ACTIONS.values():
        values = {}
        for key, value in (layout.get(name) or {}).items():
            spot = place(key)
            if spot is not None:
                values[spot] = value
        out[name] = values
    return out
