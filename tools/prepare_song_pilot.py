"""Prepare requested pilot cards from retained audio and actual ASR results.

Default target is a development artifact, not the user's library. Publishing
copies only new cards and never overwrites an existing recording or edits.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from songs.store import SongStore


def prepare():
    target=SongStore(ROOT/'work/song-verification/ready-library')
    manifest=json.loads((ROOT/'docs/songs-pilot-manifest.json').read_text())
    sources={row['source']:row for row in manifest['songs'] if row.get('source')}
    report=[]
    for folder in sorted((ROOT/'work/song-pilot').iterdir()):
        if not (folder/'metadata.json').exists(): continue
        meta=json.loads((folder/'metadata.json').read_text())
        spec=sources.get(meta.get('source'))
        result_path=ROOT/'work/song-verification'/('full-'+folder.name+'.json')
        if not spec or not result_path.exists(): continue
        existing=next((row for row in target.list(limit=500) if row.get('source_ref')==meta['source']),None)
        row=existing or target.create(spec.get('title') or meta['title'],spec.get('artist',''),'batch',meta['source'])
        if row.get('phrase_history'):
            raise ValueError('Prepared pilot has manual edits; refusing replacement')
        sid=row['song_id']; audio=target.root/sid/'audio'/'original.wav'
        audio.parent.mkdir(exist_ok=True)
        if not audio.exists(): shutil.copy2(folder/'audio.wav',audio)
        run='pilot-'+uuid.uuid4().hex
        target.patch(sid,{'status':'running','active_run_id':run,
            'recording_version':meta['title']+(' — '+spec['note'] if spec.get('note') else ''),
            'audio':{'path':str(audio.relative_to(target.root)),'format':'wav','owned':True,
                     'original_name':meta['title'],'duration_ms':round(meta.get('duration',0)*1000)},
            'sources':[{'kind':'youtube','url':meta['source'],'title':meta['title'],
                        'requested':spec['request'],'note':spec.get('note',''),'version_verified':False}],
            'capabilities':{'audio':True}})
        recognition=json.loads((folder/'recognition.json').read_text())
        phrases=[{'phrase_id':run+'-giga-'+str(i),'text':s['text'],
                  'start_ms':round(s['start']*1000),'end_ms':round(s['end']*1000),
                  'words':[],'timing_quality':'phrase'} for i,s in enumerate(recognition['segments'])]
        saved=target.save_full_analysis(sid,run,phrases,recognition['model'],json.loads(result_path.read_text()))
        report.append({'song_id':sid,'title':saved['title'],'phrases':len(saved['phrases']),
                       'chords':len(saved['chords']),'errors':saved.get('analysis_errors',[]),
                       'version_note':spec.get('note','')})
    (ROOT/'work/song-verification/prepared-library-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return target,report


def publish(source,target_path):
    target=SongStore(target_path)
    known={row.get('source_ref') for row in target.list(limit=500)}
    added=[]
    for row in source.list(limit=500):
        if row.get('source_ref') in known: continue
        sid=row['song_id']; temporary=target.root/('.import-'+uuid.uuid4().hex)
        destination=target.root/sid
        if destination.exists(): raise ValueError('Song identifier collision; no replacement performed')
        shutil.copytree(source.root/sid,temporary)
        temporary.rename(destination)
        added.append(sid); known.add(row.get('source_ref'))
    # Index recovery discovers atomically published card directories on restart.
    return added


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--publish',type=Path)
    args=parser.parse_args();store,report=prepare()
    print(json.dumps({'prepared':len(report),'errors':sum(len(r['errors']) for r in report)},ensure_ascii=False))
    if args.publish: print(json.dumps({'published':publish(store,args.publish)}))
