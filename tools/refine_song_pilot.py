"""Resume voting/alignment on retained pilot hypotheses without rerunning ASR."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--steps',default='consensus,align')
    parser.add_argument('--folder');args=parser.parse_args()
    reports=[]
    for folder in sorted((ROOT/'work/song-pilot').iterdir()):
        if args.folder and folder.name!=args.folder:continue
        result=ROOT/'work/song-verification'/('full-'+folder.name+'.json')
        if not result.is_file():continue
        raw=json.loads((folder/'recognition.json').read_text())
        baseline=folder/'gigaam-hypothesis.json'
        baseline.write_text(json.dumps({'model':raw['model'],'input':'mixture','phrases':[
            {'phrase_id':folder.name+'-giga-'+str(i),'text':s['text'],
             'start_ms':round(s['start']*1000),'end_ms':round(s['end']*1000),
             'words':[],'timing_quality':'phrase'} for i,s in enumerate(raw['segments'])]},ensure_ascii=False))
        command=[sys.executable,str(ROOT/'core/songs/worker.py'),'--input',str(folder/'audio.wav'),
            '--output',str(result),'--run-id',folder.name,'--baseline',str(baseline),'--resume','--steps',args.steps]
        completed=subprocess.run(command,capture_output=True,text=True)
        result.with_suffix('.refine.log').write_text(completed.stdout+completed.stderr)
        data=json.loads(result.read_text());selected=data.get('selected') or {}
        phrases=selected.get('phrases',[])
        report={'folder':folder.name,'returncode':completed.returncode,'alignment':selected.get('alignment'),
            'phrases':len(phrases),'changed_by_vote':sum(bool(p.get('consensus_changed')) for p in phrases),
            'words':sum(len(p.get('words',[])) for p in phrases),'errors':data.get('errors',[])}
        reports.append(report);print(json.dumps(report,ensure_ascii=False),flush=True)
        (ROOT/'work/song-verification/refinement-results.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n')
