"""Task clipboard behavior in the real Studio DOM; clipboard and core are fixtures."""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'work/task-copy'; OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as pw:
    browser = getattr(pw, os.environ.get('DEWIAR_TEST_BROWSER', 'chromium')).launch()
    page = browser.new_page(viewport={'width': 1250, 'height': 900})
    errors=[]; page.on('pageerror', lambda e: errors.append(str(e)))
    page.route(re.compile(r'^https?://'), lambda route: route.abort())
    page.add_init_script("""window.pywebview={api:{}};
      window.copied=[];
      Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>copied.push(text)}});
      const wait=()=>{if(!window.core)return setTimeout(wait,5);core.retry=()=>{};core.connect=()=>{};
        if(core.ws){core.ws.onclose=null;core.ws.close();}core.call=async()=>[];};wait();""")
    page.goto((ROOT/'ui/settings.html').as_uri()+'?port=65534')
    page.wait_for_function("typeof openTask==='function' && typeof chatCopyText==='function'")
    page.evaluate("""() => {
      S={ui:{},llm:{profiles:{}},tasks:{}};
      window.fixture={id:'a1234567',title:'Проверка копирования',dir:'/tmp/fixture/Задачи/a1234567',
        status:'running',status_ru:'В работе',created:'2026-11-01T12:00:00+03:00',steps:2,
        plan:'# План\\n- [x] Начать\\n- [ ] Закончить',notebook:'- Факт: версия 2',journal:'# Журнал\\n\\nПромежуточная запись',
        summary:'# Итог\\n\\nРезультат ещё уточняется',project:'/tmp/fixture',diff:'diff --git a/a.py b/a.py\\n@@ -1 +1 @@\\n-old\\n+new',
        team:{enabled:true,planners:[],max_rounds:3},discussion:'Обсуждение: замечание закрыто',team_review:'Проверки: без замечаний',
        outputs:[{path:'report.md'},{path:'data.txt'},{path:'page.html'},{path:'large.txt'},{path:'document.pdf'},{path:'image.png'}]};
      window.files={'report.md':{text:'# Отчёт\\n\\nТекст **результата**'},'data.txt':{text:'Строка 1\\nСтрока 2'},
        'page.html':{text:'<html><head><style>not copied</style></head><body><h1>Заголовок</h1><p>HTML текст</p><script>window.__htmlRan=true</script></body></html>'},
        'large.txt':{text:'Начало большого файла',truncated:true},'document.pdf':{text:'Извлечённый текст PDF',extracted:true}};
      core.call=async (cmd,args)=>{
        if(cmd==='tasks_list')return [fixture];
        if(cmd==='task_get')return JSON.parse(JSON.stringify(fixture));
        if(cmd==='ws_read'){
          if(window.holdReads)return await new Promise(resolve=>window.releaseRead=resolve);
          return files[args.path.split('/').pop()] || {binary:true};
        }
        return {};
      };
      document.querySelectorAll('main section').forEach(s=>s.classList.toggle('cur',s.id==='p-tasks'));
      loadTasks();
    }""")
    page.wait_for_selector('#t-copy')
    button=page.locator('#t-copy')
    # Running tasks expose all intermediate text, without waiting for completion.
    for tab,field in [('plan','plan'),('journal','journal'),('notebook','notebook'),('summary','summary'),('diff','diff'),('discussion','discussion'),('team_review','team_review')]:
        page.locator(f'#t-view [data-tab="{tab}"]').click()
        page.wait_for_function("!document.querySelector('#t-copy').disabled")
        button.click()
        page.wait_for_function('(field)=>copied.at(-1)===fixture[field]',arg=field)
        assert page.locator('#t-copy').count()==1
    # The selection survives the mouse press; unrelated controls are not copied.
    page.locator('#t-view [data-tab="journal"]').click()
    expected=page.evaluate("""() => {const node=document.querySelector('#t-content p').firstChild;
      const range=document.createRange();range.setStart(node,0);range.setEnd(node,8);
      const selection=getSelection();selection.removeAllRanges();selection.addRange(range);return selection.toString();}""")
    button.click(); page.wait_for_function('(expected)=>copied.at(-1)===expected',arg=expected)
    page.evaluate('getSelection().removeAllRanges()')
    page.evaluate("fixture.journal+='\\nОбновление во время работы';openTask(fixture.id)")
    page.wait_for_function("document.querySelector('#t-content').textContent.includes('Обновление')")
    button.focus(); page.keyboard.press('Enter')
    page.wait_for_function("copied.at(-1)===fixture.journal")

    page.locator('#t-view [data-tab="results"]').click()
    for path,expected in [('report.md','# Отчёт\n\nТекст **результата**'),('data.txt','Строка 1\nСтрока 2'),('page.html','Заголовок\nHTML текст'),('document.pdf','Извлечённый текст PDF')]:
        page.locator(f'#t-view .oi[data-p="{path}"]').click()
        page.wait_for_function("!document.querySelector('#t-copy').disabled")
        button.click();page.wait_for_function('(expected)=>copied.at(-1)===expected',arg=expected)
    assert not page.evaluate('Boolean(window.__htmlRan)')
    page.locator('#t-view .oi[data-p="large.txt"]').click()
    page.wait_for_function("!document.querySelector('#t-copy').disabled")
    assert 'только загруженный фрагмент' in button.get_attribute('title')
    button.click();page.wait_for_function("document.querySelector('#toast').textContent.includes('фрагмент')")
    page.locator('#t-view .oi[data-p="image.png"]').click()
    assert button.is_disabled()
    assert 'изображение' in button.get_attribute('title')
    # A late file read cannot replace the clipboard payload of a new tab.
    page.evaluate('holdReads=true')
    page.locator('#t-view .oi[data-p="data.txt"]').click()
    page.wait_for_function('!!window.releaseRead')
    assert button.is_disabled()
    page.locator('#t-view [data-tab="journal"]').click()
    page.evaluate("releaseRead({text:'STALE RESULT'});holdReads=false")
    button.click();page.wait_for_function("copied.at(-1)===fixture.journal")
    # Fallback clipboard path and a real failure must not produce false success.
    page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined});document.execCommand=()=>{copied.push(document.activeElement.value);return true}")
    button.click();page.wait_for_function("copied.at(-1)===fixture.journal")
    before=page.evaluate('copied.length')
    page.evaluate('document.execCommand=()=>false')
    button.click();page.wait_for_function("document.querySelector('#toast').textContent.includes('недоступен')")
    assert page.evaluate('copied.length')==before
    assert not button.is_disabled()
    for skin,width in [('neusky',1250),('neudark',1250),('neusky',760)]:
        page.evaluate('(skin)=>studioSkin(skin)',skin);page.set_viewport_size({'width':width,'height':900})
        rect=button.bounding_box();assert rect and rect['width']<=36
        assert rect['x']+rect['width']<=width
        page.screenshot(path=str(OUT/f'{skin}-{width}.png'))
    page.evaluate("showHelp('tasks')")
    assert 'двумя листами' in page.locator('#help-body').inner_text()
    assert not errors,errors
    # HUD: no clipboard icon, but keyboard paste still obtains an image.
    hud=browser.new_page(viewport={'width':420,'height':650})
    hud.add_init_script("""window.pywebview={api:{clipboard_image:async()=>'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=='}};
      const wait=()=>{if(!window.core)return setTimeout(wait,5);core.retry=()=>{};core.connect=()=>{};if(core.ws){core.ws.onclose=null;core.ws.close();}core.call=async()=>({});};wait();""")
    hud.goto((ROOT/'ui/hud.html').as_uri()+'?port=65534')
    assert hud.locator('#a-clip').count()==0
    hud.locator('#askin').focus()
    hud.evaluate("document.querySelector('#askin').dispatchEvent(new ClipboardEvent('paste',{clipboardData:new DataTransfer(),bubbles:true,cancelable:true}))")
    hud.wait_for_function('atts.length===1')
    hud.screenshot(path=str(OUT/'hud-no-clipboard-button.png'))
    browser.close()
print('ALL OK: copy task text/selection during work, results, HTML/PDF, truncation, stale reads, clipboard fallback/errors, Help; removed HUD icon with keyboard paste preserved')
