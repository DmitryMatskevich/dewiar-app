"""Build the offline video chapter catalogue from its canonical JSON, or check it without changes."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'data/help-video-reference.json'
data=json.loads(source.read_text())
assert data['video_id']=='lXRiHj9j9Pk'
assert len(data['chapters'])==30
assert len({x['id'] for x in data['chapters']})==30
assert all(0<=x['start']<x['end']<=data['duration'] for x in data['chapters'])
assert all(a['end']==b['start'] for a,b in zip(data['chapters'],data['chapters'][1:]))
chapter=next(c for c in data['chapters'] if c['id']==data['requested_chapter'])
assert chapter['start']<=data['requested_offset']<chapter['end']
text='/* Generated from data/help-video-reference.json; no transcript or network access. */\nwindow.DEWIAR_VIDEO_HELP_REFERENCE = '+json.dumps(data,ensure_ascii=False,indent=2)+';\n'
target=ROOT/'ui/assets/help-video-reference.js'
if '--check' in sys.argv:
    assert target.read_text()==text,'Run tools/sync_help_video_reference.py to refresh the catalogue'
    print('OK: video provenance and all 30 chapters match the offline catalogue')
else:
    target.write_text(text)
    print('Updated:',target.relative_to(ROOT))
