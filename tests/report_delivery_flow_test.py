"""Real core + local HTTP/SMTP fixtures, including durable pending delivery and MIME screenshot."""
import asyncio
import base64
import email
import io
import json
import socketserver
import subprocess
import sys
import tempfile
import threading
from email import policy
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from exam import Core


class HTTP(BaseHTTPRequestHandler):
    fail=False;requests=[]
    def log_message(self,*args):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).requests.append((self.path,body,self.headers.get('Authorization')))
        self.send_response(503 if self.fail else 200);self.send_header('Content-Type','application/json');self.end_headers()
        self.wfile.write(json.dumps({'ok':not self.fail,'id':123,'secret':'fixture-status-secret'}).encode())


class SMTP(socketserver.StreamRequestHandler):
    messages=[]
    def handle(self):
        self.wfile.write(b'220 localhost fixture\r\n')
        while True:
            line=self.rfile.readline()
            if not line:break
            command=line.split(b' ',1)[0].strip().upper()
            if command in (b'EHLO',b'HELO'):self.wfile.write(b'250-localhost\r\n250 AUTH PLAIN\r\n')
            elif command==b'AUTH':self.wfile.write(b'235 authenticated\r\n')
            elif command==b'DATA':
                self.wfile.write(b'354 data\r\n');parts=[]
                while True:
                    line=self.rfile.readline()
                    if line==b'.\r\n' or not line:break
                    parts.append(line[1:] if line.startswith(b'..') else line)
                type(self).messages.append(email.message_from_bytes(b''.join(parts),policy=policy.default))
                self.wfile.write(b'250 accepted\r\n')
            elif command==b'QUIT':self.wfile.write(b'221 bye\r\n');break
            else:self.wfile.write(b'250 ok\r\n')


async def main():
    http=ThreadingHTTPServer(('127.0.0.1',0),HTTP);smtp=socketserver.ThreadingTCPServer(('127.0.0.1',0),SMTP)
    for server in (http,smtp):threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='dewiar-report-') as folder:
            root=Path(folder);core=Core({'kind':'openai','model':'unused','base_url':'http://127.0.0.1:9/v1'},root)
            original=subprocess.Popen
            def spawn(command,*args,**kwargs):
                if '--bundle' in sys.argv and str(ROOT/'core/main.py') in command:
                    command=[str(ROOT/'dist/Dewiar.app/Contents/MacOS/Dewiar'),'--core','--server']
                return original(command,*args,**kwargs)
            try:
                with patch('subprocess.Popen',side_effect=spawn):core.start()
                await core.connect()
                await core.call('settings_save',patch={'report':{'server_url':f'http://127.0.0.1:{http.server_port}/reports','server_key':'fixture-own-server-key'},'privacy':{'dewiar_services':False}})
                first=await core.call('complaint_send',note='Синтетическая ошибка: проверка сервера',shot=False)
                assert first['delivery']=='server' and first['id']==123 and len(SMTP.messages)==0
                assert HTTP.requests[0][0]=='/reports' and HTTP.requests[0][2]=='Bearer fixture-own-server-key'
                HTTP.fail=True
                b=io.BytesIO();Image.new('RGB',(2,2),'red').save(b,'JPEG')
                image='data:image/jpeg;base64,'+base64.b64encode(b.getvalue()).decode()
                assert (await core.call('bug_shot',image=image))['ok']
                queued=await core.call('complaint_send',note='Синтетическая ошибка: проверка почты',shot=True)
                assert queued['delivery']=='queued' and queued['shot']
                status=await core.call('report_delivery_status')
                assert len(status['pending'])==1 and not status['mail_configured']
                assert 'fixture-status-secret' not in json.dumps(status)
                await core.call('mail_save',patch={'address':'sender@example.test','password':'fixture-only','smtp_host':'127.0.0.1','smtp_port':smtp.server_address[1],'smtp_security':'none'})
                sent=await core.call('report_delivery_retry',queue_id=queued['queue_id'])
                assert sent['delivery']=='email',sent
                assert len(SMTP.messages)==1
                msg=SMTP.messages[0]
                assert str(msg['To'])=='domatskevich@gmail.com' and str(msg['Subject'])=='Ошибка обработки персональный агент'
                files={p.get_filename():p for p in msg.iter_attachments()}
                assert set(files)=={'report.txt','screenshot.jpg'}
                assert 'Синтетическая ошибка: проверка почты' in files['report.txt'].get_content()
                assert files['screenshot.jpg'].get_payload(decode=True).startswith(b'\xff\xd8')
                await core.call('report_delivery_retry',queue_id=queued['queue_id'])
                assert len(SMTP.messages)==1, 'Confirmed email resent'
                assert not (await core.call('report_delivery_status'))['pending']
                complaints=await core.call('complaints')
                assert any(c.get('delivery')=='email' and c['status']=='email' for c in complaints['mine'])
                print('OK: real core own server, failed server queues, SMTP fallback with Russian subject/report/screenshot, retry exactly once, no vendor service or real email')
            finally:await core.stop()
    finally:
        for server in (http,smtp):server.shutdown();server.server_close()


if __name__=='__main__':asyncio.run(main())
