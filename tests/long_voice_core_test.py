"""Actual isolated core: continuation reaches the HUD and partial failures stay in history."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
TEXT = ('Это полный длинный ответ с сохранением всех предложений. ' * 100)
TAIL = 'Последняя контрольная фраза ответа.'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        body = json.dumps({'data': [{'id': 'fixture'}]}).encode()
        self.send_response(200);self.end_headers();self.wfile.write(body)
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        prompt = json.dumps(body.get('messages', []), ensure_ascii=False)
        relevant = 'TEST_LONG_RESPONSE' in prompt or 'TEST_BROKEN_RESPONSE' in prompt
        continuation = 'Ответ оборвался из-за технического' in prompt
        if 'TEST_BROKEN_RESPONSE' in prompt and continuation:
            self.send_response(503);self.end_headers();return
        if relevant:
            text, reason = (TAIL, 'stop') if continuation else (TEXT, 'length')
        else:
            text, reason = 'Готово.', 'stop'
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
        for data in [{'content':text}, {}]:
            finish = reason if not data else None
            self.wfile.write(('data: '+json.dumps({'choices':[{'delta':data,'finish_reason':finish}]},ensure_ascii=False)+'\n\n').encode())


with tempfile.TemporaryDirectory() as folder:
    tmp = Path(folder);cfg = tmp/'cfg';cfg.mkdir()
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    profile={'kind':'openai','base_url':f'http://127.0.0.1:{server.server_port}/v1','model':'fixture','max_tokens':128,
             'think':'off','roles':['communication','analytics','planning','coding']}
    (cfg/'settings.json').write_text(json.dumps({'ui':{'welcome':'done'},'sync':{'enabled':False},'privacy':{'local_only':True},
        'team':{'route':False},'tts':{'text_only':True,'engine':'off'},'audio':{'follow_up_seconds':0},'tools':{'enabled_all':False},
        'llm':{'active':'fixture','auto_select':False,'profiles':{'fixture':profile}}}))
    env={k:v for k,v in os.environ.items() if not k.startswith('DEWIAR_')}
    env.update(DEWIAR_CONFIG_DIR=str(cfg),DEWIAR_STATE_DIR=str(tmp/'state'),DEWIAR_WORKSPACE_DIR=str(tmp/'workspace'),
               DEWIAR_PORT=str(port),NO_PROXY='127.0.0.1,localhost',no_proxy='127.0.0.1,localhost')
    with (tmp/'core.log').open('w') as log:
        process=subprocess.Popen([sys.executable,str(ROOT/'core/main.py'),'--server','--null-audio'],env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            ws=None;deadline=time.monotonic()+35
            while time.monotonic()<deadline:
                if process.poll() is not None:raise AssertionError((tmp/'core.log').read_text()[-5000:])
                if (cfg/'token').exists():
                    try:ws=connect(f'ws://127.0.0.1:{port}',proxy=None,open_timeout=1,close_timeout=1);break
                    except OSError:pass
                time.sleep(.1)
            assert ws is not None,'Core did not start'
            with ws:
                ws.send(json.dumps({'cmd':'hello','token':(cfg/'token').read_text().strip()}));ws.recv(timeout=5)
                for index, prompt in enumerate(['TEST_LONG_RESPONSE','TEST_BROKEN_RESPONSE']):
                    ws.send(json.dumps({'id':index+1,'cmd':'say','args':{'text':prompt}}))
                    answer='';deadline=time.monotonic()+25
                    while time.monotonic()<deadline:
                        event=json.loads(ws.recv(timeout=5))
                        if event.get('type')=='answer' and event.get('done'):
                            answer=event['text'];break
                    assert answer.startswith(TEXT.rstrip()), (prompt,answer[:200])
                    if not index:
                        assert answer==TEXT+TAIL,(len(answer),answer[-200:])
                    else:
                        assert 'Не получилось' in answer,answer[-200:]
                    # The persistent chat must retain the full partial result too.
                    ws.send(json.dumps({'id':99,'cmd':'chat_list','args':{}}))
                    while True:
                        event=json.loads(ws.recv(timeout=5))
                        if event.get('id')==99:
                            state=event.get('data') or {};break
                    history=state.get('items') or []
                    assert any(m.get('role')=='assistant' and m.get('text')==answer.strip() for m in history), 'Full answer missing from persistent chat'
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            server.shutdown();server.server_close()
print('ALL OK: actual core continues a long reply and preserves partial text after continuation failure')
