"""Телефон: ответ не обрезается из-за размышлений модели. Ловит случай, когда облако Dewiar на телефоне всегда «думает»
при малом лимите токенов (700) — размышление съедает лимит, ответ обрывается.
Проверяет: «почему…» и простые — сразу, без размышлений; сложная просьба (стих, задача, анализ, план) — думает, лимит с запасом;
ушла в размышления и ничего не сказала — повтор без них; сервер не понимает chat_template_kwargs — дальше их не шлём;
переключатель «Думать над сложными вопросами» в «Мозге»."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
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
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(); errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(800)
    R = pg.evaluate("""async () => {
        Object.assign(S.llm, { kind: "openai", base_url: "https://dewiar.pro/dwrapp/cloud.php?p=", api_key: "dw-x", model: "chat", cloud: true, disable_thinking: false });
        S.onboarded = true;
        window.resolveModel = async () => "chat";
        const seen = [], NL = String.fromCharCode(10, 10);
        let mode = "ok";
        const sse = (o) => "data: " + JSON.stringify(o) + NL;
        window.http = async (m, u, o) => {
          if (!o.onChunk) return { status: 200, body: "{}" };
          const body = typeof o.body === "string" ? JSON.parse(o.body) : o.body; seen.push(body);
          if (mode === "kwargs400" && body.chat_template_kwargs) return { status: 400, body: JSON.stringify({ error: { message: "unexpected chat_template_kwargs" } }) };
          if (mode === "reason" && !seen.slice(0, -1).some(x => x.retryMark) && (body.retryMark = true)) {
            o.onChunk(sse({ choices: [{ delta: { reasoning_content: "Хм, пользователь спрашивает…" } }] }) + sse({ choices: [{ delta: {}, finish_reason: "length" }] }) + "data: [DONE]" + NL);
            return { status: 200, body: "" };
          }
          o.onChunk(sse({ choices: [{ delta: { content: "Потому что рэлеевское рассеяние сильнее для коротких волн." } }] }) + sse({ choices: [{ delta: {}, finish_reason: "stop" }] }) + "data: [DONE]" + NL);
          return { status: 200, body: "" };
        };
        const wait = async () => { for (let i = 0; i < 100 && busy; i++) await new Promise(r => setTimeout(r, 50)); await new Promise(r => setTimeout(r, 100)); };
        const out = {};
        await ask("Почему небо голубое?"); await wait(); out.deep = seen[seen.length - 1];
        await ask("Сколько будет два плюс два"); await wait(); out.simple = seen[seen.length - 1];
        await ask("Напиши стих про осень с рифмой"); await wait(); out.poem = seen[seen.length - 1];
        mode = "reason"; const n0 = seen.length;
        await ask("Объясни, как работает фотосинтез"); await wait();
        out.retry = seen.slice(n0).map(x => [x.chat_template_kwargs, x.max_tokens]); out.retryText = document.querySelector("#bot").textContent;
        mode = "kwargs400"; const n1 = seen.length;
        await ask("Почему трава зелёная?"); await wait();
        out.k400 = seen.slice(n1).map(x => !!x.chat_template_kwargs); out.noKwargs = S.llm.no_kwargs;
        mode = "ok"; const n2 = seen.length;
        await ask("Почему море солёное?"); await wait(); out.after = seen.slice(n2).map(x => !!x.chat_template_kwargs);
        S.llm.no_kwargs = false; S.llm.think = "off"; const n3 = seen.length;
        await ask("Почему луна не падает?"); await wait(); out.off = seen[n3] && seen[n3].chat_template_kwargs;
        const n4 = seen.length; await ask("Напиши стих про зиму"); await wait(); out.poemOff = seen[n4] && seen[n4].chat_template_kwargs;
        delete S.llm.think;
        out.sys = seen[0].messages[0].content;
        return out;
    }""")
    b.close()
ok(R["deep"]["chat_template_kwargs"] == {"enable_thinking": False} and R["deep"]["max_tokens"] >= 2048, f"«почему…» — без размышлений, лимит не меньше 2048 (а не 700; с инструментами — 6144 под целую программу): {R['deep'].get('chat_template_kwargs')} {R['deep'].get('max_tokens')}")
ok(R["simple"]["chat_template_kwargs"] == {"enable_thinking": False}, "простой вопрос — тоже без размышлений")
ok(len(R["retry"]) == 2 and R["retry"][1][0] == {"enable_thinking": False} and R["retry"][1][1] == 4096 and "рэлеевское" in R["retryText"],
   f"ушла в размышления и упёрлась в лимит — повтор без них, ответ есть: {R['retry']}")
ok(R["k400"][0] and R["k400"][-1] is False and R["noKwargs"] is True and R["after"] == [False], f"сервер не понимает chat_template_kwargs — повтор без них и больше не шлём: {R['k400']} {R['after']}")
ok(R["poem"]["chat_template_kwargs"] == {"enable_thinking": True} and R["poem"]["max_tokens"] >= 6144,
   f"сложная просьба (стих) — модель думает, лимит с запасом: {R['poem'].get('chat_template_kwargs')} {R['poem'].get('max_tokens')}")
ok(R["off"] == {"enable_thinking": False} and R["poemOff"] == {"enable_thinking": False}, "переключатель выключен (think: off) — не думает даже над стихом")
ok("одним-тремя" not in R["sys"] and "четыре-восемь предложений" in R["sys"], "правила речи: на настоящие вопросы — содержательный ответ, а не одна фраза")
ok(not errors, f"без ошибок на странице {errors}")
print("ИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
