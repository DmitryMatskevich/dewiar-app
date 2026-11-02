"""A Studio handshake/reconnect activates Mail only when its section is visible."""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
html=re.sub(r'<script\b[^>]*>.*?</script>', '', (ROOT/'ui/settings.html').read_text(), flags=re.S|re.I)
html=re.sub(r'<link\b[^>]*>', '', html, flags=re.I)
with sync_playwright() as pw:
    browser=getattr(pw,os.environ.get('DEWIAR_TEST_BROWSER','chromium')).launch()
    page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(html)
    page.add_script_tag(content="""
      window.$=(selector,root=document)=>root.querySelector(selector);
      window.mailCalls=[];window.mailHandlers={};
      window.core={connected:true,on(name,fn){(mailHandlers[name]??=[]).push(fn);return this;},
        async call(cmd){mailCalls.push(cmd);return cmd==='mail_rules_get'?{rules:{}}:[];}};
      window.fireHello=()=>mailHandlers.hello.forEach(fn=>fn());
    """)
    page.add_script_tag(content=(ROOT/'ui/studio/mail.js').read_text())
    page.evaluate('fireHello()')
    assert page.evaluate('mailCalls.length')==0
    page.evaluate("document.querySelector('#p-mail').classList.add('cur')")
    page.locator('nav button[data-p="mail"]').click()
    page.wait_for_function("mailCalls.includes('mail_list')")
    before=page.evaluate('mailCalls.length')
    page.evaluate("document.querySelector('#p-mail').classList.remove('cur'); fireHello()")
    assert page.evaluate('mailCalls.length')==before
    assert not errors,errors
    browser.close()
print('OK: Studio startup and reconnect do not request Mail; opening Mail loads accounts')
