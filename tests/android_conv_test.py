"""Телефон: длинный разговор, как на компьютере. Начало разговора модель сворачивает в содержание (факт из первой реплики
не теряется), запрос не растёт; перерыв виден модели; «начнём новый разговор» и 8 часов тишины — с чистого листа."""
import json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
TESTS = Path(__file__).resolve().parent
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


tmp = Path(tempfile.mkdtemp())
LLM = free()
LOG = tmp / "llm.jsonl"
www = tmp / "www"
shutil.copytree(ROOT / "android/assets/www", www)
shutil.copy(ROOT / "ui/assets/entities.js", www / "entities.js")
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)   # живое лицо Эвы
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM)], env=dict(os.environ, MOCK_LOG_FULL=str(LOG), MOCK_ECHO_LAST="1"))
time.sleep(1)


def proxy(route):
    req = route.request
    if req.method == "OPTIONS":
        return route.fulfill(status=204, headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"})
    try:
        r = urllib.request.urlopen(urllib.request.Request(req.url, data=req.post_data_buffer, method=req.method,
                                                          headers={"Content-Type": "application/json"}), timeout=60)
        body, status, ctype = r.read(), r.status, r.headers.get("Content-Type", "application/json")
    except urllib.error.HTTPError as e:
        body, status, ctype = e.read(), e.code, "application/json"
    route.fulfill(status=status, body=body, headers={"Access-Control-Allow-Origin": "*", "Content-Type": ctype})


def reqs():
    return [json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines() if x.strip()] if LOG.exists() else []


SUM = "Ты ведёшь краткое содержание"
chats = lambda: [r for r in reqs() if not r["messages"][0]["content"].startswith(SUM)]
folds = lambda: [r for r in reqs() if r["messages"][0]["content"].startswith(SUM)]
try:
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route(f"http://127.0.0.1:{LLM}/**", proxy)
        pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(800)
        pg.evaluate(f"""() => {{ Object.assign(S.llm, {{ kind: "openai", base_url: "http://127.0.0.1:{LLM}/v1", model: "chat", name: "тест" }}); saveS(); }}""")

        def say(t):
            pg.evaluate("t => { window._done = false; ask(t).then(() => window._done = true); }", t)
            for _ in range(200):
                pg.wait_for_timeout(100)
                if pg.evaluate("window._done && state === 'idle'"):
                    break
            return pg.inner_text("#bot")

        say("Запомни для разговора: мою собаку зовут Бублик, а встреча с Олегом в четверг в 15:00.")
        for i in range(2, 15):
            say(f"Вопрос номер {i}: расскажи про число {i * 7}.")
        for _ in range(100):
            if pg.evaluate("conv.summary.includes('Бублик') && !convBusy"):
                break
            pg.wait_for_timeout(200)
        say("Вопрос номер 15: как дела?")
        last = chats()[-1]
        sysp = last["messages"][0]["content"]
        ok(folds(), f"начало разговора свёрнуто в фоне ({len(folds())} раз)")
        ok("Раньше в нём было" in sysp and "Бублик" in sysp and "15:00" in sysp, "через 15 реплик факт из первой — у модели в содержании")
        ok(len(last["messages"]) <= 2 * (8 + 8) + 2 and not any("Запомни для разговора" in m["content"] for m in last["messages"][1:]),
           f"запрос не растёт, первая реплика дословно не уходит ({len(last['messages'])} сообщений)")
        ok("номер 14" in json.dumps(last["messages"], ensure_ascii=False), "последние реплики — дословно")
        ok(pg.evaluate("history.length") > len(last["messages"]) - 2, "в «Разговоре» видно и свёрнутое начало")

        pg.evaluate("conv.last = Date.now() - 45 * 60000; saveConv()")
        say("Как зовут мою собаку?")
        ok("перерыв 45 мин" in chats()[-1]["messages"][0]["content"], "перерыв 45 минут модель видит")
        ans = say("Начнём новый разговор")
        n = len(chats())
        say("Привет!")
        c = chats()[n:]
        ok("чистого листа" in ans and c and "Бублик" not in json.dumps(c[-1], ensure_ascii=False), f"«Начнём новый разговор» — с чистого листа ({ans})")
        say("Мою кошку зовут Мурка.")
        pg.evaluate("conv.summary = '- Пользователь говорил про Мурку'; conv.last = Date.now() - 9 * 3600e3; saveConv()")
        n = len(chats())
        say("Добрый вечер")
        c = chats()[n:]
        # в самом разговоре прошлого нет; в подсказке — только короткий журнал недели («что мы делали», см. android_feedback_test)
        ok(c and "Мурк" not in json.dumps(c[-1]["messages"][1:], ensure_ascii=False) and len(c[-1]["messages"]) == 2, "8 часов тишины — новый разговор")
        ans = say("Какая погода в Минске?")
        ok("посмотрю в интернете" in ans and any(r["messages"][-1]["content"].startswith("СТОП. Ты отказался") and "web_search" in r["tools"]
                                                 for r in chats()) and "Готово. Результат" in ans,
           f"«нет доступа к интернету» — телефон сам просит поискать и отвечает по найденному: {ans[:90]}")
        pg.reload(); pg.wait_for_timeout(800)
        ok(pg.evaluate("conv.turns") >= 1 and pg.evaluate("typeof conv.last") == "number", "разговор переживает перезапуск приложения")
        ok(not errs, f"без ошибок JavaScript {errs}")
        b.close()
finally:
    mock.terminate()
print("ИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
