"""Manual musical markup with revision checks and retained source hypotheses."""
import copy
import math
import uuid
from .chord_layout import build_guitar_sheet, clean_chord
from .structure import infer_sections

LABELS = {'verse': 'Куплет', 'chorus': 'Припев', 'prechorus': 'Предприпев',
          'bridge': 'Бридж', 'intro': 'Вступление', 'instrumental': 'Проигрыш',
          'outro': 'Окончание', 'unknown': 'Часть'}


def edit_structure(store, args):
    if args.get('base_revision') is None:
        raise ValueError('Для правки нужна ревизия карточки')
    with store._lock:
        row = store.get(str(args.get('song_id') or ''))
        if not row:
            raise ValueError('Песня не найдена')
        phrases = copy.deepcopy(row.get('phrases') or [])
        index = next((i for i,p in enumerate(phrases) if p['phrase_id'] == args.get('phrase_id')), None)
        if index is None:
            raise ValueError('Фраза не найдена')
        phrase = phrases[index]
        action = args.get('action')
        changed_ids = set()
        if action == 'split':
            char = args.get('char_index')
            if not isinstance(char, int) or isinstance(char, bool) or not 0 < char < len(phrase['text']):
                raise ValueError('Поставьте курсор между словами в тексте фразы')
            left, right = phrase['text'][:char].rstrip(), phrase['text'][char:].lstrip()
            if not left or not right or (not phrase['text'][char-1].isspace() and not phrase['text'][char].isspace()):
                raise ValueError('Граница должна проходить между словами')
            # No proportional timing fallback: measured word boundary or an
            # explicit point selected by the user while listening.
            word = next((w for w in phrase.get('words', []) if w.get('char_start', -1) >= char), None)
            boundary = args.get('time_ms')
            if boundary is None and word:
                boundary = word['start_ms']
            if not isinstance(boundary, (int, float)) or not math.isfinite(boundary) or not phrase['start_ms'] < boundary < phrase['end_ms']:
                raise ValueError('Прослушайте фразу и укажите время разделения внутри неё')
            a = {**phrase, 'text': left, 'end_ms': round(boundary), 'words': [],
                 'agreement': [], 'alignment_stale': True, 'timing_quality': 'manual_phrase'}
            b = {**phrase, 'phrase_id': str(uuid.uuid4()), 'text': right, 'start_ms': round(boundary),
                 'words': [], 'agreement': [], 'alignment_stale': True, 'timing_quality': 'manual_phrase'}
            b.pop('section_start', None)
            phrases[index:index+1] = [a,b]
            changed_ids.add(phrase['phrase_id'])
        elif action == 'merge':
            if index+1 >= len(phrases):
                raise ValueError('Это последняя фраза')
            after = phrases[index+1]
            changed_ids.update((phrase['phrase_id'], after['phrase_id']))
            phrases[index:index+2] = [{**phrase, 'text': phrase['text']+' '+after['text'],
                'end_ms': after['end_ms'], 'words': [], 'agreement': [], 'alignment_stale': True,
                'timing_quality': 'manual_phrase', 'review_state': 'needs_review'}]
        elif action == 'section':
            kind = args.get('kind')
            if kind not in LABELS and kind != 'continue':
                raise ValueError('Неизвестная часть песни')
            if kind == 'continue':
                phrase.pop('section_start', None)
            else:
                phrase['section_start'] = kind
        else:
            raise ValueError('Неизвестная правка структуры')
        sections = []
        if any(p.get('section_start') for p in phrases):
            for i,p in enumerate(phrases):
                if not sections or p.get('section_start'):
                    kind = p.get('section_start', 'unknown')
                    sections.append({'kind': kind, 'label': LABELS[kind], 'evidence': 'Ручная разметка',
                                     'phrase_indexes': [], 'start_ms': p['start_ms']})
                sections[-1]['phrase_indexes'].append(i)
                sections[-1]['end_ms'] = p['end_ms']
        else:
            sections = infer_sections(phrases)
        # Retain full previous selection as an undoable source revision.
        history = row.get('phrase_history', {})
        history.setdefault('_structure', []).append({'phrases': row.get('phrases'), 'sections': row.get('sections'),
            'phrase_history': {pid:history.get(pid) for pid in changed_ids}})
        chords = row.get('chords', [])
        for pid in changed_ids:
            history.pop(pid, None)
        for chord in chords:
            if (chord.get('anchor') or {}).get('phrase_id') in changed_ids:
                chord['previous_anchor'] = chord.pop('anchor')
                chord['verified'] = False
        return store.patch(row['song_id'], {'phrases': phrases, 'sections': sections,
            'status':'review' if row.get('status')=='ready' else row.get('status','review'),
            'phrase_history': history, 'words': [], 'chords':chords, 'guitar_sheet': build_guitar_sheet(phrases,chords),
            'review_state': 'needs_review', 'stage_label': 'Ручная разметка сохранена'}, args['base_revision'])


def preview_chords(store, args):
    """Read-only word positions, through the same layout as the saved sheet."""
    from .public import public_song
    row = store.get(str(args.get('song_id') or ''))
    if not row:
        raise ValueError('Песня не найдена')
    events = args.get('chords')
    if not isinstance(events, list) or len(events) > 2000:
        raise ValueError('Некорректный список аккордов')
    row = copy.deepcopy(row)
    row['chords'] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError('Аккорд должен быть объектом')
        start = event.get('start_ms')
        if not isinstance(start, (int, float)) or not math.isfinite(start) or start < 0:
            continue
        row['chords'].append({**event, '_preview_index': index})
    # Text drafts have no measured word timing until they are aligned again.
    for phrase in row.get('phrases', []):
        if phrase['phrase_id'] == args.get('phrase_id') and isinstance(args.get('text'), str) and phrase['text'] != args['text']:
            phrase.update(text=args['text'][:20000], words=[], alignment_stale=True)
            for chord in row['chords']:
                if (chord.get('anchor') or {}).get('phrase_id') == phrase['phrase_id']:
                    chord.pop('anchor', None)
    positions = {}
    for line in public_song(row)['guitar_sheet']:
        for mark in line.get('chords', []):
            index = mark.get('_preview_index')
            if index is None or mark.get('continued'):
                continue
            at = mark.get('char_index')
            span = next((s for s in line.get('source_spans', [])
                         if s['display_char_start'] <= at < s['display_char_end']), None)
            if span:
                positions[index] = {'phrase_id': span['phrase_id'],
                    'char_index': span['phrase_char_start'] + at - span['display_char_start'],
                    'quality': mark.get('anchor_quality', 'line')}
    return {'positions': positions}


def edit_chords(store, args):
    if args.get('base_revision') is None:
        raise ValueError('Для сохранения аккордов нужна ревизия карточки')
    with store._lock:
        row = store.get(str(args.get('song_id') or ''))
        if not row:
            raise ValueError('Песня не найдена')
        events = args.get('chords')
        if not isinstance(events,list) or len(events) > 2000:
            raise ValueError('Некорректный список аккордов')
        cleaned = []
        for event in events:
            if not isinstance(event, dict):
                raise ValueError('Аккорд должен быть объектом')
            # N is the recognizer's no-chord marker; preserve these intervals
            # when a user edits another event in the same song.
            name = 'N' if event.get('name') == 'N' else clean_chord(event.get('name'))
            start = event.get('start_ms')
            if not name:
                raise ValueError('Неизвестное название аккорда: ' + str(event.get('name') or ''))
            if not isinstance(start,(int,float)) or not math.isfinite(start) or start < 0:
                raise ValueError('Некорректная сохранённая позиция аккорда')
            anchor = event.get('anchor')
            if anchor:
                phrase = next((p for p in row.get('phrases',[]) if p['phrase_id']==anchor.get('phrase_id')), None)
                at = anchor.get('grapheme_index')
                if not phrase or not isinstance(at,int) or not 0 <= at < len(phrase['text']) or not phrase['start_ms'] <= start < phrase['end_ms']:
                    raise ValueError('Привязка аккорда должна попадать в выбранную фразу')
                anchor = {**anchor, 'kind':'manual'}
            cleaned.append({**event, 'name':name, 'start_ms':round(start), 'anchor':anchor, 'verified':event.get('verified') is True})
        histories = row.get('phrase_history', {})
        histories.setdefault('_chords', []).append(row.get('chords',[]))
        return store.patch(row['song_id'], {'chords':cleaned, 'guitar_sheet':build_guitar_sheet(row.get('phrases',[]),cleaned),
            'status':'review' if row.get('status')=='ready' else row.get('status','review'),'review_state':'needs_review',
            'phrase_history':histories, 'capabilities':{**row.get('capabilities',{}),'chords':bool(cleaned)},
            'stage':'review','stage_label':'Ручные аккорды сохранены'},args['base_revision'])
