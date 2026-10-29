"""Телефон: «Кодер, сделай калькулятор» — ловит случай, когда модель сказала «Готово», а где забрать файл, непонятно.
Модель делает программу инструментом make_app — она сразу открывается на экране и работает, лежит в «Мой день» →
«Сделанное», сохраняется в «Загрузки». Сказала «Готово, калькулятор сделан», не сделав, — одно напоминание, и делает.
Оборванный HTML — модели честная ошибка, а не «готово». Скриншоты — в папку из аргумента."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


www = Path(tempfile.mkdtemp()) / "www"
shutil.copytree(ROOT / "android/assets/www", www)
shutil.copy(ROOT / "ui/assets/entities.js", www / "entities.js")
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)   # живое лицо Эвы
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
CALC = """<!doctype html><html lang="ru"><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{margin:0;background:#15142a;color:#fff;font:20px sans-serif;display:flex;flex-direction:column;height:100vh}
#d{padding:24px;font-size:42px;text-align:right}#k{flex:1;display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:8px}
button{font-size:26px;border:0;border-radius:14px;background:#2a2850;color:#fff}</style></head><body><div id="d">0</div><div id="k"></div>
<script>let e="";const d=document.getElementById("d");for(const c of "789/456*123-0.=+C"){const b=document.createElement("button");b.textContent=c;
b.onclick=()=>{if(c==="C")e="";else if(c==="=")e=String(Function("return "+e)());else e+=c;d.textContent=e||"0"};k.appendChild(b)}</script></body></html>"""
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 393, "height": 852}, device_scale_factor=2); errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(800)
    R = pg.evaluate("""async (CALC) => {
        Object.assign(S.llm, { kind: "openai", base_url: "https://dewiar.pro/dwrapp/cloud.php?p=", api_key: "dw-x", model: "chat", cloud: true });
        S.onboarded = true; S.persona = "coder"; window.resolveModel = async () => "chat";
        const NL = String.fromCharCode(10, 10), seen = [];
        const sse = (o) => "data: " + JSON.stringify(o) + NL;
        let mode = "tool";
        const call = (html) => sse({ choices: [{ delta: { tool_calls: [{ index: 0, id: "c1", function: { name: "make_app", arguments: JSON.stringify({ title: "Калькулятор", html }) } }] } }] });
        window.http = async (m, u, o) => {
          if (!o.onChunk) return { status: 200, body: "{}" };
          const body = typeof o.body === "string" ? JSON.parse(o.body) : o.body; seen.push(body);
          const last = body.messages[body.messages.length - 1];
          let out;
          if (last.role === "tool") out = sse({ choices: [{ delta: { content: last.content.startsWith("Ошибка") ? "Не вышло: HTML оборвался." : "Готово — калькулятор открыт на экране." } }] });
          else if (mode === "lie" && !String(last.content).startsWith("СТОП")) out = sse({ choices: [{ delta: { content: "Готово, калькулятор сделан!" } }] });
          else if (mode === "cut") out = call(CALC.slice(0, 400));
          else out = call(CALC);
          o.onChunk(out + "data: [DONE]" + NL); return { status: 200, body: "" };
        };
        const wait = async () => { for (let i = 0; i < 200 && busy; i++) await new Promise(r => setTimeout(r, 50)); await new Promise(r => setTimeout(r, 200)); };
        const res = {};
        await ask("Сделай математический калькулятор"); await wait();
        res.tools = seen[0].tools.map(t => t.function.name); res.max = seen[0].max_tokens;
        res.view = !!document.querySelector("#appview.on iframe"); res.card = document.querySelector("#card").innerText;
        res.bot = document.querySelector("#bot").textContent;
        return res;
    }""", CALC)
    pg.wait_for_timeout(500)
    fr = pg.frame_locator("#appview iframe")
    for k in ("7", "+", "8", "="):
        fr.locator("button", has_text=k).first.click()
    R["calc"] = fr.locator("#d").inner_text()
    if SHOTS:
        pg.screenshot(path=str(SHOTS / "phone-app-open.png"))
    pg.click("#av-dl"); pg.wait_for_timeout(200)
    R["dl"] = pg.evaluate("(window._downloads || []).map(x => [x[0], x[1].length])")
    pg.click("#av-x"); pg.wait_for_timeout(200)
    R2 = pg.evaluate("""async () => {
        const res = { closed: !document.querySelector("#appview.on") };
        if (true) { renderDay(); }
        dayTab = "made"; openSheet("#sh-day"); renderDay();
        res.made = document.querySelector("#day-body").innerText;
        closeSheets(true);
        return res;
    }""")
    if SHOTS:
        pg.evaluate("dayTab = 'made'; $$('#day-tabs button').forEach(x => x.classList.toggle('on', x.dataset.t === 'made')); renderDay(); openSheet('#sh-day')")
        pg.wait_for_timeout(400)
        pg.screenshot(path=str(SHOTS / "phone-app-made.png"))
        pg.evaluate("closeSheets(true)")
    R3 = pg.evaluate("""async () => {
        const wait = async () => { for (let i = 0; i < 200 && busy; i++) await new Promise(r => setTimeout(r, 50)); await new Promise(r => setTimeout(r, 200)); };
        const n0 = apps().length; const seen = [];
        const http0 = window.http; let mode = "lie";
        window.http = async (m, u, o) => { const b = typeof o.body === "string" ? JSON.parse(o.body) : o.body; seen.push(b); return http0(m, u, o); };
        // «Готово!» без инструмента — переключаем заглушку на ложь
        window.__mode = "lie";
        return { n0 };
    }""")
    # ложное «готово» и оборванный HTML — отдельными прогонами с другой заглушкой
    def run(mode, text):
        return pg.evaluate("""async ([mode, text, CALC]) => {
            const NL = String.fromCharCode(10, 10), seen = [];
            const sse = (o) => "data: " + JSON.stringify(o) + NL;
            const call = (html) => sse({ choices: [{ delta: { tool_calls: [{ index: 0, id: "c1", function: { name: "make_app", arguments: JSON.stringify({ title: "Калькулятор", html }) } }] } }] });
            window.http = async (m, u, o) => {
              if (!o.onChunk) return { status: 200, body: "{}" };
              const body = typeof o.body === "string" ? JSON.parse(o.body) : o.body; seen.push(body);
              const last = body.messages[body.messages.length - 1];
              let out;
              if (last.role === "tool") { seen.toolResult = last.content; out = sse({ choices: [{ delta: { content: last.content.startsWith("Ошибка") ? "Не вышло: код оборвался, попробую короче." : "Готово — калькулятор открыт." } }] }); }
              else if (mode === "lie" && !String(last.content).startsWith("СТОП")) out = sse({ choices: [{ delta: { content: "Готово, калькулятор сделан!" } }] });
              else if (mode === "cut") out = call(CALC.slice(0, 400));
              else out = call(CALC);
              o.onChunk(out + "data: [DONE]" + NL); return { status: 200, body: "" };
            };
            const n0 = apps().length;
            await ask(text);
            for (let i = 0; i < 200 && busy; i++) await new Promise(r => setTimeout(r, 50));
            await new Promise(r => setTimeout(r, 200));
            const v = document.querySelector("#appview.on"); if (v) document.querySelector("#av-x").click();
            return { n: apps().length - n0, rounds: seen.length, nudged: seen.some(b => b.messages.some(m => String(m.content).startsWith("СТОП") && m.content.includes("make_app"))), tool: seen.toolResult || "", bot: document.querySelector("#bot").textContent };
        }""", [mode, text, CALC])
    L = run("lie", "Сделай калькулятор")
    C = run("cut", "Сделай калькулятор побольше")
    b.close()
ok("make_app" in R["tools"] and R["max"] >= 6000, f"у модели на телефоне есть make_app и лимит под целый HTML ({R['max']})")
ok(R["view"] and "Калькулятор" in R["card"], f"калькулятор сразу открыт на экране, в разговоре — карточка «Открыть / В «Загрузки»» ({R['bot']})")
ok(R["calc"] == "15", f"калькулятор работает: 7 + 8 = {R['calc']}")
ok(R["dl"] and R["dl"][0][0] == "Калькулятор.html" and R["dl"][0][1] > 500, f"«В «Загрузки»» — файл Калькулятор.html: {R['dl']}")
ok(R2["closed"] and "Калькулятор" in R2["made"], "закрыл — и он лежит в «Мой день» → «Сделанное»")
ok(L["nudged"] and L["n"] == 1, f"сказала «Готово», не сделав, — напоминание, и программа сделана ({L})")
ok(C["n"] == 0 and "оборвался" in C["tool"] and "Не вышло" in C["bot"], f"оборванный HTML — не «готово», а честная ошибка ({C['tool'][:60]})")
ok(not errors, f"без ошибок на странице {errors}")
print("ИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
