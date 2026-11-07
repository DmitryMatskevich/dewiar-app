from pathlib import Path
import argparse
import json
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"core"))
from songs.reference_fragments import *  # compatibility for offline trials

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library',type=Path,required=True)
    p.add_argument('--refined',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    root=args.library.resolve();out=args.output.resolve()
    if root==out or root in out.parents:p.error('Output must be separate from the library')
    rows=[]
    for file in sorted(root.glob('*/song.json')):
        song=json.loads(file.read_text())
        pool=song.get('phrases',[])
        selected=anchors(pool)
        if len(selected)<3 and args.refined:
            path=args.refined/file.parent.name/'result.json'
            if path.is_file():
                extra=json.loads(path.read_text()).get('selected',{}).get('phrases',[])
                # Preserve origins for auditing which analysis supplied a query.
                pool=pool+[dict(p,phrase_id='trial:'+str(p.get('phrase_id'))) for p in extra]
                selected=anchors(pool)
        rows.append(dict(song_id=file.parent.name,title=song.get('title'),
            anchors=selected,queries=query_plan(selected,song.get('title',''),song.get('artist',''))))
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    for r in rows:
        print(r['song_id'],r['title'])
        for q in r['queries'][:3]:print(' ',q['query'])
        if not r['anchors']:print(' NO RELIABLE FRAGMENT')


if __name__=='__main__':main()
