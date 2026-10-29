"""The Models screen exposes independent communication/planning/coding queues."""
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route(re.compile(r"^https?://"), lambda route: route.abort())
        page.add_init_script("""
          window.__calls = [];
          window.pywebview = {api:{}};
          const wait = () => { if (!window.core) return setTimeout(wait, 5);
            core.retry = () => {}; core.connect = () => {};
            if (core.ws) { core.ws.onclose = null; core.ws.close(); }
            core.call = async (name, args) => {
              window.__calls.push({name, args});
              if (name === 'settings_save') return Object.assign({}, window.S, args.patch);
              if (name === 'models_routing_status') return {role: args.role, next: null, profiles: [], timezone: 'UTC'};
              return {};
            };
          }; wait();
        """)
        page.goto((ROOT / "ui/settings.html").as_uri() + "?port=1")
        page.wait_for_function("!!window.renderProfiles && !!window.refreshHelpTitles")
        page.evaluate("""
          () => { document.querySelectorAll('main section').forEach(s => s.classList.toggle('cur', s.id === 'p-brain')); S = {llm:{active:'chat', auto_select:true, profiles:{
            chat:{name:'Чат', model:'chat', kind:'openai', priority:2, roles:['communication']},
            legacy:{name:'Старая модель', model:'legacy', kind:'ollama', priority:3},
            plan:{name:'План', model:'plan', kind:'openai', priority:1, roles:['planning']},
            code:{name:'Код', model:'code', kind:'openai', priority:1, roles:['coding']},
            spare:{name:'Свободная', model:'spare', kind:'openai', priority:9, roles:[]}
          }}, ui:{}, privacy:{}, tools:{}}; renderProfiles(); }
        """)
        assert not errors, errors
        assert page.locator("[data-model-role]").count() == 3
        assert page.locator("[data-model-role='communication']").get_attribute("aria-selected") == "true"
        assert page.locator("#profiles .prof").count() == 2
        assert "Чат" in page.locator("#profiles").inner_text()
        assert "Старая модель" in page.locator("#profiles").inner_text()
        assert "План" not in page.locator("#profiles .head .t").all_inner_texts()
        assert page.locator("#model-role-add").is_visible()
        assert page.locator("#model-role-max-tokens").input_value() == "1024"

        page.locator("[data-model-role='planning']").click()
        assert page.locator("[data-model-role='planning']").get_attribute("aria-selected") == "true"
        assert page.locator("#profiles .prof").count() == 1
        assert "План" in page.locator("#profiles").inner_text()
        assert "Чат" not in page.locator("#profiles .head .t").all_inner_texts()
        assert page.locator("#model-role-title").inner_text() == "Планирование"
        assert page.locator("#model-role-max-tokens").input_value() == "16384"

        page.locator("[data-model-role='coding']").click()
        assert page.locator("#profiles .prof").count() == 1
        assert "Код" in page.locator("#profiles").inner_text()
        assert page.locator("#model-role-description").inner_text().startswith("Разработка программ")

        page.locator("[data-model-role='planning']").click()
        page.locator("#model-role-add-select").select_option("spare")
        page.locator("#model-role-add-button").click()
        page.wait_for_timeout(50)
        assert any(c["name"] == "settings_save" and "planning" in c["args"]["patch"]["llm"]["profiles"]["spare"]["roles"] for c in page.evaluate("window.__calls"))
        assert "Свободная" in page.locator("#profiles").inner_text()

        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    main()
    print("OK: three model role tabs filter profiles and assign existing models")
