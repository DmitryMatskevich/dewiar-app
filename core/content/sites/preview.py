"""Token-gated loopback preview with public-asset fallback and no form submissions."""
import atexit
import hashlib
import hmac
import http.server
import mimetypes
from pathlib import Path
import re
import secrets
import shutil
import threading
from urllib.parse import unquote,urlsplit

_lock=threading.Lock();_servers={}


def open_preview(receipt):
    dist=Path(receipt['dist']).resolve();public=Path(receipt['public_root']).resolve()
    article=dist/(receipt['article_path'].strip('/')+'/index.html')
    if not article.is_file() or hashlib.sha256(article.read_bytes()).hexdigest()!=receipt['article_sha256']:raise ValueError('Сборка удалена или изменена; выполните её заново')
    with _lock:
        key=str(dist)
        if key in _servers:return _servers[key][1]
        token=secrets.token_urlsafe(32)
        cookie_name='dewiar_preview_'+token[:12]
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_HEAD(self):self.serve(head=True)
            def do_GET(self):self.serve()
            def serve(self,head=False):
                path=urlsplit(self.path).path
                if path=='/open/'+token:
                    self.send_response(303);self.send_header('Set-Cookie',cookie_name+'='+token+'; Path=/; HttpOnly; SameSite=Strict');self.send_header('Location',receipt['article_path']);self.end_headers();return
                cookie=self.headers.get('Cookie','');match=re.search(r'(?:^|;\s*)'+re.escape(cookie_name)+'=([^;]+)',cookie)
                if not match or not hmac.compare_digest(match[1],token):self.send_error(403);return
                rel=unquote(path).lstrip('/')
                if '\x00' in rel or '\\' in rel or any(p.startswith('.') for p in rel.split('/') if p):self.send_error(404);return
                found=None
                for root in (dist,public):
                    candidate=(root/rel).resolve()
                    if candidate.is_dir():candidate=(candidate/'index.html').resolve()
                    if candidate.suffix.lower() not in {'.html','.htm','.css','.js','.mjs','.json','.xml','.txt','.svg','.png','.jpg','.jpeg','.webp','.gif','.ico','.avif','.woff','.woff2','.ttf','.otf','.pdf','.mp4','.webm','.mp3','.wav','.ogg','.vtt'}:continue
                    if candidate.is_relative_to(root) and candidate.is_file():found=candidate;break
                if found is None:self.send_error(404);return
                mime=mimetypes.guess_type(str(found))[0] or 'application/octet-stream'
                data=None
                if found.suffix=='.html':
                    text=found.read_text(encoding='utf-8')
                    banner='<div style="position:sticky;top:0;z-index:2147483647;background:#5b4206;color:#fff;padding:10px;font:16px system-ui">ПРЕДПРОСМОТР — сайт не опубликован. Отправка форм отключена.</div>'
                    text=re.sub(r'(<body\b[^>]*>)',lambda m:m[1]+banner,text,count=1,flags=re.I);data=text.encode()
                self.send_response(200);self.send_header('Content-Type',mime+('; charset=utf-8' if mime.startswith('text/') else ''));self.send_header('Content-Length',str(len(data) if data is not None else found.stat().st_size));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
                self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'none'; frame-src 'none'; form-action 'none'; object-src 'none'; base-uri 'self'")
                self.end_headers()
                if not head:
                    if data is not None:self.wfile.write(data)
                    else:
                        with found.open('rb') as f:shutil.copyfileobj(f,self.wfile)
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True,name='content-preview').start()
        url=f'http://127.0.0.1:{server.server_port}/open/{token}';_servers[key]=(server,url);return url


def close_all():
    with _lock:
        for server,_ in _servers.values():server.shutdown();server.server_close()
        _servers.clear()


atexit.register(close_all)
