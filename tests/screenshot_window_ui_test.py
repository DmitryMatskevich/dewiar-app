"""Standalone desktop screenshot window: original pixels, 1:1 zoom, region confirmation."""
import base64
import io
import os
import shutil
import tempfile
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
image=io.BytesIO();Image.new('RGB',(2400,1200),(90,160,210)).save(image,'PNG')
url='data:image/png;base64,'+base64.b64encode(image.getvalue()).decode()
with tempfile.TemporaryDirectory() as folder,sync_playwright() as pw:
    ui=Path(folder);(ui/'assets').mkdir()
    shutil.copy(ROOT/'ui/screenshot-window.html',ui/'screenshot-window.html')
    for name in ['screenshot-picker.js','screenshot-picker.css']:
        shutil.copy(ROOT/'ui/assets'/name,ui/'assets'/name)
    (ui/'assets/fonts.css').write_text('')
    (ui/'assets/dewiar.js').write_text('''
      window.calls=[];window.screenshotClosed=false;
      window.api=()=>({async close_screenshot_window(){window.screenshotClosed=true;}});
      window.core={snapshot:{},on(){return this;},async call(cmd,args={}){
        calls.push([cmd,args]);
        if(cmd==='screenshot_image')return URL;
        if(cmd==='screenshot_draft')return {url:URL,width:2400,height:1200};
        return {id:'result'};
      }};
    '''.replace('URL',repr(url)))
    browser=getattr(pw,os.environ.get('DEWIAR_TEST_BROWSER','chromium')).launch()
    page=browser.new_page(viewport={'width':1100,'height':780});errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto((ui/'screenshot-window.html').as_uri()+'?mode=view&id=abc')
    page.locator('.ss-full-image').wait_for()
    assert page.locator('.ss-full-image').get_attribute('src')==url
    page.get_by_role('button',name='Показать 1:1',exact=True).click()
    assert page.locator('.ss-actual').count()==1
    assert page.locator('.ss-view-stage').evaluate('(e)=>e.scrollWidth')>=2400
    page.get_by_role('button',name='Вместить в окно',exact=True).click()
    assert page.locator('.ss-actual').count()==0
    page.goto((ui/'screenshot-window.html').as_uri()+'?mode=region&id=region')
    page.locator('.ss-region-stage canvas').wait_for()
    assert not page.evaluate("calls.some(c=>c[0]==='screenshot_attach')")
    page.locator('[data-ss-confirm="region"]').click()
    page.wait_for_function('window.screenshotClosed')
    args=page.evaluate("calls.find(c=>c[0]==='screenshot_attach')[1]")
    assert args['draft_id']=='region' and args['region']['width']==1920
    page.goto((ui/'screenshot-window.html').as_uri()+'?mode=region&id=cancel')
    page.locator('.ss-region-stage canvas').wait_for()
    page.keyboard.press('Escape');page.wait_for_function('window.screenshotClosed')
    assert page.evaluate("calls.some(c=>c[0]==='screenshot_draft_cancel')")
    assert not page.evaluate("calls.some(c=>c[0]==='screenshot_attach')")
    assert not errors,errors
    browser.close()
print('OK: standalone screenshot viewer and region window; original PNG, zoom, confirmation, Escape')
