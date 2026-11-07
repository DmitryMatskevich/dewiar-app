from pathlib import Path
import argparse
import json
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"core"))
from songs.reference_text import *  # compatibility for offline trials

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--refined', type=Path)
    p.add_argument('--extra-references', type=Path)
    p.add_argument('--morphology', action='store_true')
    args = p.parse_args()
    library, output = args.library.resolve(), args.output.resolve()
    if output == library or library in output.parents:
        p.error('Отчёт должен находиться отдельно от рабочей библиотеки')
    known = None
    if args.morphology:
        from functools import lru_cache
        import pymorphy3
        morph = pymorphy3.MorphAnalyzer()
        known = lru_cache(maxsize=20000)(morph.word_is_known)
    extra = json.loads(args.extra_references.read_text()) if args.extra_references else {}
    paths = sorted(library.glob('*/song.json'))
    before = {str(f): digest(f) for f in paths}
    started = time.monotonic()
    rows = []
    for f in paths:
        song = json.loads(f.read_text())
        phrases = song.get('phrases', [])
        refs = [r for r in song.get('source_candidates', []) if r.get('plain_lyrics')]
        refs += extra.get(f.parent.name, [])
        item = dict(song_id=f.parent.name, title=song.get('title'),
                    phrases=len(phrases), reference_documents=len(refs),
                    boundary=boundary_candidates(phrases, refs),
                    reference=reference_candidates(phrases, refs),
                    lexical=lexical_signals(phrases, known) if known else [])
        if args.refined:
            file = args.refined/f.parent.name/'result.json'
            if file.is_file():
                result = json.loads(file.read_text())
                selected = result.get('selected', {})
                selected = selected.get('phrases', []) if isinstance(selected, dict) else selected
                item['refined'] = dict(phrases=len(selected), boundary=boundary_candidates(selected, refs),
                    reference=reference_candidates(selected, refs),
                    lexical=lexical_signals(selected, known) if known else [])
        rows.append(item)
    after = {str(f): digest(f) for f in paths}
    report = dict(songs=rows, seconds=round(time.monotonic()-started, 3),
                  cards_unchanged=before == after, dictionary_enabled=bool(known),
                  automatic_edits=0, note='Signals and candidates, not measured recognition errors')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(dict(songs=len(rows), seconds=report['seconds'], cards_unchanged=before == after,
                         boundary_candidates=sum(len(r['boundary']['candidates']) for r in rows),
                         reference_candidates=sum(len(r['reference']) for r in rows),
                         lexical_signals=sum(len(r['lexical']) for r in rows), automatic_edits=0)))


if __name__ == '__main__':
    main()
