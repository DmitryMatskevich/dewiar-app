"""Isolated real content editor and real Store; never starts Engine or loads settings."""
import http.server
import json
from pathlib import Path
import secrets
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from content.store import ContentStore
from content.commands import dispatch

def server(store, port=0, tasks=None, ws=None, command_handler=None, oauth_fixture=None,backup_fixture=False,theme_fixture=False):
    token=secrets.token_urlsafe(32)
    archive_script=ROOT/'ui/studio/content_archives.js'
    files={f'/studio/{name}':ROOT/'ui/studio'/name for name in ('content.js','content_sources.js','content_sites.js','distribution.js','content.css','social.js','social.css','video.js','video_exchange.js','higgsfield.js','oauth.js','captions.js','video_review.js','video_distribution.js','authors.js','authors.css','video.css','studio.css')}
    html='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ТЕСТ — Контент — отдельные данные</title>
<link rel="stylesheet" href="/studio/studio.css"><link rel="stylesheet" href="/studio/content.css">
<style>body{display:block;overflow:auto;height:auto;background:#121b24;color:#e7eff4}main{max-width:1280px;margin:auto;padding:24px}#test-banner{position:sticky;top:0;z-index:10;padding:14px;background:#5b4206;color:#fff5d2;font-weight:700} @media(max-width:600px){main{padding:12px}}</style>
<div id="test-banner">ТЕСТ — отдельные временные данные. Рабочие настройки, модели и аккаунты не подключены.</div><main><div id="content-app"></div></main>
<script>const core={on(){},async call(cmd,args){const r=await fetch('/api',{method:'POST',headers:{'Content-Type':'application/json','X-Test-Token':TOKEN},body:JSON.stringify({cmd,args})});const d=await r.json();if(!r.ok)throw Error(d.error);return d.data;}};</script><script src="/studio/content.js"></script><script>loadContentPage();</script></html>'''.replace('TOKEN',json.dumps(token)).encode()
    html=html.replace(b'<script>loadContentPage();</script>',b'<script src="/studio/content_sources.js"></script><script src="/studio/content_sites.js"></script><script src="/studio/distribution.js"></script><script>loadContentPage();</script>')
    files['/studio/content_archives.js']=archive_script
    html=html.replace(b'</html>',b'<script src="/studio/content_archives.js"></script></html>')
    html=html.replace(b'</main>',b'<hr><div id="social-app"></div></main>').replace(b'</html>',b'<link rel="stylesheet" href="/studio/social.css"><script src="/studio/social.js"></script><script>loadSocialPage();</script></html>')
    html=html.replace(b'</main>',b'<hr><div id="video-app"></div></main>').replace(b'</html>',b'<link rel="stylesheet" href="/studio/video.css"><script src="/studio/video.js"></script><script src="/studio/video_exchange.js"></script><script src="/studio/higgsfield.js"></script><script src="/studio/oauth.js"></script><script src="/studio/captions.js"></script><script src="/studio/video_review.js"></script><script src="/studio/video_distribution.js"></script><script>loadVideoPage();</script></html>')
    html=html.replace(b'</html>',b'<link rel="stylesheet" href="/studio/authors.css"><script src="/studio/authors.js"></script><script>if(!core.on||!core.connected)loadAuthorsPage();</script></html>')
    if ws:
        html=html.replace(b'<script>if(!core.on||!core.connected)loadAuthorsPage();</script>',b'')
        files['/assets/dewiar.js']=ROOT/'ui/assets/dewiar.js'
        start=html.index(b'<script>const core=');end=html.index(b'</script>',start)+len(b'</script>')
        html=html[:start]+b'<script src="/assets/dewiar.js"></script>'+html[end:]
        html=html.replace(b'loadContentPage();',b"core.on('hello',loadContentPage); if(core.connected)loadContentPage();")
        html=html.replace(b'loadSocialPage();',b"core.on('hello',loadSocialPage); if(core.connected)loadSocialPage();")
        html=html.replace(b'loadVideoPage();',b"core.on('hello',loadVideoPage); if(core.connected)loadVideoPage();")
    if backup_fixture:
        files['/studio/snapshot.js']=ROOT/'ui/studio/snapshot.js'
        html=html.replace(b'<main>',('<main><p>ТЕСТ резервных копий: временная рабочая папка. Перезапуск имитируется.</p><div style="display:flex;justify-content:flex-end"><div id="sn" class="sn-wrap"></div></div><p id="test-toast" role="status"></p>').encode())
        html=html.replace(b'</html>',b'<script>const $=s=>document.querySelector(s),$$=s=>Array.from(document.querySelectorAll(s));function toast(t){$("#test-toast").textContent=t;}</script><script src="/studio/snapshot.js"></script><script>core.on("hello",snLoad);snLoad();</script></html>')
    if theme_fixture:
        for name in ('theme.css','theme.js'):files['/studio/'+name]=ROOT/'ui/studio'/name
        for name in ('skins.css','skins.js'):files['/assets/'+name]=ROOT/'ui/assets'/name
        html=html.replace(b'<link rel="stylesheet" href="/studio/content.css">',b'<link rel="stylesheet" href="/assets/skins.css"><link rel="stylesheet" href="/studio/theme.css"><script src="/assets/skins.js"></script><script src="/studio/theme.js"></script><link rel="stylesheet" href="/studio/content.css">')
        html=html.replace(b'background:#121b24;color:#e7eff4',b'background:var(--scene);color:var(--ink)')
        html=html.replace(b'<main>',('<main><label>ТЕСТ — тема интерфейса<select id="test-skin"><option value="neusky">Небесный</option><option value="snow">Белоснежная</option><option value="brand">Фирменная</option><option value="neudark">Графитовый</option></select></label><p>Выбор меняет только этот тестовый браузер.</p>').encode())
        html=html.replace(b'</html>',b'<script>document.getElementById("test-skin").value=document.documentElement.dataset.skin;document.getElementById("test-skin").onchange=e=>studioSkin(e.target.value);</script></html>')
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,data,mime='application/json'):
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
        def do_GET(self):
            if oauth_fixture and self.path.startswith('/oauth-test/'):
                ident=self.path.removeprefix('/oauth-test/')
                if not oauth_fixture.exists(ident):return self.send(410,b'TEST session closed','text/plain')
                page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ТЕСТ — имитация входа — отдельные данные</title><style>body{font:18px system-ui;max-width:760px;margin:30px auto;padding:20px}header{padding:20px;background:#fff0b2}button{font:inherit;margin:20px 10px 20px 0;padding:12px}</style><header>ТЕСТ — отдельные данные. Это локальная имитация, а не сайт Higgsfield.</header><h1>Проверка входа Dewiar</h1><p>Пароли и реальные разрешения не используются. Выбор проверяет работу кнопок и сохранение тестового доступа.</p><button id="test-approve">ТЕСТ — разрешить</button><button id="test-deny">ТЕСТ — отказать</button><p id="result" role="status"></p><script>for(const [id,decision] of [['test-approve','approve'],['test-deny','deny']])document.getElementById(id).onclick=async()=>{try{const r=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json','X-Test-Token':TOKEN},body:JSON.stringify({decision})});const d=await r.json();document.getElementById('result').textContent=d.message||d.error;document.querySelectorAll('button').forEach(b=>b.disabled=true);}catch(e){document.getElementById('result').textContent='Тестовый сервер недоступен.';}};</script></html>'''
                return self.send(200,page.replace('TOKEN',json.dumps(token)).encode(),'text/html; charset=utf-8')
            if self.path.split('?')[0]=='/':return self.send(200,html,'text/html; charset=utf-8')
            p=files.get(self.path)
            if p:return self.send(200,p.read_bytes(),'text/css' if p.suffix=='.css' else 'text/javascript')
            self.send(404,b'{}')
        def do_POST(self):
            if oauth_fixture and self.path.startswith('/oauth-test/') and self.headers.get('X-Test-Token')==token:
                try:
                    n=int(self.headers.get('Content-Length','0'))
                    if not 0<n<1024:raise ValueError('Неверный размер')
                    oauth_fixture.decide(self.path.removeprefix('/oauth-test/'),json.loads(self.rfile.read(n)).get('decision'))
                    return self.send(200,json.dumps({'message':'Тестовый ответ принят. Вернитесь в Dewiar.'},ensure_ascii=False).encode())
                except (ValueError,TypeError):return self.send(400,b'{"error":"TEST request rejected"}')
            if self.path!='/api' or self.headers.get('X-Test-Token')!=token:return self.send(403,b'{}')
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=16*1024*1024:raise ValueError('Неверный размер')
                req=json.loads(self.rfile.read(n))
                from interface_origin import authenticated_interface
                with authenticated_interface():
                    result=command_handler(req['cmd'],req.get('args',{})) if command_handler else dispatch(store,req['cmd'],req.get('args',{}),tasks=tasks)
                self.send(200,json.dumps({'data':result},ensure_ascii=False).encode())
            except (ValueError,KeyError,TypeError) as e:self.send(400,json.dumps({'error':str(e)},ensure_ascii=False).encode())
    httpd=http.server.ThreadingHTTPServer(('127.0.0.1',port),Handler)
    httpd.close_content=getattr(command_handler,'close',lambda:None)
    if oauth_fixture:oauth_fixture.base_url=f'http://127.0.0.1:{httpd.server_port}'
    return httpd

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--bridge',action='store_true');parser.add_argument('--theme-fixture',action='store_true');parser.add_argument('--backup-fixture',action='store_true');parser.add_argument('--archive-fixture',action='store_true');parser.add_argument('--oauth-fixture',action='store_true');parser.add_argument('--social-fixture',action='store_true');parser.add_argument('--journal-fixture',action='store_true');parser.add_argument('--video-fixture',action='store_true');parser.add_argument('--astro-preview-root');options=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='dewiar-content-test-') as folder:
        folder=str(Path(folder).resolve())
        from types import SimpleNamespace
        taskdir=Path(folder)/'synthetic-task'; (taskdir/'outputs').mkdir(parents=True)
        (taskdir/'outputs/transcript.md').write_text('ТЕСТ — синтетическая расшифровка.\n00:00 Плановое обслуживание ЦОД.\n00:12 Проверка оборудования.',encoding='utf-8')
        tasks={'fixture-task':SimpleNamespace(dir=taskdir,data={'title':'ТЕСТ — готовая транскрибация','status':'done','workflow':'media-transcription','outputs':[{'path':'outputs/transcript.md'}]})}
        store=ContentStore(Path(folder)/'Контент')
        if options.video_fixture or options.archive_fixture:
            import shutil,subprocess
            from content import video_store
            clip=Path(folder)/'TEST-video.mp4'
            ffmpeg=shutil.which('ffmpeg')
            if not ffmpeg:raise ValueError('Existing ffmpeg required for explicit synthetic fixture; no installation')
            subprocess.run([ffmpeg,'-nostdin','-v','error','-f','lavfi','-i','testsrc2=s=320x180:r=25:d=2','-f','lavfi','-i','sine=frequency=600:duration=2','-shortest','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(clip)],check=True,timeout=15)
            fixture_video=video_store.create(store,{'request_key':'video-fixture','data':{'title':'ТЕСТ — проверка обмена видео','author':'Тестовый автор','brief':'Синтетическая таблица для проверки импорта, звука и проигрывания.','script':'Это тестовая видеозадача.','scenes':[{'id':'fixture1','title':'Тестовая таблица','narration':'','visual_prompt':'Синтетические цветовые полосы; тестовый тон.','duration_us':2000000}]}})
            if options.archive_fixture:
                import struct,time
                from content import video_exchange
                # Valid ISO BMFF free box: a large real MP4 without expensive
                # long encoding, models, media downloads or private footage.
                with clip.open('ab') as media:
                    media.write(struct.pack('>I4s',101*1024**2,b'free'))
                    remaining=101*1024**2-8
                    while remaining:
                        chunk=b'\0'*min(1024**2,remaining);media.write(chunk);remaining-=len(chunk)
                pack=video_exchange.handoff(store,{'id':fixture_video['id'],'revision':1})
                job=video_exchange.start_import(store,{'request_key':'archive-large-fixture','path':str(clip),'handoff_hash':pack['hash']})
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    result=video_exchange.get_import(store,job['id'])
                    if result['state']!='running':break
                    time.sleep(.05)
                if result['state']!='imported':raise RuntimeError('Synthetic large MP4 import failed: '+str(result['state']))
                store.create({'title':'ТЕСТ — полный архив','body':'ТЕСТ — первая сохранённая редакция.'},'archive-article')
                copies=Path(folder)/'TEST-copies';copies.mkdir()
                print('TEST archive destination: '+str(copies),flush=True)
            print('TEST media: '+str(clip),flush=True)
        if options.social_fixture or options.journal_fixture:
            from content.social_store import save
            import uuid
            def add(kind,data):return save(store,{'kind':kind,'data':data,'request_key':uuid.uuid4().hex})
            binds=[]
            for platform,host in [('linkedin','linkedin.com'),('facebook','facebook.com'),('threads','threads.com'),('x','x.com')]:
                conn=add('connections',{'name':'ТЕСТ — '+platform,'platform':platform})
                identity=add('identities',{'name':'ТЕСТ — автор '+platform,'platform':platform,'url':'https://'+host+'/dewiar-fixture'})
                dest=add('destinations',{'name':'ТЕСТ — лента '+platform,'platform':platform,'url':'https://'+host+'/dewiar-fixture'})
                binds.append(add('bindings',{'name':'ТЕСТ — '+platform,'connection_id':conn['id'],'identity_id':identity['id'],'destination_id':dest['id']})['id'])
            add('sets',{'name':'ТЕСТ — все четыре сети','binding_ids':binds})
            if options.journal_fixture:
                from content import distribution,packages,manual
                article=store.create({'title':'ТЕСТ — журнал публикаций','body':'ТЕСТ — первая редакция, сохранённая в старых пакетах.'},'journal-article')
                campaign=distribution.create(store,{'id':article['id'],'source_revision':1,'name':'ТЕСТ — история двух назначений','binding_ids':binds[:2],'request_key':'journal-campaign'})
                variant=next(v for v in campaign['variants'] if v['target']['destination']['platform']=='linkedin')
                package=packages.prepare(store,{'variant_id':variant['id'],'expected_revision':1})
                for i in range(1,24):manual.report(store,{'package_hash':package['hash'],'permalink':'https://linkedin.com/feed/update/dewiar-fixture-'+str(i),'note':'ТЕСТ — отчёт '+str(i)})
                distribution.update(store,{'variant_id':variant['id'],'expected_revision':1,'text':'ТЕСТ — новая редакция поста. Старые пакеты сохраняют первый текст.'})
        if options.astro_preview_root:
            from content.sites import base,service
            material=store.create({'title':'ТЕСТ — переносы ОСКС','author':'Тестовый автор','body':'Это синтетический материал для проверки редактора и локальной сборки сайта. Он не предназначен для публикации.\n\nПроверяем сохранение авторского текста, связность версии и отображение в общем шаблоне сайта.'},'site-fixture')
            service.configure(store,{'site_id':'site-ockc-ru','local_root':options.astro_preview_root,'expected_revision':0})
            edition=base.create(store,{'id':material['id'],'source_revision':1,'site_id':'site-ockc-ru','request_key':'edition-fixture','data':{'slug':'dewiar-ockc-preview-test','publication_date':'2026-11-08T12:00:00Z','category_id':'5'}})
            service.prepare(store,{'edition_id':edition['id'],'expected_revision':1,'dry_run':False})
        from contextlib import nullcontext
        from content_test_host import websocket,handler
        from content_oauth_fixture import OAuthFixture
        oauth_fixture=OAuthFixture() if options.oauth_fixture else None
        with (websocket(folder,tasks,oauth_fixture,options.backup_fixture) if options.bridge else nullcontext(None)) as ws:
            httpd=server(ContentStore(Path(folder)/'Контент'),tasks=tasks,ws=ws,command_handler=handler(folder,tasks,oauth_fixture,options.backup_fixture),oauth_fixture=oauth_fixture,backup_fixture=options.backup_fixture,theme_fixture=options.theme_fixture)
            print(f'http://127.0.0.1:{httpd.server_port}/'+(f'?port={ws[0]}&token={ws[1]}' if ws else ''),flush=True)
            try:httpd.serve_forever()
            except KeyboardInterrupt:pass
            finally:
                httpd.close_content()
                httpd.server_close()
                from content.sites.preview import close_all
                close_all()
                from content.video_preview import close_all as close_video_previews
                close_video_previews()
