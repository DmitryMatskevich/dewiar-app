"""Loopback player for exactly one verified render, with HTTP byte ranges."""
import atexit
import hashlib
import hmac
import html
import http.server
import os
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import urlsplit
from .video_exchange import get_import

_lock = threading.Lock()
_servers = {}


def _fingerprint(info):
    from .video_assets import fingerprint
    return fingerprint(info)


def open_preview(store, ident, track=None):
    job = get_import(store, ident)
    if job['state'] != 'imported': raise ValueError('Для просмотра нужен успешно проверенный файл')
    from .video_assets import verify
    receipt=job['receipt'];path,digest,stamp=verify(store,receipt)
    caption_bytes=None;track_markup=''
    if track:
        from .captions import render
        if track['import_id']!=ident or track['asset_sha256']!=digest or track['archived']:raise ValueError('Субтитры относятся к другому файлу')
        caption_bytes=render(track,'vtt').encode()
        track_markup=f'<track kind="subtitles" src="/captions.vtt" srclang="{html.escape(track["language"],quote=True)}" label="Субтитры — редакторские тайминги" default>'
    with _lock:
        key = (str(store.path), ident, job['stale'], track['id'] if track else None, track['revision'] if track else None)
        if key in _servers: return _servers[key][1]
        token = secrets.token_urlsafe(32); cookie_name = 'dewiar_video_' + token[:12]
        mime = {'.mp4':'video/mp4', '.mov':'video/quicktime', '.webm':'video/webm', '.mkv':'video/x-matroska'}[path.suffix]
        page = ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>Просмотр файла — Dewiar</title><style>body{background:#14212c;color:#e8f1f7;font:16px/1.5 system-ui;margin:0}header{padding:15px 22px;background:#5b4206}main{max-width:1100px;margin:24px auto;padding:0 20px}video{display:block;width:100%;max-height:70vh;background:#000}code{overflow-wrap:anywhere}p{color:#b8cbd9}</style>'
                '<header>ПРОСМОТР — файл не опубликован. Решение редактора смотрите в карточке видео.</header><main><h1>Результат монтажа</h1>'
                '<video controls preload="metadata" playsinline src="/media">'+track_markup+'</video>'
                '<p><a href="/download" download style="color:#9bd5ff">Скачать проверенный видеофайл</a></p>'
                '<p>Если браузер не поддерживает контейнер или кодек, используйте локальный видеоплеер. Проверка декодирования сама по себе не означает редакционное принятие.</p>'
                f'<p>Версия видео: {job["video_revision"]}. {"Сценарий уже изменён; файл относится к предыдущей версии." if job["stale"] else ""}</p>'
                f'<p>SHA256: <code>{html.escape(digest)}</code></p></main></html>').encode()
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self): self.serve(False)
            def do_HEAD(self): self.serve(True)
            def headers_for(self, status, length, content_type):
                self.send_response(status); self.send_header('Content-Type',content_type); self.send_header('Content-Length',str(length))
                self.send_header('Cache-Control','no-store'); self.send_header('Referrer-Policy','no-referrer'); self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; media-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            def serve(self, head):
                route = urlsplit(self.path).path
                if route == '/open/' + token:
                    self.send_response(303); self.send_header('Set-Cookie',cookie_name+'='+token+'; Path=/; HttpOnly; SameSite=Strict'); self.send_header('Location','/'); self.end_headers(); return
                found = re.search(r'(?:^|;\s*)'+re.escape(cookie_name)+'=([^;]+)', self.headers.get('Cookie',''))
                if not found or not hmac.compare_digest(found[1],token): self.send_error(403); return
                if route == '/':
                    self.headers_for(200,len(page),'text/html; charset=utf-8'); self.end_headers()
                    if not head: self.wfile.write(page)
                    return
                if route == '/captions.vtt' and caption_bytes is not None:
                    self.headers_for(200,len(caption_bytes),'text/vtt; charset=utf-8');self.end_headers()
                    if not head:self.wfile.write(caption_bytes)
                    return
                if route not in ('/media','/download'): self.send_error(404); return
                try: fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
                except OSError:self.send_error(409,'Saved video unavailable');return
                try:
                    with os.fdopen(fd,'rb') as file:
                        if _fingerprint(os.fstat(file.fileno())) != stamp: self.send_error(409,'Saved video changed'); return
                        start, end, status = 0, stamp[1]-1, 200
                        value = self.headers.get('Range')
                        if value:
                            match = re.fullmatch(r'bytes=(\d{0,18})-(\d{0,18})',value)
                            if not match or not any(match.groups()): self.range_error(); return
                            first, last = match.groups()
                            if first: start=int(first); end=min(int(last),end) if last else end
                            else:
                                count=int(last)
                                if count<=0:self.range_error();return
                                start=max(0,stamp[1]-count)
                            if start>end or start>=stamp[1]:self.range_error();return
                            status=206
                        length=end-start+1; self.headers_for(status,length,mime); self.send_header('Accept-Ranges','bytes')
                        if route=='/download':self.send_header('Content-Disposition',f'attachment; filename="{digest}{path.suffix}"')
                        if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{stamp[1]}')
                        self.end_headers()
                        if not head:
                            file.seek(start)
                            while length:
                                chunk=file.read(min(length,1024*1024))
                                if not chunk:break
                                self.wfile.write(chunk);length-=len(chunk)
                except (OSError,ValueError):
                    # A closed player/seek can close its response early.
                    return
            def range_error(self):
                self.send_response(416);self.send_header('Content-Range',f'bytes */{stamp[1]}');self.send_header('Content-Length','0');self.end_headers()
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True,name='content-video-preview').start()
        url=f'http://127.0.0.1:{server.server_port}/open/{token}';_servers[key]=(server,url);return url


def close_all():
    with _lock:
        for server,_ in _servers.values(): server.shutdown();server.server_close()
        _servers.clear()


atexit.register(close_all)
