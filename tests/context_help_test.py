"""Real Studio DOM; all help articles and titles, no model or external service calls."""
import json
import re
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':1440,'height':960})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.route(re.compile('^https?://'),lambda route:route.abort())
        page.add_init_script("""window.__helpCalls=[];window.pywebview={api:{}};
          const wait=()=>{if(!window.core)return setTimeout(wait,5);core.retry=()=>{};core.connect=()=>{};
          if(core.ws){core.ws.onclose=null;core.ws.close();}core.call=async(c,a)=>{window.__helpCalls.push(c);return {};};};wait();""")
        page.goto((ROOT/'ui/settings.html').as_uri()+'?port=1')
        page.wait_for_function('!!window.refreshHelpTitles')
        page.evaluate("""() => {S={llm:{active:'fixture',profiles:{fixture:{name:'Fixture model',model:'test-only',kind:'openai',priority:1}}},ui:{},privacy:{},tools:{}};renderProfiles();refreshHelpTitles();}""")
        page.wait_for_timeout(100)
        missing=page.evaluate("""() => [...document.querySelectorAll('main section[id^="p-"]')].filter(s=>!s.querySelector('[data-section-help]')).map(s=>s.id)""")
        assert not missing,missing
        keys=page.evaluate('Object.keys(window.DEWIAR_HELP)')
        assert len(keys)>=33,len(keys)
        reference=json.loads((ROOT/'data/help-video-reference.json').read_text())
        assert page.evaluate('window.DEWIAR_VIDEO_HELP_REFERENCE')==reference
        assert len(reference['chapters'])==30
        assert next(c for c in reference['chapters'] if c['start']<=2005<c['end'])['id']=='V15'
        for chapter in reference['chapters']:
            for section in chapter['help_sections']:
                assert page.evaluate('([key,id])=>DEWIAR_HELP[key].video_refs.some(v=>v.id===id)',[section,chapter['id']]),(section,chapter['id'])
        page.evaluate("showHelp('soul')")
        assert 'раз в неделю' in page.locator('#help-body').inner_text()
        assert 'веса' in page.locator('#help-body').inner_text()
        page.evaluate("showHelp('const')")
        assert 'а не математическая гарантия' in page.locator('#help-body').inner_text()
        page.evaluate("showHelp('home')")
        assert 'Показать агента' in page.locator('#help-body').inner_text()
        page.evaluate("showHelp('brain')")
        brain_help = page.locator('#help-body').inner_text()
        assert all(text in brain_help for text in [
            'Окно контекста', '32 768', 'Context Length', 'OLLAMA_CONTEXT_LENGTH',
            'меньше 24 000', 'меньше 12 000', 'около 8 000',
            '«Общение» — 1 024', '«Планирование» — 16 384', '«Программирование» — 8 192', '«Аналитика» — 32 768'
        ])
        before=page.evaluate('window.__helpCalls.length')
        for key in keys:
            page.evaluate('(key)=>showHelp(key)',key)
            assert page.locator('#help').is_visible(),key
            content=page.locator('#help-body').inner_text()
            assert len(content)>200,(key,len(content))
            assert 'Как пользоваться' in content and 'Если не работает' in content,key
            assert page.locator('#help-body .tplay,#help-body a[href^="http"]').count()==0,key
        assert page.evaluate('window.__helpCalls.length')==before,'Reading help calls core'
        # Do not wipe form contents, active section or selection when opening/closing help.
        page.evaluate('helpClose()')
        page.evaluate("""() => {document.querySelectorAll('main section').forEach(s=>s.classList.toggle('cur',s.id==='p-brain'));document.querySelector('[data-f=name]').value='Unsaved fixture';}""")
        page.locator('#p-brain [data-section-help]').click()
        assert page.locator('#help-body').inner_text().startswith('Модели')
        page.keyboard.press('Escape')
        assert not page.locator('#help').is_visible()
        assert page.locator('[data-f=name]').input_value()=='Unsaved fixture'
        assert page.evaluate('document.activeElement.dataset.sectionHelp')=='brain'
        edge=page.evaluate("""() => {const r=document.querySelector('#p-brain [data-section-help]').getBoundingClientRect();return {right:r.right,gap:innerWidth-r.right};}""")
        assert 8 <= edge['gap'] <= 28,edge
        assert 'правого края окна' in page.locator('#p-brain [data-section-help]').get_attribute('title')
        page.evaluate("""() => {document.querySelectorAll('main section').forEach(s=>s.classList.toggle('cur',s.id==='p-home'));}""")
        home_edge=page.evaluate("""() => Object.fromEntries(['#home-help-slot button','#sn'].map(s=>{const e=document.querySelector(s),r=e?.getBoundingClientRect();return [s,r&&{right:r.right,gap:innerWidth-r.right,visible:r.width>0&&r.height>0}]}))""")
        assert home_edge['#home-help-slot button']['visible'],home_edge
        assert 8 <= home_edge['#home-help-slot button']['gap'] <= 28,home_edge
        if home_edge['#sn']['visible']:
            assert home_edge['#sn']['gap'] >= home_edge['#home-help-slot button']['gap'],home_edge
        page.evaluate("""() => {document.querySelectorAll('main section').forEach(s=>s.classList.toggle('cur',s.id==='p-brain'));}""")
        page.locator('#p-brain [data-section-help]').click()
        page.locator('#help-search').fill('раз в неделю')
        assert 'Душа' in page.locator('#help-results').inner_text()
        page.locator('#help-search').fill('Ctrl+Alt+N')
        assert page.locator('#help-results button').count()>0
        page.locator('#help-search').fill('микрофон')
        assert page.locator('#help-results button').count()>0
        page.locator('#help-results button').first.click()
        assert page.locator('#help-body').is_visible()
        page.locator('#help-search').fill('<script>window.__helpPwned=1</script>')
        assert 'Ничего не найдено' in page.locator('#help-results').inner_text()
        assert not page.evaluate('Boolean(window.__helpPwned)')
        page.locator('#help-all').click()
        assert page.locator('#help-results button').count()==len(keys)
        page.locator('#help-current').click()
        assert page.locator('#help-heading').inner_text()=='Модели'
        assert all(word in page.locator('#help-body').inner_text() for word in ['Общение', 'Планирование', 'Программирование', 'Аналитика'])
        page.locator('#help-x').click()
        # Late model cards and team controls get titles after rerender without duplicate help actions.
        page.evaluate("""() => {openProf='fixture';renderProfiles();document.querySelector('#t-new-team').innerHTML=taskTeamEditor('fixture',{});}""")
        page.wait_for_timeout(120)
        assert 'ночным записям' in page.locator('#s-upd').get_attribute('title')
        assert 'не очистка памяти' in page.locator('#m-reload').get_attribute('title')
        assert '1 — наивысший' in page.locator('[data-f=priority]').get_attribute('title')
        assert 'полночь' in page.locator('[data-f=schedule_end]').get_attribute('title')
        assert 'закрепляется' in page.locator('[data-team=judge]').get_attribute('title')
        page.locator('[data-a=test]').hover()
        assert 'токены' in page.locator('[data-a=test]').get_attribute('title')
        audit=page.evaluate("""() => {refreshHelpTitles();return {missing:[...document.querySelectorAll('button,input,select,textarea,summary')].filter(e=>!e.title.trim()).map(e=>e.id||e.outerHTML.slice(0,100)),buttons:document.querySelectorAll('button').length,duplicates:[...document.querySelectorAll('main section[id^="p-"]')].filter(s=>s.querySelectorAll('[data-section-help]').length!==1).map(s=>s.id),extra:document.querySelectorAll('main section .hbtn').length};}""")
        assert not audit['missing'],audit['missing']
        assert not audit['duplicates'],audit['duplicates']
        assert audit['extra']==0
        # Safe rendering of article data, including legacy string examples.
        page.evaluate("""() => {DEWIAR_HELP.fixture={title:'<img src=x onerror=window.__helpPwned=2>',what:'<script>bad</script>',how:['<b>plain</b>'],try:['Пример строкой'],troubleshoot:['Проверка']};showHelp('fixture');}""")
        assert page.locator('#help-body img,#help-body script').count()==0
        assert 'Пример строкой' in page.locator('#help-body').inner_text()
        page.evaluate("delete DEWIAR_HELP.fixture;showHelp('brain')")
        for skin in ['neusky','neudark','brand']:
            page.evaluate('(skin)=>studioSkin(skin)',skin)
            for width in [1440,1024,390]:
                page.set_viewport_size({'width':width,'height':960})
                box=page.locator('#help').bounding_box()
                assert box and box['x']>=-1 and box['x']+box['width']<=width+1,(skin,width,box)
                assert page.locator('#help').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'),(skin,width)
        page.wait_for_timeout(50)
        assert page.locator('#help').get_attribute('aria-modal')=='true'
        page.locator('#help-all').focus();page.keyboard.press('Tab')
        assert page.evaluate("document.activeElement.id")=='help-x'
        page.keyboard.press('Shift+Tab')
        assert page.evaluate("document.activeElement.id")=='help-all'
        # Reopen/close remains idempotent and does not rebuild user fields.
        for _ in range(8):page.evaluate("showHelp('brain');helpClose()")
        assert page.locator('[data-f=name]').input_value()=='Fixture model' # Last deliberate profile rerender, not help.
        relevant=[e for e in errors if not any(x in e for x in ['WebSocket','connect ECONNREFUSED'])]
        assert not relevant,relevant
        print(f'OK: {len(keys)} help articles, {audit["buttons"]} buttons titled, fields/late controls, local search, escaping, no executable examples, focus/unsaved form, themes and narrow/wide panels')
        page.set_viewport_size({'width':1440,'height':960})
        page.evaluate("studioSkin('neusky');showHelp('brain')")
        (ROOT/'work').mkdir(exist_ok=True)
        page.wait_for_timeout(350)
        page.screenshot(path=str(ROOT/'work/context-help-light.png'))
        page.evaluate("studioSkin('neudark')")
        page.wait_for_timeout(350)
        page.screenshot(path=str(ROOT/'work/context-help-dark.png'))
        hud=browser.new_page(viewport={'width':420,'height':700})
        hud.route(re.compile('^https?://'),lambda route:route.abort())
        hud.goto((ROOT/'ui/hud.html').as_uri()+'?port=1&mode=card')
        hud.wait_for_function("!!document.querySelector('button[title]')")
        missing=hud.evaluate("[...document.querySelectorAll('button')].filter(b=>!(b.title||b.dataset.tip)).map(b=>b.id)")
        assert not missing,missing
        hud.evaluate("document.getElementById('b-mic').title='Включить микрофон'")
        hud.locator('#b-mic').dispatch_event('mouseover')
        tip=hud.locator('#b-mic').get_attribute('data-tip')
        assert tip.startswith('Включить микрофон') and 'системный доступ' in tip,tip
        assert tip.count('Переключить голосовой ввод')==1
        hud.evaluate("core.call=async()=>({id:'fixture',name:'Иванов Иван Иванович',scope:'chat:fixture',local:[],mac:[],lookup_error:false});core.emit('contact_suggestion',{})")
        assert hud.locator('#contact-suggestion').is_visible()
        assert 'Создать контакт' in hud.locator('#contact-suggestion').inner_text()
        for button in hud.locator('#contact-suggestion button').all():
            assert button.bounding_box()['width']>80
        assert hud.locator('#contact-suggestion').evaluate('(e)=>e.scrollWidth<=e.clientWidth')
        print(f'OK: all {hud.locator("button").count()} HUD buttons have explanations; live microphone state preserved in established tooltip')
        browser.close()


if __name__=='__main__':main()
