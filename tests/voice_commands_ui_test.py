"""Studio voice editor: render, search, edit, preview and save; desktop tools are not called."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import voice_commands as vc
from settings import DEFAULTS
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'work/voice-commands'; OUT.mkdir(parents=True, exist_ok=True)
fixture = {'config': vc.config(), 'defaults': vc.config(), 'installed': [{'name': 'Claude', 'target': 'Claude'}]}
script = """(() => {
  window.pywebview = {api: new Proxy({}, {get: () => () => Promise.resolve(true)})};
  const ready = () => { if (!window.core) return setTimeout(ready, 5);
    core.retry = () => {}; core.connect = () => {};
    if (core.ws) {core.ws.onclose = null; core.ws.onerror = null; core.ws.close();}
    window.calls = []; window.testSettings = SETTINGS;
    core.call = async (cmd, args = {}) => {
      calls.push([cmd, args]);
      if (cmd === 'voice_commands') return FIXTURE;
      if (cmd === 'tools') return [];
      if (cmd === 'voice_command_test') return await window.testPreview(args);
      if (cmd === 'settings_save') {
        await window.validateCommands(args.patch.voice_commands);
        window.savedCommands = JSON.parse(JSON.stringify(args.patch.voice_commands));
        testSettings.voice_commands = savedCommands; return testSettings;
      }
      return {};
    };
  }; ready();
})();""".replace('SETTINGS', json.dumps(DEFAULTS, ensure_ascii=False)).replace('FIXTURE', json.dumps(fixture, ensure_ascii=False))

with sync_playwright() as pw:
    browser = getattr(pw, os.environ.get('DEWIAR_TEST_BROWSER', 'chromium')).launch()
    page = browser.new_page(viewport={'width': 1300, 'height': 950})
    errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
    page.expose_function('validateCommands', vc.validate)
    def preview(args):
        vc.validate(args['config']); r = vc.parse(args['text'], args['config'])
        return dict(r, allowed=True, confirm=False) if r else None
    page.expose_function('testPreview', preview)
    page.add_init_script(script)
    page.goto((ROOT / 'ui/settings.html').as_uri() + '?port=65534')
    page.click('nav button[data-p="hands"]'); page.click('#hands-tab-commands')
    page.wait_for_function("document.querySelectorAll('#vc-actions details').length === 7")
    assert page.locator('#hands-abilities').is_hidden()
    page.fill('#vc-search', 'клод')
    assert page.locator('#vc-apps details').count() == 1
    page.locator('#vc-apps summary').click()
    aliases = page.locator('#vc-apps textarea'); aliases.fill('Клод\nклод код\nмой клод')
    page.fill('#vc-test-text', 'открой мой клод'); page.click('#vc-test')
    page.wait_for_function("document.querySelector('#vc-test-result').textContent.includes('Claude')")
    assert not page.evaluate("calls.some(c => ['control_mac_app', 'yandex_music'].includes(c[0]))")
    page.click('#vc-save'); page.wait_for_function("document.querySelector('#vc-status').textContent.startsWith('Сохранено')")
    assert 'мой клод' in page.evaluate('savedCommands.applications[0].aliases')
    aliases.fill('кодекс'); page.click('#vc-save')
    page.wait_for_function("document.querySelector('#vc-status').textContent.startsWith('Не сохранено')")
    assert 'мой клод' in page.evaluate('savedCommands.applications[0].aliases')
    aliases.fill('Клод\nклод код\nмой клод')
    page.locator('#vc-apps select').select_option('Claude')
    page.fill('#vc-search', ''); page.select_option('#vc-filter', 'actions')
    page.locator('#vc-actions summary').first.click()
    phrases = page.locator('#vc-actions textarea').first
    phrases.fill('покажи мне {приложение}')
    page.fill('#vc-test-text', 'покажи мне клод'); page.click('#vc-test')
    page.wait_for_function("document.querySelector('#vc-test-result').textContent.includes('Claude')")
    page.fill('#vc-test-text', 'включи песню Земфира — Хочешь'); page.click('#vc-test')
    page.wait_for_function("document.querySelector('#vc-test-result').textContent.includes('Земфира — Хочешь')")
    phrases.fill('покажи мне {приложение}\nоткрой {приложение}')
    for phrase, expected in [('открой таблицу','Numbers'),('открой офис','LibreOffice'),('открой mail','Mail'),('открой контакт','Contacts'),('переговоры','Фокусировка macOS'),('выключи не беспокоить','Выключить')]:
        page.fill('#vc-test-text', phrase); page.click('#vc-test')
        page.wait_for_function("expected=>document.querySelector('#vc-test-result').textContent.includes(expected)",arg=expected)
        assert 'undefined' not in page.locator('#vc-test-result').inner_text()
    page.evaluate("showHelp('hands')")
    help_text=page.locator('#help-body').inner_text()
    required=['Numbers','LibreOffice','Contacts','переговоры','Set Focus','цельные фразы']
    assert not [word for word in required if word not in help_text], help_text
    page.evaluate('helpClose()')
    assert not page.evaluate("calls.some(c => ['control_mac_app', 'yandex_music'].includes(c[0]))")
    page.select_option('#vc-filter', 'all')
    page.evaluate("document.querySelector('main').scrollTop = 0")
    page.evaluate("document.documentElement.classList.add('st-light')")
    page.screenshot(path=str(OUT / 'light.png'), full_page=True)
    page.evaluate("document.documentElement.classList.remove('st-light')")
    page.screenshot(path=str(OUT / 'dark.png'), full_page=True)
    page.set_viewport_size({'width': 760, 'height': 900})
    assert page.evaluate("document.querySelector('#hands-commands').scrollWidth <= document.querySelector('#hands-commands').clientWidth + 1")
    page.screenshot(path=str(OUT / 'narrow.png'), full_page=True)
    assert not errors, errors
    browser.close()
print('OK: voice settings UI, search, editing, validation, preview, save; light/dark/narrow layouts')
