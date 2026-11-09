"""Undo line times that a step rerun moved onto the wrong lines.

Before 08.11.2026 a rerun of «Распознавание слов» on a card with manual
edits copied the new start/end/word times onto the card BY LIST POSITION.
When the rerun had a different number of lines, every later line received
its neighbour's time («Гоп со смыком»: «Славился своим огромным приком»
moved from 6–11 s to 17.6–33.7 s, and its place in the sheet became an
empty passage). The merge is fixed in core/songs (merge by id/text); this
tool repairs cards damaged earlier.

For each line whose time jumped by more than 3 s in such a rerun, the time
before the rerun is restored only if the independent recognizers heard this
line's words there clearly better than at the new place. Text, manual
boundaries and edits are kept. Default is a report; ``--apply`` writes with
a backup and a revision check.

  .songs-venv/bin/python tools/songs_restore_timing.py [--apply] [--song ID]
"""
import argparse
import copy
import json
import re
import sys
import uuid
from difflib import SequenceMatcher
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'core'))
from songs.store import SongStore  # noqa: E402

ROOT = Path.home() / 'Library/Application Support/Dewiar/state/songs'
MARK = 'Распознавание слов обновлено'
FIELDS = ('start_ms', 'end_ms', 'timing_quality', 'alignment_stale', 'alignment_warning')


def tokens(text):
    return [t for t in (re.sub(r'[^\w]+', '', w.casefold().replace('ё', 'е')) for w in str(text or '').split()) if t]


def support(text, a, b, hypotheses):
    """Share of the line's words a timed recognizer heard inside [a, b] (±1 s)."""
    want = tokens(text)
    if not want:
        return 0.0
    best = 0.0
    for hypothesis in hypotheses:
        # Coarse windows (25-second chunks, 17-second GigaAM blocks) contain
        # the neighbours too and cannot tell one line's place from another's.
        rows = [p for p in hypothesis.get('phrases') or [] if p.get('timing_quality') != 'chunk'
                and int(p.get('end_ms') or 0) - int(p.get('start_ms') or 0) <= 12000
                and int(p.get('start_ms') or 0) < b + 1000 and int(p.get('end_ms') or 0) > a - 1000]
        heard = [t for p in rows for t in tokens(p.get('text'))]
        if not heard:
            continue
        matched = sum(x.size for x in SequenceMatcher(None, want, heard, autojunk=False).get_matching_blocks())
        best = max(best, matched / len(want))
    return best


def damaged_reruns(row):
    """(phrases before, phrases after) for each position-merged rerun."""
    previous, out = None, []
    for edit in row.get('edits') or []:
        changes = edit.get('changes') or {}
        phrases = changes.get('phrases')
        if not isinstance(phrases, list):
            continue
        if previous is not None and MARK in str(changes.get('stage_label') or ''):
            out.append((previous, phrases, edit.get('at')))
        previous = phrases
    return out


def restore(row):
    selected = next((r for r in row.get('analysis_runs') or [] if r.get('run_id') == row.get('selected_run_id')), {})
    hypotheses = selected.get('hypotheses') or []
    phrases = copy.deepcopy(row.get('phrases') or [])
    by_id = {p.get('phrase_id'): p for p in phrases}
    changes = []
    for before, after, at in damaged_reruns(row):
        old = {p.get('phrase_id'): p for p in before}
        for moved in after:
            pid = moved.get('phrase_id')
            prior, current = old.get(pid), by_id.get(pid)
            if not prior or not current or current.get('timing_quality') == 'manual_phrase':
                continue
            if abs(int(prior['start_ms']) - int(moved['start_ms'])) <= 3000 and \
                    abs(int(prior['end_ms']) - int(moved['end_ms'])) <= 3000:
                continue
            # Only undo the rerun's move; a later deliberate change stays.
            if (int(current['start_ms']), int(current['end_ms'])) != (int(moved['start_ms']), int(moved['end_ms'])) and \
                    abs(int(current['start_ms']) - int(moved['start_ms'])) > 3000:
                continue
            here = support(current['text'], int(current['start_ms']), int(current['end_ms']), hypotheses)
            there = support(current['text'], int(prior['start_ms']), int(prior['end_ms']), hypotheses)
            if there < .5 or there < here + .2:
                continue
            for name in FIELDS:
                if name in prior:
                    current[name] = copy.deepcopy(prior[name])
                else:
                    current.pop(name, None)
            same_text = tokens(prior.get('text')) == tokens(current.get('text'))
            current['words'] = copy.deepcopy(prior.get('words') or []) if prior.get('text') == current.get('text') else []
            if not current['words']:
                current.update(timing_quality='phrase', alignment_stale=True)
            for name in ('measured_start_ms', 'measured_end_ms', 'timing_fit'):
                current.pop(name, None)
            changes.append({'phrase_id': pid, 'text': current['text'][:60], 'rerun_at': at,
                            'wrong': [int(moved['start_ms']), int(moved['end_ms'])],
                            'restored': [int(current['start_ms']), int(current['end_ms'])],
                            'support_wrong': round(here, 2), 'support_restored': round(there, 2),
                            'same_text': same_text})
    return phrases, changes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--song', action='append', default=[])
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    store = SongStore(args.root)
    ids = args.song or [p.parent.name for p in sorted(args.root.glob('*/song.json'))]
    report = []
    for sid in ids:
        row = store.get(sid)
        if not row:
            continue
        phrases, changes = restore(row)
        if not changes:
            continue
        print(f"• {row.get('title')}: строк с возвращённым временем {len(changes)}")
        for c in changes:
            print(f"    {c['text']}: {c['wrong'][0]/1000:.1f}–{c['wrong'][1]/1000:.1f} с → "
                  f"{c['restored'][0]/1000:.1f}–{c['restored'][1]/1000:.1f} с "
                  f"(слова слышны: {c['support_wrong']:.0%} → {c['support_restored']:.0%})")
        item = {'song_id': sid, 'title': row.get('title'), 'changes': changes}
        report.append(item)
        if not args.apply:
            continue
        if row.get('status') in {'running', 'recording'} or row.get('active_run_id'):
            print('    ! пропущена: идёт обработка')
            continue
        backup = args.root / sid / 'audio' / 'analysis' / str(uuid.uuid4())
        backup.mkdir(parents=True, exist_ok=False)
        (backup / 'before-restore-timing.json').write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            store.patch(sid, {'phrases': phrases, 'words': [w for p in phrases for w in p.get('words', [])]},
                        row['revision'])
            item['applied'] = True
        except ValueError as exc:
            print(f'    ! не записано — {exc}')
    out = APP / 'work' / 'restore-timing-report.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(('Записано' if args.apply else 'Проверка без записи') + f'; отчёт: {out}')


if __name__ == '__main__':
    main()
