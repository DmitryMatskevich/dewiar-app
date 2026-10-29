"""Телефон: журнал отчёта говорит, что было в разговоре. Отчёт 0.4.13 с планшета Huawei — двадцать строк «слушаю: сервер»
и ничего больше: не понять, расслышал ли сервер, ответила ли модель и почему молчит голос. Теперь в журнале: что
услышано и за сколько (📝), вопрос и куда ушёл (❓), длина ответа и время (💬), ошибки распознавания, «голоса нет»."""
import os, shutil, subprocess, sys, tempfile
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
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 393, "height": 852}); errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(800)
    log = pg.evaluate("""async () => {
        Object.assign(S.llm, { kind: "openai", base_url: "https://dewiar.pro/dwrapp/cloud.php?p=", api_key: "dw-x", model: "chat", cloud: true });
        S.onboarded = true; window.resolveModel = async () => "chat";
        D.ttsReady = () => false; D.mock = false; voiceWarned = false;
        const NL = String.fromCharCode(10, 10);
        window.http = async (m, u, o) => {
          if (!o.onChunk) return { status: 200, body: "{}" };
          o.onChunk("data: " + JSON.stringify({ choices: [{ delta: { content: "Сейчас в Минске плюс двенадцать." } }] }) + NL + "data: [DONE]" + NL);
          return { status: 200, body: "" };
        };
        const n0 = PLOG.length;
        sttActive = true; sttVia = "server"; sttT0 = Date.now() - 1500; sttGen = 7;
        N.ev({ type: "stt_result", g: 7, text: "какая погода в минске" });
        for (let i = 0; i < 100 && (busy || sttActive); i++) await new Promise(r => setTimeout(r, 50));
        await new Promise(r => setTimeout(r, 300));
        sttActive = true; sttVia = "server"; sttGen = 8;
        N.ev({ type: "stt_error", g: 8, code: 3, msg: "HTTP 502" });
        return PLOG.slice(n0).join("\\n");
    }""")
    print(log)
    ok("📝 какая погода в минске (сервер, 1." in log or "📝 какая погода в минске (сервер," in log, "услышанное и откуда — в журнале")
    ok("❓ какая погода в минске → Dewiar Облако" in log, "вопрос и куда ушёл")
    ok("💬 ответ 32 симв." in log, "длина ответа и время")
    ok("голоса нет" in log, "нет русского голоса — строка в журнале")
    ok("ошибка 3 HTTP 502" in log, "ошибка распознавания сервера — в журнале")
    ok(not errors, f"без ошибок страницы {errors[:2]}")
    b.close()
print("ALL OK" if not fails else f"ошибок: {len(fails)}")
sys.exit(1 if fails else 0)
