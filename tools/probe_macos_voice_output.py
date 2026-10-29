"""Explicit speaker test of frozen core with a silent WAV; never reads the microphone."""
import argparse
import json,os,subprocess,tempfile,time,wave,socket
from pathlib import Path
from websockets.sync.client import connect
parser=argparse.ArgumentParser()
parser.add_argument("--play",action="store_true",required=True,help="play one canned test phrase")
parser.parse_args()
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='dewiar-voice-output-') as folder:
 t=Path(folder);cfg=t/'config';cfg.mkdir();wav=t/'silence.wav'
 with wave.open(str(wav),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0'*(16000*2*30))
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 (cfg/'settings.json').write_text(json.dumps({'tts':{'engine':'espeak','text_only':False},'privacy':{'local_only':True,'mic':'button'},'sync':{'enabled':False},'obsidian':{'vault':''},'welcome':{'on':False},'night':{'on':False},'diary':{'night':False},'audio':{'sounds':False},'report':{'auto':False,'pulse':False}}))
 env=dict(os.environ,DEWIAR_CONFIG_DIR=str(cfg),DEWIAR_STATE_DIR=str(t/'state'),DEWIAR_WORKSPACE_DIR=str(t/'workspace'),DEWIAR_PORT=str(port),DEWIAR_PROVISION=str(t/'no.json'))
 with (t/'core.log').open('w') as log:
  p=subprocess.Popen([str(root/'dist/Dewiar.app/Contents/MacOS/Dewiar'),'--core','--wav',str(wav)],env=env,stdout=log,stderr=log)
  try:
   deadline=time.monotonic()+20
   while not (cfg/'token').exists() and time.monotonic()<deadline:time.sleep(.1)
   token=(cfg/'token').read_text().strip()
   while True:
    try:ws=connect(f'ws://127.0.0.1:{port}',proxy=None);break
    except OSError:
     if time.monotonic()>deadline:raise
     time.sleep(.1)
   with ws:
    ws.send(json.dumps({'cmd':'hello','token':token}));ws.recv(timeout=5)
    ws.send(json.dumps({'id':1,'cmd':'voice_test','args':{'text':'Проверка звука. Голосовой режим работает.'}}))
    deadline=time.monotonic()+30;maximum=0;started=False;completed=False
    while time.monotonic()<deadline:
     try:r=json.loads(ws.recv(timeout=1))
     except TimeoutError:continue
     if r.get('error'):raise RuntimeError(r['error'])
     if r.get('type')=='level' and r.get('src')=='voice':maximum=max(maximum,r.get('v',0))
     if r.get('type')=='state':
      if r.get('state')=='speaking':started=True
      if started and r.get('state') in ('idle','muted'):completed=True;break
    result={'frozen':True,'actual_output':True,'microphone_used':False,'max_voice_level':maximum,'completed':completed}
    assert maximum>0 and completed,result
    (root/'work/voice-frozen-output.json').write_text(json.dumps(result,indent=2));print(result)
  except Exception:
   print((t/'core.log').read_text()[-1500:]);raise
  finally:
   p.terminate()
   try:p.wait(timeout=5)
   except subprocess.TimeoutExpired:p.kill();p.wait()
