"""Calendar category sums, periods across years, HUD updates, saved Studio choices."""
import os, shutil, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder, sync_playwright() as pw:
    ui=Path(folder)/'ui';shutil.copytree(ROOT/'ui',ui)
    (ui/'assets/dewiar.js').write_text('''window.calls=[];window.opens=[];window.fixtureDays={};
      window.api=()=>({open_settings:(...args)=>opens.push(args)});
      window.core={_on:{},on(k,f){(this._on[k]??=[]).push(f);return this},async call(cmd,args={}){calls.push([cmd,args]);if(cmd==='calendar_month')return {days:fixtureDays};return {}}};
      window.fire=(type,data)=>(core._on[type]||[]).forEach(fn=>fn(data));''')
    browser=getattr(pw,os.environ.get('DEWIAR_TEST_BROWSER','chromium')).launch()
    page=browser.new_page(viewport={'width':420,'height':760});errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto((ui/'hud.html').as_uri())
    page.evaluate('''() => {const d=new Date(), key=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
      fixtureDays[key]=[{type:'reminder',title:'День рождения Анны'},{type:'event',title:'Встреча'},
        {type:'reminder',title:'Купить хлеб'},{type:'event',title:'Завершено',done:true},{type:'event',title:'Пауза',paused:true}];
      setCalendarBadgeSettings({calendar_badge_types:['birthday','event']});}''')
    badge=page.locator('#b-cal-count');page.wait_for_function("document.getElementById('b-cal-count').textContent==='2'")
    page.evaluate("fire('settings',{settings:{ui:{calendar_badge_types:['birthday','event','reminder']}}})")
    page.wait_for_function("document.getElementById('b-cal-count').textContent==='3'")
    page.locator('#b-cal').click(force=True);assert page.evaluate('opens.at(-1)')==['calendar']
    page.evaluate("setCalendarBadgeSettings({calendar_badge_types:[]})")
    page.wait_for_function("document.getElementById('b-cal-count').hidden")
    # December -> January queries exactly both months and uses local date boundaries.
    result=page.evaluate('''async () => {let queries=[];
      const n=await CalendarBadge.load({call:async(cmd,args)=>{queries.push(args);return {days:{'2026-12-31':[{type:'event'}],'2027-01-01':[{type:'event'}],'2027-01-07':[{type:'event'}]}}}}, {types:['event'],days:7},new Date(2026,11,31));
      return {n,queries};}''')
    assert result=={'n':2,'queries':[{'year':2026,'month':12},{'year':2027,'month':1}]},result
    page.evaluate("setCalendarBadgeSettings({calendar_badge_types:['birthday','event']})")
    page.wait_for_function("document.getElementById('b-cal-count').textContent==='2'")
    for skin in ['neusky','neudark']:
        page.evaluate('(s)=>applyLook({skin:s})',skin)
        for width in [340,640]:
            page.set_viewport_size({'width':width,'height':650})
            assert badge.is_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    shot=ROOT/'work/calendar-badge/hud.png';shot.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(shot))
    assert not errors,errors
    # Actual Studio controls save only the selected categories and period.
    studio=browser.new_page()
    studio.add_init_script('''const wait=()=>{if(!window.core)return setTimeout(wait,5);core.connect=()=>{};core.retry=()=>{};
      if(core.ws){core.ws.onclose=null;core.ws.close();}
      window.saved=[];core.call=async(cmd,args)=>{if(cmd==='settings_save'){saved.push(args.patch);return {ui:args.patch.ui}};return {days:{}};};};wait();''')
    studio.goto((ROOT/'ui/settings.html').as_uri()+'?port=65534#calendar')
    studio.wait_for_function("typeof renderCalendarBadgeSettings==='function'")
    studio.evaluate("S={ui:{}};renderCalendarBadgeSettings();document.querySelector('#p-calendar').classList.add('cur');document.querySelector('#p-calendar details').open=true")
    box=studio.locator('#cal-badge-types')
    assert box.locator('input:checked').count()==2
    box.locator('input[value=reminder]').check(force=True)
    studio.wait_for_function('saved.length===1')
    assert studio.evaluate('saved[0].ui.calendar_badge_types')==['birthday','event','reminder']
    studio.locator('#cal-badge-days').select_option('7',force=True)
    studio.wait_for_function('saved.length===2')
    assert studio.evaluate('saved[1].ui.calendar_badge_days')==7
    studio.evaluate('renderCalendarBadgeSettings()')
    assert studio.locator('#cal-badge-days').input_value()=='7'
    shot=ROOT/'work/calendar-badge/settings.png';studio.locator('#p-calendar details').evaluate('e=>e.open=true');studio.locator('#p-calendar details').screenshot(path=str(shot))
    browser.close()
print('ALL OK: calendar sums, exclusive birthdays, empty selection, completed/paused, year boundary, HUD and saved settings')
