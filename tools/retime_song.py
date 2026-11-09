"""Re-align saved lyrics without changing text or writing the live song card."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'core'))
from songs.worker import align_phrases, apply_vocal_timing, save
from songs.onset_check import existing_vocals


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--song', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args=parser.parse_args()
    raw=args.song.read_bytes()
    row=json.loads(raw)
    audio=args.song.parent.parent/row['audio']['path']
    selected=next((r for r in row.get('analysis_runs',[]) if
                   r.get('run_id')==row.get('selected_run_id')), {})
    timed={'phrases':copy.deepcopy(row['phrases'])}
    with tempfile.TemporaryDirectory(prefix='dewiar-retime-') as tmp:
        decoded=Path(tmp)/'audio.wav'
        subprocess.run(['ffmpeg','-v','error','-nostdin','-i',str(audio),
                        '-ar','16000','-ac','1',str(decoded)],check=True)
        vocals=existing_vocals(audio)
        align_phrases(decoded,timed,lambda s:print(s,flush=True),
                      selected.get('hypotheses',[]),vocal_path=vocals)
        # Same final step as the batch worker: edges and breaks from the stem.
        result={}
        apply_vocal_timing(timed,vocals,lambda s:print(s,flush=True),result)
        timed['vocal_activity']=result.get('vocal_activity')
    assert {p['phrase_id']:p['text'] for p in timed['phrases']} == {
        p['phrase_id']:p['text'] for p in row['phrases']}, 'Text must not change'
    protected={p['phrase_id']:p for p in row['phrases'] if p.get('review_state')=='verified'
               or any(w.get('verified') or str(w.get('method','')).startswith('manual')
                      for w in p.get('words',[]))}
    timed['phrases']=[copy.deepcopy(protected.get(p['phrase_id'],p)) for p in timed['phrases']]
    save(args.report,dict(song_id=row['song_id'],base_revision=row['revision'],
                         source_sha256=hashlib.sha256(raw).hexdigest(),**timed))


if __name__=='__main__':
    main()
