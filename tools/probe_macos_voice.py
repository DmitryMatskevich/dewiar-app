"""Explicit live probe: verifies current app's voice mode and plays one canned phrase.

Does not record or transcribe ambient speech, change profiles, or invoke a language model.
"""
import argparse
import json
from pathlib import Path
import time
from websockets.sync.client import connect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true',required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    config=Path.home()/'Library/Application Support/Dewiar'
    meta=json.loads((config/'state/core-port.json').read_text())
    token=(config/'token').read_text().strip()
    with connect(f"ws://127.0.0.1:{meta['port']}",proxy=None) as ws:
        ws.send(json.dumps({'cmd':'hello','token':token}))
        data=json.loads(ws.recv(timeout=5))['data']
        if data['state'] not in ('idle','muted'):
            raise RuntimeError('App is busy; live voice probe skipped')
        deadline=time.monotonic()+25
        while data.get('audio_mode')=='voice' and not data.get('status',{}).get('stt_ready') and time.monotonic()<deadline:
            time.sleep(.3)
            ws.send(json.dumps({'id':999,'cmd':'hello','args':{}}))
            while True:
                message=json.loads(ws.recv(timeout=5))
                if message.get('id')==999:
                    data=message['data'];break
        status=data['status']
        assert data['audio_mode']=='voice' and status.get('input') and status.get('stt_ready'), 'Voice capture or recognition not ready'
        result={'audio_mode':data['audio_mode'],'input_open':bool(status.get('input')),'stt_ready':status['stt_ready'],
                'tts':status.get('tts'),'voice_levels':0,'max_voice_level':0,'completed':False}
        ws.send(json.dumps({'id':1,'cmd':'voice_test','args':{'text':'Проверка звука. Голосовой режим работает.'}}))
        started=False;deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            try:message=json.loads(ws.recv(timeout=1))
            except TimeoutError:continue
            if message.get('error'):raise RuntimeError(message['error'])
            if message.get('type')=='level' and message.get('src')=='voice':
                result['voice_levels']+=1
                result['max_voice_level']=max(result['max_voice_level'],message.get('v',0))
            if message.get('type')=='state':
                if message.get('state')=='speaking':started=True
                if started and message.get('state') in ('idle','muted'):
                    result['completed']=True;break
        assert result['completed'] and result['max_voice_level']>0,result
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
