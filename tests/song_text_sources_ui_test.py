"""Real editor controls, isolated store and synthetic text, no network/model calls."""
import sys,tempfile,threading
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from songs_ui_preview import SongStore,seed,make_server
with tempfile.TemporaryDirectory(prefix='dewiar-text-source-ui-') as tmp,sync_playwright() as pw:
 store=SongStore(Path(tmp)/'songs');seed(store);r=store.list()[0];sid=r['song_id']
 text='Тихий вечер согревае окна нашего дома'
 store.patch(sid,{'phrases':[{'phrase_id':'test','text':text,'start_ms':1000,'end_ms':9000,'words':[]}],'status':'review'})
 server=make_server(store);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1200,'height':900});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 try:
  page.goto(f'http://127.0.0.1:{server.server_port}/');page.locator(f'button[data-song-open="{sid}"]').click()
  panel=page.locator('.song-text-reference');panel.get_by_text('Вставить текст песни вручную',exact=True).click()
  panel.locator('#song-text-source-lyrics').fill(text.replace('согревае','согревает'))
  panel.get_by_role('button',name='Загрузить текст и сверить',exact=True).click()
  panel.get_by_role('button',name='Подтвердить источник и сверить',exact=True).click()
  panel.locator('[data-text-correction]').check();panel.get_by_role('button',name='Применить выбранные исправления').click()
  page.get_by_text('Выбранные исправления сохранены',exact=True).wait_for()
  assert store.get(sid)['phrases'][0]['text']==text.replace('согревае','согревает')
  page.reload();page.locator(f'button[data-song-open="{sid}"]').click()
  assert 'согревает' in page.locator('#song-guitar-content').inner_text()
  assert 'подтверждено вами' in panel.locator('.song-reference-diff > summary').inner_text()
  page.set_viewport_size({'width':375,'height':812})
  assert panel.bounding_box()['width']<=375
  assert not errors,errors
  print('OK: add pasted source → manual confirmation → correction → durable reload; mobile layout')
 finally:browser.close();server.shutdown();server.server_close();thread.join(timeout=5)
