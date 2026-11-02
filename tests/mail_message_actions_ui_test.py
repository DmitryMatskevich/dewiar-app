"""Mail detail actions: copy, trash confirmation, and editable forward draft."""
import os
import re
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
markup = re.sub(r'<script\b[^>]*>.*?</script>', '', (ROOT / "ui/settings.html").read_text(), flags=re.S | re.I)
markup = re.sub(r'<link\b[^>]*>', '', markup, flags=re.I)
with tempfile.TemporaryDirectory() as folder, sync_playwright() as pw:
    path = Path(folder) / "settings.html"
    path.write_text(markup, encoding="utf-8")
    browser = getattr(pw, os.environ.get("DEWIAR_TEST_BROWSER", "chromium")).launch()
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script("Object.defineProperty(navigator,'clipboard',{value:{writeText:async value=>window.copied=value}})")
    page.goto(path.as_uri())
    page.add_script_tag(content="""
      window.$=(selector,root=document)=>root.querySelector(selector);
      window.toast=message=>window.lastToast=message; window.ask=async(question,opts)=>{window.lastAsk=[question,opts];return true;};
      window.calls=[]; window.sentDraft=null; window.mailDraft={id:'draft-1',revision:1,state:'draft',kind:'forward',from:'d@dcnt.ru',to:[],cc:[],
        subject:'Fwd: Заявка',body:'Пересланное письмо',forwarded_original:true,original:{from:'Client',subject:'Заявка',text:'Текст'}};
      const accounts=[{id:'dcnt',address:'d@dcnt.ru',label:'DCNT',enabled:true,configured:true,has_password:true,credential_state:'ok',needs_password:false}];
      window.core={connected:true,on(){return this;},async call(cmd,args={}){calls.push([cmd,args]);
        if(cmd==='mail_accounts')return accounts;
        if(cmd==='mail_guess')return {smtp_host:'smtp.test',smtp_port:465,smtp_security:'ssl',imap_host:'imap.test',imap_port:993};
        if(cmd==='mail_rules_get')return {rules:{accounts,importance_rules:[]}};
        if(cmd==='mail_list')return [];
        if(cmd==='mail_open')return {id:'dcnt:77:42',account_id:'dcnt',account:'d@dcnt.ru',from:'Client <client@example.org>',
          to:'me@example.org',cc:'',subject:'Заявка',date:'2026-11-02',text:'Текст письма'};
        if(cmd==='mail_forward_prepare')return mailDraft;
        if(cmd==='mail_reply_revise'){mailDraft={...mailDraft,...args.patch,revision:args.revision+1,
          to:args.patch.to===undefined?mailDraft.to:args.patch.to.split(/[;,\\s]+/).filter(Boolean),
          cc:args.patch.cc===undefined?mailDraft.cc:args.patch.cc.split(/[;,\\s]+/).filter(Boolean)};return mailDraft;}
        if(cmd==='mail_reply_send'){sentDraft=args;return {from:'d@dcnt.ru',sent_copy:'appended'};}
        if(cmd==='mail_delete')return {state:'moved',folder:'Trash'};
        return {};
      }};
    """)
    page.add_script_tag(content=(ROOT / "ui/studio/mail.js").read_text())
    page.locator('nav button[data-p="mail"]').click()
    page.locator("#ml-message-subject")
    page.evaluate("mlOpen('dcnt:77:42')")
    page.wait_for_function("document.getElementById('ml-detail').hidden===false")
    page.click("#ml-copy-message")
    page.wait_for_function("window.copied && window.copied.includes('Текст письма')")
    assert "скопировано" in page.evaluate("lastToast").lower()
    page.click("#ml-forward-message")
    page.wait_for_function("document.getElementById('ml-forward-fields').hidden===false")
    assert "файлом .eml" in page.locator("#ml-forward-attachments").text_content()
    page.fill("#ml-draft-to", "client@example.org")
    page.fill("#ml-draft-body", "Проверьте, пожалуйста.")
    page.click("#ml-draft-update")
    page.wait_for_function("calls.some(call=>call[0]==='mail_reply_revise' && call[1].patch.to==='client@example.org')")
    assert "client@example.org" in page.locator("#ml-draft-meta").text_content()
    page.click("#ml-draft-send")
    page.wait_for_function("calls.some(call=>call[0]==='mail_reply_send')")
    assert page.evaluate("sentDraft.confirmed") is True
    page.click("#ml-delete-message")
    page.wait_for_function("calls.some(call=>call[0]==='mail_delete')")
    assert "Корзину" in page.evaluate("lastAsk[0]")
    assert page.locator("#ml-detail").is_hidden()
    assert not errors, errors
    browser.close()
print("OK: copy to clipboard, editable .eml forward with confirmation, and delete to Trash")
