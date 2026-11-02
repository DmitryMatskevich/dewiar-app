"""Studio → Почта: ящик без рабочего пароля просит пароль в самом окне Dewiar (как Apple Mail), а не окном macOS."""
import os
import re
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
html = re.sub(r'<script\b[^>]*>.*?</script>', '', (ROOT / 'ui/settings.html').read_text(), flags=re.S | re.I)
html = re.sub(r'<link\b[^>]*>', '', html, flags=re.I)
with tempfile.TemporaryDirectory() as folder, sync_playwright() as pw:
    page_file = Path(folder) / 'settings.html'
    page_file.write_text(html, encoding='utf-8')
    browser = getattr(pw, os.environ.get('DEWIAR_TEST_BROWSER', 'chromium')).launch()
    page = browser.new_page(); errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(page_file.as_uri() + '?p=mail&attention=1')
    page.add_script_tag(content="""
      window.$=(selector,root=document)=>root.querySelector(selector);
      window.toast=()=>{}; window.ask=async()=>true;
      window.calls=[]; window.unlocked=false; window.saveRejects=true;
      const acc=(state)=>[{id:'gmail',address:'me@gmail.com',label:'Gmail',enabled:true,configured:true,has_password:true,
          credential_state:'ok',needs_password:false},
        {id:'dcnt',address:'d@dcnt.ru',label:'Dcnt',enabled:true,configured:true,has_password:true,
          credential_state:state,needs_password:state!=='ok'}];
      window.core={connected:true,on(){return this;},async call(cmd,args){calls.push([cmd,args]);
        if(cmd==='mail_accounts')return acc(unlocked?'ok':'needs_access');
        if(cmd==='mail_rules_get')return {rules:{importance_rules:[]}};
        if(cmd==='mail_guess')return {};
        if(cmd==='mail_account_unlock'){unlocked=true;return {test:{smtp:'ok',imap:'ok'}};}
        if(cmd==='mail_account_save')return saveRejects?{test:{smtp:'не принят',imap:'не принят',auth_rejected:true},password_saved:false}
                                                        :{test:{smtp:'ok',imap:'ok'},password_saved:true};
        return [];}};
    """)
    page.add_script_tag(content=(ROOT / 'ui/studio/mail.js').read_text())
    page.evaluate("document.querySelector('#p-mail').classList.add('cur')")
    page.locator('nav button[data-p="mail"]').click()
    page.wait_for_function("document.activeElement && document.activeElement.id==='ml-pass'")
    assert page.locator('#ml-selected-address').text_content() == 'd@dcnt.ru'
    assert 'Связке ключей' in page.locator('#ml-res').text_content()
    assert page.locator('#ml-pass').get_attribute('placeholder') == 'введите пароль приложения заново'
    assert page.locator('#ml-unlock').is_visible()
    # Неверный новый пароль: сообщение и снова поле ввода; сохранённый не трогается (это решает ядро).
    page.fill('#ml-pass', 'typo')
    page.click('#ml-save')
    page.wait_for_function("document.getElementById('ml-res').textContent.includes('не сохранён')")
    # «Разрешить доступ» — одна команда ядру, затем синхронизация.
    page.click('#ml-unlock')
    page.wait_for_function("calls.some(c=>c[0]==='mail_sync')")
    assert page.locator('#ml-unlock').is_hidden()
    assert not errors, errors
    browser.close()
print('OK: mailbox without a working password asks inside Dewiar, rejected password is not saved, unlock is one action')
