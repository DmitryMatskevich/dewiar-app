"""Real price panel: filters, estimates, source links, error state, responsive layout."""
from pathlib import Path
import sys
import tempfile
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from model_prices import get_prices
with tempfile.TemporaryDirectory() as tmp:
    data=get_prices(tmp)
    long_data=get_prices(tmp,input_tokens=120000)
html=(ROOT/'ui/settings.html').read_text()
start=html.index('    <div class="actions"><button class="btn" id="model-prices-open"')
end=html.index('    <p class="lead">',start)
with sync_playwright() as p:
    browser=p.chromium.launch();page=browser.new_page(viewport={'width':1100,'height':1100})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content('<html><body>'+html[start:end]+'</body></html>')
    page.add_style_tag(path=str(ROOT/'ui/studio/studio.css'))
    page.add_style_tag(content='body{display:block;padding:24px;overflow:auto} .card{max-width:1000px;margin:auto}')
    page.evaluate('window.$=s=>document.querySelector(s);window.calls=[];window.core={call:async(cmd,args)=>{calls.push({cmd,args});return args.input_tokens===120000 ? window.longPriceFixture : window.priceFixture}}')
    page.evaluate('(d)=>window.priceFixture=d',data)
    page.evaluate('(d)=>window.longPriceFixture=d',long_data)
    page.add_script_tag(path=str(ROOT/'ui/studio/model-prices.js'))
    page.get_by_role('button',name='Проверить стоимость моделей').click()
    page.wait_for_function("document.querySelector('#model-prices-status').textContent.includes('Проверено:')")
    assert page.evaluate("calls[0].cmd==='model_prices' && calls[0].args.refresh===true")
    page.get_by_label('Провайдер',exact=True).select_option('Claude')
    assert 'Claude' in page.locator('#model-prices-rows').inner_text()
    assert 'OpenAI' not in page.locator('#model-prices-rows').inner_text()
    page.get_by_label('Поиск',exact=True).fill('haiku')
    assert 'Haiku' in page.locator('#model-prices-rows').inner_text()
    page.get_by_label('Входных токенов на запрос').fill('120000')
    page.get_by_role('button',name='Рассчитать',exact=True).click()
    page.wait_for_function('calls.length===2')
    assert page.evaluate('calls[1].args.input_tokens===120000 && !calls[1].args.refresh')
    assert '$0.5' in page.locator('#model-prices-rows').inner_text()
    assert '$0.0625' in page.locator('#model-prices-rows').inner_text()
    assert page.locator('#model-prices-sources a').count()==4
    page.get_by_label('Провайдер',exact=True).select_option('')
    page.get_by_label('Поиск',exact=True).fill('')
    page.screenshot(path='/tmp/dewiar-model-prices-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path='/tmp/dewiar-model-prices-mobile.png',full_page=True)
    page.evaluate("() => {core.call=async()=>{throw Error('Нет сети')}}")
    page.get_by_role('button',name='Обновить из интернета').click()
    page.wait_for_function("document.querySelector('#model-prices-warning').textContent==='Нет сети'")
    assert not page.get_by_role('button',name='Обновить из интернета').is_disabled()
    assert page.locator('#model-prices-rows tr').count()>0
    assert not errors,errors
    browser.close()
print('PASS: comparison panel, Claude, filters, estimate parameters, source links, network error, mobile')
