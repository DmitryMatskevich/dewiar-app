"""Shared Help/message shell: contrast, scrolling, modal stacking and keyboard, in WebKit."""
from pathlib import Path
import re
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]

CHECK = """() => {
 const root=document.querySelector('.dw-dialog[open]'),panel=root.querySelector('.dw-panel');
 const body=root.querySelector('.dw-body'),head=root.querySelector('.dw-header'),foot=root.querySelector('.dw-footer');
 const rect=e=>e.getBoundingClientRect();
 body.scrollTop=body.scrollHeight;
 const rgb=s=>s.match(/[\\d.]+/g).slice(0,3).map(Number);
 const lum=c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
 const bg=lum(rgb(getComputedStyle(panel).backgroundColor)),fg=lum(rgb(getComputedStyle(body).color));
 const x=root.querySelector('.dw-close'),r=rect(x);
 return {contrast:(Math.max(bg,fg)+.05)/(Math.min(bg,fg)+.05),
   fits:rect(panel).left>=0&&rect(panel).right<=innerWidth&&rect(panel).top>=0&&rect(panel).bottom<=innerHeight,
   pinned:rect(head).top>=rect(panel).top&&rect(foot).bottom<=rect(panel).bottom,
   scroll:body.scrollHeight>body.clientHeight&&body.scrollTop>0,
   clickable:x.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)),
   modal:root.matches(':modal')};
}"""
with sync_playwright() as p:
    browser=p.webkit.launch()
    for name in ['hud.html','settings.html']:
        page=browser.new_page(viewport={'width':390,'height':520})
        page.route(re.compile('^https?://'),lambda route:route.abort())
        page.add_init_script('window.pywebview={api:new Proxy({},{get:()=>()=>Promise.resolve(true)})}')
        page.goto((ROOT/'ui'/name).as_uri()+'?port=1&mode=card')
        page.wait_for_function('!!window.DewiarDialogs')
        for light in [True,False]:
            page.evaluate('(light)=>{document.body.classList.toggle("light",light);document.documentElement.classList.toggle("st-light",light)}',light)
            if name=='hud.html': page.locator('#b-help').click()
            else: page.evaluate("showHelp('brain')")
            result=page.evaluate(CHECK)
            assert result['contrast']>=4.5 and all(result[k] for k in ['fits','pinned','scroll','clickable','modal']),(name,light,result)
            page.locator('.dw-dialog[open] .dw-actions button').last.focus()
            page.keyboard.press('Tab')
            assert page.locator('.dw-dialog[open] .dw-close').evaluate('(e)=>e===document.activeElement')
            page.screenshot(path=str(ROOT/'work'/f'unified-{name}-{light}.png'))
            page.keyboard.press('Escape')
            assert page.locator('.dw-dialog[open]').count()==0
        page.evaluate("void messagePopup('Длинное сообщение. '.repeat(250),{title:'Сообщение'})")
        result=page.evaluate(CHECK)
        assert result['contrast']>=4.5 and result['fits'] and result['pinned'] and result['scroll'],result
        page.locator('.dw-dialog[open] .dw-copy').click()
        page.wait_for_function('!!document.querySelector(".dw-dialog[open] .dw-status").textContent')
        page.keyboard.press('Escape')
        page.close()
    browser.close()
print('OK: WebKit, common Help/message shell in HUD and Studio, light/dark contrast, 390x520 scrolling, pinned controls, focus and copy status')
