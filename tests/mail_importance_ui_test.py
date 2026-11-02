"""Mail importance editor: sender rules, subject modes, priority changes and removal."""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
html = re.sub(r'<script\b[^>]*>.*?</script>', '', (ROOT / "ui/settings.html").read_text(), flags=re.S | re.I)
html = re.sub(r'<link\b[^>]*>', '', html, flags=re.I)
with sync_playwright() as pw:
    browser = getattr(pw, os.environ.get("DEWIAR_TEST_BROWSER", "chromium")).launch()
    page = browser.new_page(viewport={"width": 1100, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_content(html)
    page.add_script_tag(content='''
      window.$=(selector,root=document)=>root.querySelector(selector);
      window.saved=[];
      window.savedBadgeMode='unread';
      window.core={connected:true,on(){return this},async call(cmd,args={}){
        if(cmd==='mail_accounts')return [];
        if(cmd==='mail_rules_get')return {rules:{schema_version:2,accounts:[],important_contacts:[],important_recipient_groups:[],importance_rules:[],website_forms:[],reply_preferences:{confirmed_self_addresses:[]}}};
        if(cmd==='mail_rules_save'){saved.push(args.rules);return {rules:args.rules};}
        if(cmd==='settings_save'){savedBadgeMode=args.patch.ui.mail_badge_mode;return {ui:{mail_badge_mode:savedBadgeMode}};}
        if(cmd==='mail_list')return [];
        return {};
      }};
      window.toast=()=>{};
    ''')
    page.add_script_tag(content=(ROOT / "ui/studio/mail.js").read_text())
    page.locator('nav button[data-p="mail"]').click()
    page.wait_for_function("typeof mlRulesData==='object' && document.getElementById('ml-rules').value.length>0")
    page.locator("#ml-badge-mode").select_option("important")
    page.wait_for_function("savedBadgeMode==='important'")
    page.locator("#p-mail details.ml-settings").nth(1).locator("summary").click()
    page.locator("#ml-ir-sender").fill("client@example.org")
    page.locator("#ml-ir-subject").fill("Оплата")
    page.locator("#ml-ir-add").click()
    assert "Заполните только одно поле" in page.locator("#ml-ir-state").inner_text()
    page.locator("#ml-ir-subject").fill("")
    page.locator("#ml-ir-add").click()
    page.wait_for_function("mlRulesData.importance_rules.length===1", timeout=5000)
    rule = page.evaluate("mlRulesData.importance_rules[0]")
    assert rule["sender_address"] == "client@example.org" and rule["importance"] == "important"
    page.locator("#ml-importance-rules input[type=checkbox]").uncheck()
    page.wait_for_function("saved.at(-1).importance_rules[0].enabled===false")
    page.locator("#ml-importance-rules select").select_option("normal")
    page.wait_for_function("mlRulesData.importance_rules[0].importance==='normal'")
    page.locator("#ml-importance-rules button").click()
    page.wait_for_function("mlRulesData.importance_rules.length===0")
    page.locator("#ml-ir-subject").fill("Счёт №42")
    page.locator("#ml-ir-match").select_option("exact")
    page.locator("#ml-ir-add").click()
    page.wait_for_function("mlRulesData.importance_rules.length===1")
    rule = page.evaluate("mlRulesData.importance_rules[0]")
    assert rule["subject"] == "Счёт №42" and rule["subject_match"] == "exact"
    page.locator("#ml-ir-subject").fill("")
    page.locator("#ml-ir-recipient").fill("team@example.org")
    page.locator("#ml-ir-add").click()
    page.wait_for_function("mlRulesData.importance_rules.length===2")
    assert page.evaluate("mlRulesData.importance_rules[1].recipient_address") == "team@example.org"
    assert not errors, errors
    browser.close()
print("OK: importance editor validates one criterion, saves sender and exact-subject rules, toggles, reprioritizes and removes")
