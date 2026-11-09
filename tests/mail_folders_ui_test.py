"""Mail folder navigation renders standard/custom folders and opens the selected mailbox."""
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
html = re.sub(r'<script\b[^>]*>.*?</script>', '', (ROOT / "ui/settings.html").read_text(), flags=re.S | re.I)
html = re.sub(r'<link\b[^>]*>', '', html, flags=re.I)
with sync_playwright() as pw:
    browser = getattr(pw, "chromium").launch()
    page = browser.new_page(viewport={"width": 430, "height": 850})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_content(html)
    page.add_script_tag(content='''
      window.$=(selector,root=document)=>root.querySelector(selector);
      window.folderSync=[]; window.mailLists=[];
      window.core={connected:true,on(){return this},async call(cmd,args={}){
        if(cmd==='mail_accounts')return [{id:'podolsk',label:'Склад Подольск',address:'dm@example.org',enabled:true,configured:true,has_password:true}];
        if(cmd==='mail_guess')return {};
        if(cmd==='mail_rules_get')return {rules:{schema_version:2,accounts:[],importance_rules:[],website_forms:[],reply_preferences:{confirmed_self_addresses:[]}}};
        if(cmd==='mail_folders')return [
          {id:'INBOX',name:'Входящие',standard:true}, {id:'SENT',name:'Отправленные',standard:true},
          {id:'REPLIES',name:'Ответы',standard:true,virtual:true}, {id:'DRAFTS',name:'Черновики',standard:true},
          {id:'JUNK',name:'Спам',standard:true}, {id:'TRASH',name:'Удалённые',standard:true},
          {id:'F:Q2xpZW50cw',name:'Клиенты',standard:false}];
        if(cmd==='mail_folder_sync'){folderSync.push(args);return {};}
        if(cmd==='mail_list'){mailLists.push(args);return [];}
        if(cmd==='settings_save')return {ui:{mail_badge_filters:{important:false,unread:false,unanswered:false}}};
        return {};
      }};
      window.toast=()=>{};
    ''')
    page.add_script_tag(content=(ROOT / "ui/studio/mail.js").read_text())
    page.locator('nav button[data-p="mail"]').click()
    page.wait_for_function("mlFolders.length===7")
    folder_buttons = page.locator("#ml-folders .ml-folder")
    assert folder_buttons.count() == 7
    assert folder_buttons.nth(0).inner_text() == "Входящие"
    assert folder_buttons.nth(0).locator("svg").count() == 1
    folder_buttons.filter(has_text="Спам").click()
    page.wait_for_function("folderSync.at(-1).folder_id==='JUNK' && mailLists.at(-1).folder_id==='JUNK'")
    assert page.locator("#ml-view-title").inner_text() == "Склад Подольск · Спам"
    folder_buttons.filter(has_text="Клиенты").click()
    page.wait_for_function("folderSync.at(-1).folder_id==='F:Q2xpZW50cw'")
    assert page.locator("#ml-view-title").inner_text() == "Склад Подольск · Клиенты"
    assert not errors, errors
    browser.close()
print("OK: standard folders, virtual replies, user folders, icons and selected-folder loading")
