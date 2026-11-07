"""Studio image OCR review/copy/insert; HUD task OCR and top results live in file_converter_ui_test."""
import base64
import io
import re
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'work/image-ocr';OUT.mkdir(parents=True,exist_ok=True)
image=Image.new('RGB',(2000,100),'white');buffer=io.BytesIO();image.save(buffer,'PNG')
URL='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()
with sync_playwright() as p:
    browser=p.chromium.launch()
    for name in ('settings',):
        page=browser.new_page(viewport={'width':420,'height':850});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.route(re.compile(r'^https?://'),lambda route:route.abort())
        page.add_init_script('''window.pywebview={api:{}};window.__calls=[];window.__copied=[];window.__sent=[];
Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>__copied.push(text)}});
const wait=()=>{if(!window.core)return setTimeout(wait,1);core.retry=()=>{};core.connect=()=>{};
if(core.ws){core.ws.onclose=null;core.ws.close();}
core.sendLater=async(cmd,args)=>{__sent.push({cmd,args});};core.call=async(cmd,args)=>{if(cmd==='image_ocr'){__calls.push(args);return new Promise(resolve=>window.__ocrReply=resolve);}return []};};wait();''')
        page.goto((ROOT/f'ui/{name}.html').as_uri()+'?port=65534')
        page.wait_for_function("typeof extractImageText==='function'")
        if name=='hud':
            page.evaluate("openAsk();document.querySelector('#askin').value='Мой черновик'")
            text='#askin';chips='#atts'; add='addFiles';count='atts.length'
        else:
            page.wait_for_function("typeof chatAddFiles==='function'")
            page.evaluate("document.querySelector('nav button[data-p=chat]').click();document.querySelector('#ch-text').value='Мой черновик'")
            text='#ch-text';chips='#ch-atts';add='chatAddFiles';count='chAtts.length'
        # A real pasted image retains original data for OCR despite its smaller chat preview.
        page.evaluate('''async ({url,add})=>{const bytes=Uint8Array.from(atob(url.split(',')[1]),c=>c.charCodeAt(0));
const file=new File([bytes],'pasted.png',{type:'image/png'});await window[add]([file]);}''',{'url':URL,'add':add})
        assert page.locator(chips+' .image-ocr-action').count()==1
        page.locator(chips+' .image-ocr-action').click()
        assert page.locator('#image-ocr-insert').is_disabled()
        assert page.evaluate('__calls[0].image')==URL
        result='Распознанный текст 42\n<script>window.__injected=true</script>'
        page.evaluate('(text)=>__ocrReply({status:"done",text,engine:"fixture"})',result)
        page.wait_for_function("!document.querySelector('#image-ocr-insert').disabled")
        assert page.locator('#image-ocr-text').input_value()==result
        assert not page.evaluate('Boolean(window.__injected)')
        page.locator('#image-ocr-copy').click();assert page.evaluate('__copied')==[result]
        page.locator('#image-ocr-text').fill('Проверенный текст 42')
        page.locator('#image-ocr-insert').click()
        assert page.locator(text).input_value()=='Мой черновик\n\nПроверенный текст 42'
        assert page.evaluate(count)==1
        assert page.evaluate('__sent.length')==0
        page.locator(chips+' .image-ocr-action').click();page.evaluate('__ocrReply({status:"empty",text:""})')
        page.wait_for_function("document.querySelector('#image-ocr-status').textContent.includes('не найден')")
        assert page.locator('#image-ocr-insert').is_disabled()
        page.locator('#image-ocr-close').click()
        page.locator(chips+' .image-ocr-action').click();page.evaluate('__ocrReply({status:"error",error:"OCR недоступен"})')
        page.wait_for_function("document.querySelector('#image-ocr-error').textContent==='OCR недоступен'")
        assert page.locator('#image-ocr-insert').is_disabled()
        page.locator('#image-ocr-close').click()
        page.locator(chips+' .image-ocr-action').click();page.evaluate('window.__old=__ocrReply')
        page.locator('#image-ocr-close').click();page.locator(chips+' .image-ocr-action').click()
        page.evaluate('__old({status:"done",text:"stale"})')
        assert page.locator('#image-ocr-text').input_value()==''
        page.evaluate('__ocrReply({status:"done",text:"Новый результат 123",engine:"fixture"})')
        page.wait_for_function("!document.querySelector('#image-ocr-insert').disabled")
        for width,light in ((420,False),(320,True)):
            page.set_viewport_size({'width':width,'height':850})
            page.evaluate('(light)=>document.body.classList.toggle("light",light)',light)
            assert page.locator('.image-ocr-panel').evaluate('e=>e.scrollWidth<=e.clientWidth')
            page.screenshot(path=str(OUT/f'{name}-{width}.png'))
        page.locator('#image-ocr-close').click()
        page.set_viewport_size({'width':1250,'height':900})
        page.locator(chips+(' button[data-i]' if name=='settings' else ' button:not(.image-ocr-action)')).click();assert page.evaluate(count)==0
        assert not errors,errors
        page.close()
    browser.close()
print('ALL OK: Studio pasted image, original OCR pixels, review/edit/copy/insert, literal text, no auto-send, empty/error/stale states, responsive layout, removal')
