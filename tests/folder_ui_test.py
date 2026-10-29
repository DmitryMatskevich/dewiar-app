"""Контракт выбора рабочей папки в разделе «Папка»."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
settings = (ROOT / "ui" / "settings.html").read_text(encoding="utf-8")
folder = (ROOT / "ui" / "studio" / "folder.js").read_text(encoding="utf-8")
help_src = (ROOT / "ui" / "assets" / "help.js").read_text(encoding="utf-8")
commands = (ROOT / "core" / "engine_commands.py").read_text(encoding="utf-8")


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print("✅ " + message)


check('id="f-root"' in settings and 'Настроено на папку' in settings,
      "текущий путь показан с подписью «Настроено на папку»")
check('id="f-change"' in settings and 'Изменить папку' in settings,
      "в разделе есть отдельная кнопка изменения рабочей папки")
check('aria-live="polite"' in settings,
      "изменившийся путь доступен VoiceOver как статус")
check('window.api && window.api()' in folder and 'pick_folder' in folder,
      "выбор каталога выполняется системным диалогом macOS")
check('core.call("ws_set_path", { path })' in folder and 'await loadFolder()' in folder,
      "выбранная папка сохраняется в ядре и дерево перечитывается")
check('"Знания» переиндексируются в фоне' in settings or 'переиндексируются в фоне' in settings,
      "интерфейс объясняет фоновую переиндексацию")
check('os.access(candidate, os.W_OK)' in commands and 'old_path' in commands and 'self.s.update({"workspace": {"path": old_path}})' in commands,
      "ядро проверяет доступ к каталогу и откатывает настройку при ошибке")
check('Изменить папку' in help_src and 'Настроено на папку' in help_src,
      "справка описывает текущий путь и смену папки")
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto((ROOT / "ui" / "settings.html").as_uri() + "?port=1")
    page.locator('nav button[data-p="folder"]').click()
    page.wait_for_selector("#f-change", state="visible")
    page.evaluate("""() => {
      window.__folderCalls = [];
      window.api = () => ({pick_folder: async () => "/tmp/New Dewiar"});
      window.core.call = async (cmd, args) => {
        window.__folderCalls.push([cmd, args]);
        if (cmd === "ws_set_path") return "/tmp/New Dewiar";
        if (cmd === "ws_tree") return {root: "/tmp/New Dewiar", items: [], obsidian: {connected: false}};
        return {};
      };
    }""")
    page.locator("#f-change").click()
    page.wait_for_function("document.querySelector('#f-root').textContent === '/tmp/New Dewiar'")
    calls = page.evaluate("window.__folderCalls")
    check(any(c[0] == "ws_set_path" and c[1]["path"] == "/tmp/New Dewiar" for c in calls),
          "кнопка реально передаёт выбранную папку ядру")
    check(page.locator("#f-change").is_enabled(), "после выбора кнопка снова доступна")
    browser.close()
print("OK: выбор рабочей папки")
