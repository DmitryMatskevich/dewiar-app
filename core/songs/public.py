"""Ответы студии без многократной передачи полного архива распознаваний.

Оригиналы и история остаются в SongStore. Список получает только карточки,
редактор — текущий текст и счётчики undo/redo, новый вариант — отдельно.
"""
import copy
from functools import wraps

CARD_KEYS = ('song_id','revision','title','artist','recording_version','source','source_ref',
             'status','stage','stage_label','updated_at','created_at','capabilities','karaoke')


def public_song(row, summary=False):
    if not isinstance(row, dict) or 'revision' not in row or 'song_id' not in row:
        return row
    if summary:
        return {k:copy.deepcopy(row[k]) for k in CARD_KEYS if k in row}
    result = {k:copy.deepcopy(v) for k,v in row.items()
              if k not in {'edits','analysis_runs','phrase_history','words','guitar_sheet'}}
    result['analysis_runs'] = [{'run_id':r['run_id'],'phrases':copy.deepcopy(r.get('phrases',[]))}
                              for r in row.get('analysis_runs',[]) if r['run_id']==row.get('candidate_run_id')]
    result['phrase_history'] = {pid:{'cursor':h['cursor'],'version_count':len(h['versions'])}
        for pid,h in row.get('phrase_history',{}).items() if isinstance(h,dict) and 'versions' in h}
    keys = ('phrase_id','source_phrase_ids','source_spans','text','start_ms','end_ms','cells','chords','unplaced_chords','timing_quality','display_words')
    from .chord_layout import build_guitar_sheet
    from .sheet_lines import readable_sheet
    starts = [row.get('phrases',[])[s['phrase_indexes'][0]].get('phrase_id')
              for s in row.get('sections',[]) if s.get('phrase_indexes')
              and 0 <= s['phrase_indexes'][0] < len(row.get('phrases',[]))]
    events = sorted(copy.deepcopy(row.get('chords',[])), key=lambda c:c.get('start_ms',0))
    duration = max([row.get('audio',{}).get('duration_ms',0) or 0,
                    *[p.get('end_ms',0) or 0 for p in row.get('phrases',[])]])
    for i,event in enumerate(events):
        if event.get('end_ms') is None:
            event['end_ms'] = events[i+1]['start_ms'] if i+1<len(events) else max(duration,event['start_ms']+1000)
    sheet = readable_sheet(build_guitar_sheet(row.get('phrases',[]),events), section_starts=starts)
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
