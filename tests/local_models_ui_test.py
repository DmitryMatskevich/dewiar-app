from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
html=(root/'ui/settings.html').read_text(); panel=html[html.index('    <div class="wpanel" id="w-local">'):html.index('    <div class="wpanel" id="w-cloud">')]
script=(root/'ui/studio/connect.js').read_text().split('/* Shared inventory:')[1]
with sync_playwright() as p:
 b=p.chromium.launch(); page=b.new_page(viewport={'width':1000,'height':1000})
 page.set_content('<html><head></head><body><div id="p-brain">'+panel+'</div></body></html>')
 page.add_style_tag(path=str(root/'ui/studio/studio.css'))
 page.add_style_tag(content='body {display:block; overflow:auto; padding:24px;} #p-brain {max-width:960px;margin:auto;}')
 page.evaluate('''() => {
 window.$=s=>document.querySelector(s); window.calls=[]; window.S={llm:{profiles:{existing:{model:'flint@q4',base_url:'http://localhost/v1',priority:7}},auto_select:true}};
 window.openProf='';window.renderProfiles=()=>{};window.save=async p=>{Object.assign(S.llm,p.llm)};
 window.addProfile=async (source,p)=>{S.llm.profiles.new=p;window.openProf='new'};
 window.core={on:()=>{},call:async (cmd,args)=>{calls.push({cmd,args});if(cmd==='local_model_open_app')return {message:'Открыто приложение LM Studio'};if(cmd==='local_model_details')return {name:'Flint',model:args.model,app:'LM Studio',publisher:'recursivesi',parameters:'40B',context:262144,loaded_contexts:[8192],description:'<img src=x onerror=alert(1)>',metadata_source:'LM Studio'};if(cmd==='local_model_connect')return {model:args.model,base_url:'http://localhost/v1'};return {models:[
 {name:'Flint',model:'flint@q4',source:'lmstudio',app:'LM Studio',state:'downloaded',size_gib:22.5},
 {name:'Muse Glimmer',model:'muse@q4',source:'lmstudio',app:'LM Studio',state:'available'},
 {name:'Qwen',model:'pack',source:'strata',app:'Strata',state:'incomplete',runtime:true}],errors:[]}}};
 }''')
 page.add_script_tag(content='/* Shared inventory: '+script)
 page.locator('#w-local').evaluate("e=>{e.style.display='block';e.classList.add('on')}")
 page.evaluate('loadUnifiedModels()')
 assert page.locator('.local-model-row').count()==3
 page.get_by_role('button',name='Открыть LM Studio',exact=True).first.click()
 page.wait_for_function("document.querySelector('#local-model-result').textContent==='Открыто приложение LM Studio'")
 assert page.evaluate("calls.at(-1).cmd==='local_model_open_app' && calls.at(-1).args.source==='lmstudio'")
 assert not page.evaluate("calls.some(x=>x.cmd==='local_model_connect')")

 page.locator('.local-model-details summary').first.click()
 page.wait_for_function("document.querySelector('.local-model-details').textContent.includes('recursivesi')")
 assert '262' in page.locator('.local-model-details').first.inner_text()
 assert page.locator('.local-model-details img').count()==0
 assert '<img src=x' in page.locator('.local-model-details').first.inner_text()

 page.get_by_label('Поиск моделей').fill('flint');assert page.locator('.local-model-row').count()==1
 page.get_by_role('button',name='Использовать',exact=True).click();page.wait_for_function("S.llm.active==='existing'")
 assert page.evaluate('S.llm.profiles.existing.priority')==7
 assert page.evaluate('S.llm.auto_select') is False
 assert page.evaluate('calls.at(-1).args.model')=='flint@q4'

 # A new profile must also override automatic routing.
 page.evaluate('S.llm.auto_select=true')
 page.get_by_label('Поиск моделей').fill('muse')
 page.get_by_role('button',name='Использовать',exact=True).click()
 page.wait_for_function("S.llm.active==='new'")
 assert page.evaluate('S.llm.auto_select') is False
 assert page.evaluate('S.llm.profiles.new.model')=='muse@q4'
 # Failed connection leaves the user's active model and automatic mode unchanged.
 page.evaluate("window.savedCall=core.call; core.call=async(cmd,args)=>{if(cmd==='local_model_connect')throw Error('Сервер недоступен');return savedCall(cmd,args)}; S.llm.auto_select=true")
 page.get_by_role('button',name='Использовать',exact=True).click()
 page.wait_for_function("document.querySelector('#local-model-result').textContent==='Сервер недоступен'")
 assert page.evaluate("S.llm.active==='new' && S.llm.auto_select")
 page.evaluate('core.call=savedCall')
 # Exercise the actual Strata helper, also used by the advanced settings buttons.
 strata_script=(root/'ui/studio/brain.js').read_text().split('async function useStrataProfile(profile) {')[1].split("$('#strata-probe')")[0]
 page.add_script_tag(content='async function useStrataProfile(profile) {'+strata_script)
 page.evaluate("window.toast=()=>{}; S.llm.auto_select=true; S.llm.profiles.strata={model:'strata-m',base_url:'http://strata/v1',priority:2};")
 page.evaluate("useStrataProfile({model:'strata-m',base_url:'http://strata/v1'})")
 assert page.evaluate("S.llm.active==='strata' && !S.llm.auto_select && S.llm.profiles.strata.priority===2")
 page.get_by_label('Поиск моделей').fill('<script>');assert page.locator('.local-model-row').count()==0
 page.get_by_label('Поиск моделей').fill('');page.get_by_label('Показать').select_option('download');assert page.locator('.local-model-row a').count()==3
 page.get_by_label('Показать').select_option('local')
 page.locator('#w-local').evaluate("e=>{e.style.display='block';e.classList.add('on')}")
 page.locator('.local-model-details summary').first.click()
 page.wait_for_function("document.querySelector('.local-model-details').textContent.includes('recursivesi')")
 page.screenshot(path='/tmp/dewiar-models-desktop.png',full_page=True)
 page.set_viewport_size({'width':390,'height':844});page.screenshot(path='/tmp/dewiar-models-mobile.png',full_page=True)
 assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
 b.close()
print('PASS: inventory, filter, safe text, catalogs, selected model, profile priority, mobile width')
