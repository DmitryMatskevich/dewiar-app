"""Изолированный стенд настоящего редактора песен, без Engine/TTS/настроек.

Хранилище временное; используются только синтетические фразы и тихий WAV.
Стенд не подключается к рабочему ядру и не умеет скачивать модели. После
Ctrl+C сервер закрывается, временные тестовые данные удаляются.
"""
import base64
import functools
import http.server
import json
import secrets
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
from songs.store import SongStore
from songs.review import review_command
from songs.import_audio import AudioImporter
from songs.public import public_song


def seed(store):
    row = store.create('ТЕСТ — вымышленная песня для проверки редактора', 'Синтетические данные, не результат распознавания')
    path = store.root / row['song_id'] / 'audio' / 'silent.wav'
    path.parent.mkdir()
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(b'\x00\x00' * 16000 * 12)
    phrases = [{'phrase_id': f'test-{i}', 'start_ms': i*3000, 'end_ms': (i+1)*3000,
                'text': text, 'timing_quality': 'phrase', 'review_state': 'needs_review', 'note': ''}
               for i, text in enumerate(['Здесь проверяем первую строку.', 'И сохраняем каждую поправку.',
                                        'Здесь проверяем первую строку.', 'И сохраняем каждую поправку.'])]
    store.patch(row['song_id'], {'status': 'running', 'active_run_id': 'fixture',
        'audio': {'path': str(path.relative_to(store.root)), 'format': 'wav', 'duration_ms':12000},
        'capabilities': {'audio': True, 'lyrics': False, 'chords': False, 'structure': False}})
    store.save_analysis(row['song_id'], 'fixture', phrases, 'synthetic-fixture-not-ASR')


def page(token):
    source = (ROOT / 'ui/settings.html').read_text()
    section = '>' + source.split('<div id="p-songs" hidden>', 1)[1].split('</section>', 1)[0].rsplit('</div>', 1)[0]
    return ('''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ТЕСТ — Песни — отдельные данные</title><link rel="stylesheet" href="/studio/studio.css"><link rel="stylesheet" href="/studio/songs.css">
<style>body{display:block;overflow:auto;height:auto}main{max-width:1280px;margin:auto;padding:24px;overflow:visible}section{display:block!important}#test-banner{position:sticky;top:0;z-index:50;padding:14px 24px;background:#593b00;color:#fff3cb;font-weight:700}#test-message{padding:8px 24px} @media(max-width:600px){main{padding:12px}}</style>
<div id="test-banner" role="note">ТЕСТ — отдельные временные данные. Рабочие настройки и голоса не подключены.</div>
<div id="test-message" role="status"></div><main><section id="p-songs"''' + section + '''</section></main>
<script>
const S={features:{songs:true}};
function escH(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function toast(s){document.getElementById('test-message').textContent=s;}
const core={on(){},async call(cmd,args){const r=await fetch('/api',{method:'POST',headers:{'Content-Type':'application/json','X-Test-Token':''' + json.dumps(token) + '''},body:JSON.stringify({cmd,args})}); const v=await r.json();if(v.error)throw Error(v.error);return v.result;}};
</script><script src="/studio/song_sheet.js"></script><script src="/studio/song_review.js"></script><script src="/studio/songs.js"></script><script>loadSongs();</script></html>''').encode()


def make_server(store):
    token = secrets.token_urlsafe(32)
    importer = AudioImporter(store)
    assets = {f'/studio/{name}': ROOT/'ui/studio'/name for name in ('studio.css','songs.css','song_review.js','song_sheet.js','songs.js')}
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def reply(self, code, content, mime='application/json'):
            self.send_response(code); self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(content))); self.end_headers(); self.wfile.write(content)
        def do_GET(self):
            if self.path == '/': return self.reply(200, page(token), 'text/html; charset=utf-8')
            if self.path in assets:
                mime = 'text/css' if self.path.endswith('.css') else 'text/javascript'
                return self.reply(200, assets[self.path].read_bytes(), mime)
            self.reply(404, b'{}')
        def do_POST(self):
            origin = self.headers.get('Origin')
            expected = f'http://127.0.0.1:{self.server.server_port}'
            if self.path != '/api' or self.headers.get('X-Test-Token') != token or (origin and origin != expected):
                return self.reply(403, b'{}')
            size = int(self.headers.get('Content-Length') or 0)
            if not 0 < size < 1_600_000: return self.reply(413, b'{}')
            try:
                body = json.loads(self.rfile.read(size)); cmd = body['cmd']; a = body.get('args') or {}
                if cmd in {'song_edit_phrase','song_phrase_audio','song_full_audio','song_edit_structure','song_edit_chords','song_finish_review'}: result = review_command(store,cmd,a)
                elif cmd in {'song_upload_begin','song_upload_chunk','song_upload_finish','song_upload_cancel'}: result = importer.command(cmd,a)
                elif cmd == 'songs_list': result = store.list(a.get('q',''),a.get('status',''))
                elif cmd == 'song_get': result = store.get(a['song_id'])
                elif cmd == 'song_create': result = store.create(a.get('title',''),a.get('artist',''))
                elif cmd == 'song_source_add':
                    from songs.manual_sources import add_manual_source
                    result = add_manual_source(store, a['song_id'], a.get('url'), a.get('lyrics'), a.get('base_revision'))
                elif cmd == 'song_patch':
                    changes = a.get('changes') or {}
                    if set(changes)-{'title','artist'}: raise ValueError('Только название и исполнитель')
                    result = store.patch(a['song_id'],changes,a['base_revision'])
                elif cmd == 'song_delete': result = store.delete(a['song_id'])
                else: raise ValueError('На тестовом стенде запись и модели отключены. Здесь проверяется редактор.')
                result = [public_song(r,summary=True) for r in result] if cmd == 'songs_list' else public_song(result)
                self.reply(200,json.dumps({'result':result},ensure_ascii=False).encode())
            except (ValueError,KeyError,TypeError) as exc:
                self.reply(400,json.dumps({'error':str(exc)},ensure_ascii=False).encode())
    return http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)


def main():
    with tempfile.TemporaryDirectory(prefix='dewiar-songs-ui-') as temp:
        store=SongStore(Path(temp)/'songs'); seed(store)
        server=make_server(store)
        print(f'TEST ONLY: http://127.0.0.1:{server.server_port}/',flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()

if __name__ == '__main__': main()
