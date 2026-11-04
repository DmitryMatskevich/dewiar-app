"""Audit retained REAL pilot results; never starts models or changes the library.

Reports evidence and suspicious recognition, not fictional accuracy scores.
Missing recordings/results fail the audit instead of silently skipping a song.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'core'))
from songs.evidence import review_evidence


def audit():
    manifest = json.loads((ROOT/'docs/songs-pilot-manifest.json').read_text())
    references = json.loads((ROOT/'work/song-verification/references/prepared.json').read_text())
    metadata = {json.loads(p.read_text())['source']: p.parent
                for p in (ROOT/'work/song-pilot').glob('*/metadata.json')}
    rows = []
    for song in manifest['songs']:
        paths = song.get('processing')
        if paths:
            folder = ROOT/paths['folder']
            audio = ROOT/paths['audio']
            raw = json.loads((ROOT/paths['baseline']).read_text())
            full = json.loads((ROOT/paths['result']).read_text())
        else:
            folder = metadata[song['source']]
            audio = folder/'audio.wav'
            raw = json.loads((folder/'recognition.json').read_text())
            full = json.loads((ROOT/'work/song-verification'/f'full-{folder.name}.json').read_text())
        with wave.open(str(audio)) as wav:
            duration = round(wav.getnframes()/wav.getframerate()*1000)
        phrases = full['selected']['phrases']
        assert phrases, f'No phrases: {song["request"]}'
        models = [raw['model'], *[h['model'] for h in full['hypotheses']]]
        assert len(set(models)) == 3, f'Missing independent ASR: {song["request"]}'
        invalid = [p.get('phrase_id') for p in phrases
                   if not 0 <= p['start_ms'] < p['end_ms'] <= duration+100]
        evidence = review_evidence({'phrases': phrases, 'source_candidates': references.get(folder.name, [])})
        rows.append({'request':song['request'], 'source':song['source'], 'folder':folder.name,
            'audio_sha256':hashlib.sha256(audio.read_bytes()).hexdigest(),
            'duration_ms':duration, 'models':models, 'phrases':len(phrases),
            'chords':sum(e['name']!='N' for e in full['harmony']['events']),
            'invalid_phrase_times':invalid, 'analysis_errors':full.get('errors',[]),
            'issues':evidence['issues'], 'reference_comparisons':[
                {k:v for k,v in r.items() if k!='differences'} | {'difference_groups':len(r['differences'])}
                for r in evidence['references']], 'source_check':song.get('source_check'),
            'expert_verified':False})
    return {'recordings':len(rows), 'synthetic_recordings':0, 'songs':rows,
            'note':'Различия с публикацией не являются WER; экспертная приёмка не выполнена.'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args(); result=audit()
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    for r in result['songs']:
        print(json.dumps({k:r[k] for k in ('request','phrases','chords','issues','reference_comparisons','invalid_phrase_times')},ensure_ascii=False))
    if any(r['invalid_phrase_times'] or r['analysis_errors'] for r in result['songs']):
        raise SystemExit('Pilot has processing/time failures; see report')
