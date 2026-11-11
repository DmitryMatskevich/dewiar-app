"""Ответы студии без многократной передачи полного архива распознаваний.

Оригиналы и история остаются в SongStore. Список получает только карточки,
редактор — текущий текст и счётчики undo/redo, новый вариант — отдельно.
"""
import copy
from functools import wraps

CARD_KEYS = ('song_id','revision','title','search_query','artist','tags','tag_scores','rating','recording_version','source','source_ref',
             'status','stage','stage_label','updated_at','created_at','capabilities','karaoke','processing')


def public_song(row, summary=False):
    if not isinstance(row, dict) or 'revision' not in row or 'song_id' not in row:
        return row
    if summary:
        return {k:copy.deepcopy(row[k]) for k in CARD_KEYS if k in row}
    result = {k:copy.deepcopy(v) for k,v in row.items()
              if k not in {'edits','analysis_runs','phrase_history','words','guitar_sheet','vocal_activity'}}
    result['analysis_runs'] = [{'run_id':r['run_id'],'phrases':copy.deepcopy(r.get('phrases',[]))}
                              for r in row.get('analysis_runs',[]) if r['run_id']==row.get('candidate_run_id')]
    result['phrase_history'] = {pid:{'cursor':h['cursor'],'version_count':len(h['versions'])}
        for pid,h in row.get('phrase_history',{}).items() if isinstance(h,dict) and 'versions' in h}
    keys = ('phrase_id','source_phrase_ids','source_spans','text','start_ms','end_ms','cells','chords','unplaced_chords','timing_quality','display_words','instrumental','vocal','pause','unsung','stanza_start','verse_continuation','verse_pause')
    from .chord_layout import build_guitar_sheet
    from .sheet_lines import readable_sheet
    from .phrase_timeline import ordered_phrases
    selected = next((r for r in row.get('analysis_runs',[]) if r.get('run_id')==row.get('selected_run_id')), {})
    phrases = ordered_phrases(row.get('phrases',[]),selected.get('hypotheses',[]))
    result['phrases'] = phrases
    # Preserve user labels and references when a legacy recovered suffix moves.
    old_ids=[p.get('phrase_id') for p in row.get('phrases',[])]
    indexes={p.get('phrase_id'):i for i,p in enumerate(phrases)}
    if old_ids != [p.get('phrase_id') for p in phrases]:
        if all(s.get('kind')=='unknown' and s.get('review_state')!='verified' for s in result.get('sections',[])):
            from .structure import infer_sections
            result['sections']=infer_sections(phrases)
        else:
            for section in result.get('sections',[]):
                section['phrase_indexes']=sorted(indexes[old_ids[i]] for i in section.get('phrase_indexes',[])
                                                 if 0<=i<len(old_ids) and old_ids[i] in indexes)
        for section in result.get('sections',[]):
            members=[phrases[i] for i in section['phrase_indexes']]
            if members:
                section.update(start_ms=min(p['start_ms'] for p in members),end_ms=max(p['end_ms'] for p in members))
    # A recognized part (verse, chorus) or one the user confirmed starts a
    # stanza. Automatic «Часть N» splits only mark gaps in the recording and
    # are left to the stanza layout (one-line «parts» otherwise).
    starts = [phrases[s['phrase_indexes'][0]].get('phrase_id')
              for s in result.get('sections',[]) if s.get('phrase_indexes')
              and 0 <= s['phrase_indexes'][0] < len(phrases)
              and (s.get('kind') not in (None, '', 'unknown') or s.get('review_state') == 'verified')]
    events = sorted(copy.deepcopy(row.get('chords',[])), key=lambda c:c.get('start_ms',0))
    duration = max([row.get('audio',{}).get('duration_ms',0) or 0,
                    *[p.get('end_ms',0) or 0 for p in row.get('phrases',[])]])
    for i,event in enumerate(events):
        if event.get('end_ms') is None:
            event['end_ms'] = events[i+1]['start_ms'] if i+1<len(events) else max(duration,event['start_ms']+1000)
    vocal = row.get('vocal_activity')
    if vocal:
        # Singing without text needs an independent recognizer's words there;
        # a lead instrument in the vocal stem is not singing.
        from .vocal_timing import unrecognized_windows
        vocal = {**vocal, 'lexical': unrecognized_windows(phrases, selected.get('hypotheses', []))}
    sheet = readable_sheet(build_guitar_sheet(phrases,events,duration,vocal), section_starts=starts, events=events)
    result['guitar_sheet'] = [{k:copy.deepcopy(line[k]) for k in keys if k in line} for line in sheet]
    from .evidence import review_evidence
    result['review_evidence'] = review_evidence(row)
    return result


def public_song_commands(method):
    @wraps(method)
    def wrapped(self, cmd, args):
        result = method(self, cmd, args)
        if cmd == 'songs_list':
            return [public_song(r, summary=True) for r in result]
        if str(cmd).startswith('song_'):
            return public_song(result)
        return result
    return wrapped
