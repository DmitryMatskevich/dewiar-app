"""Read-only layout audit of every saved song; no models, network or data writes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
from platform_info import data_paths
from songs.chord_layout import build_guitar_sheet, clean_chord
from songs.public import public_song
from songs.sheet_lines import readable_sheet


def audit(path):
    before = path.read_bytes()
    row = json.loads(before)
    shown = public_song(row)['guitar_sheet']
    starts = [row['phrases'][s['phrase_indexes'][0]].get('phrase_id')
              for s in row.get('sections', []) if s.get('phrase_indexes')
              and 0 <= s['phrase_indexes'][0] < len(row['phrases'])]
    detailed = readable_sheet(build_guitar_sheet(row.get('phrases', []), row.get('chords', [])), section_starts=starts)
    expected = Counter((clean_chord(c.get('name')), c.get('start_ms', 0))
                       for c in row.get('chords', []) if clean_chord(c.get('name')))
    actual = Counter((c['name'], c.get('start_ms', 0)) for line in detailed
                     for c in line.get('chords', []) + line.get('unplaced_chords', []))
    original_words = ' '.join(p['text'] for p in row.get('phrases', [])).split()
    shown_words = ' '.join(p['text'] for p in shown).split()
    checks = {
        'all_words_in_order': original_words == shown_words,
        'all_chord_events_preserved': expected == actual,
        'no_added_continuations': not any(c.get('continued') for l in detailed for c in l.get('chords', [])),
        'cells_reconstruct_text': all(''.join(c['text'] for c in l['cells']) == l['text'] for l in shown),
        'anchors_in_bounds': all(0 <= c['char_index'] < len(l['text']) for l in shown for c in l['cells'] if c['chords']),
        'no_unplaced_chords_under_lyrics': all(not l['unplaced_chords'] for l in shown if l['text']),
        'source_unchanged': path.read_bytes() == before,
    }
    return {'song_id': row['song_id'], 'title': row['title'], 'artist': row.get('artist', ''),
            'lyric_lines': sum(bool(l['text']) for l in shown), 'words': len(shown_words),
            'chord_events': sum(actual.values()), 'checks': checks,
            'long_source_tokens': sum(len(w) > 64 for w in original_words),
            'recognition_warnings': len(public_song(row)['review_evidence']['issues'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    config, state = data_paths()
    settings = config / 'settings.json'
    before = hashlib.sha256(settings.read_bytes()).hexdigest()
    rows = [audit(p) for p in sorted((state / 'songs').glob('*/song.json'))]
    result = {'songs': rows, 'settings_unchanged': before == hashlib.sha256(settings.read_bytes()).hexdigest()}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    for row in rows:
        print(row['title'], row['artist'], 'OK' if all(row['checks'].values()) else row['checks'])
    raise SystemExit(0 if rows and result['settings_unchanged'] and all(all(r['checks'].values()) for r in rows) else 1)


if __name__ == '__main__':
    main()
