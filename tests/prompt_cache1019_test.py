"""Отзыв 19.10 (YouTube): «на своём сервере долго отвечает: в подсказку для модели каждый раз вписывается текущее время
с минутами — сервер не может её запомнить и перечитывает целиком на каждый вопрос. Если время передавать в самом
сообщении, а подсказку не менять, ответы идут сразу». llama.cpp, LM Studio, Ollama, vLLM переиспользуют уже прочитанное
начало запроса (кэш префикса) — лишь бы оно не менялось.
Проверяем на компьютере (настоящее ядро, mock_llm пишет запросы) и на телефоне (app.js): системная подсказка и
инструменты от вопроса к вопросу не меняются, в них нет ни времени, ни найденного в записях; время, неделя по дням и
найденное — справкой в начале последнего сообщения пользователя; в историю разговора справка не попадает."""
import asyncio, json, os, re, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
TESTS = Path(__file__).resolve().parent
PY = sys.executable
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


tmp = Path(tempfile.mkdtemp())
PORT, LLM = free(), free()
LOG = tmp / "llm.jsonl"
(tmp / "cfg").mkdir()
(tmp / "ws/Заметки").mkdir(parents=True)
(tmp / "ws/Заметки/Велосипед.md").write_text("Велосипед: цепь смазать маслом 3 мая, давление в шинах 3 бара.", encoding="utf-8")
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "lira", "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False, "follow_up_seconds": 0},
    "ui": {"welcome": "done", "intro": "done"},
    "llm": {"active": "m", "profiles": {"m": {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM}/v1", "model": "chat"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT),
           DEWIAR_ASSUME_AEC="1", DEWIAR_WORKSPACE_DIR=str(tmp / "ws"), DEWIAR_FAKE_RAM_GB="16", DEWIAR_FAKE_CORES="8")
mock = subprocess.Popen([PY, str(TESTS / "mock_llm.py"), str(LLM)], env=dict(os.environ, MOCK_LOG_FULL=str(LOG)))
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
import websockets  # noqa: E402


def requests():
    return [json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines() if x.strip()] if LOG.exists() else []


async def run():
    ws_ = None
    for _ in range(300):
        await asyncio.sleep(0.2)
        if (tmp / "cfg/token").exists():
            try:
                ws_ = await websockets.connect(f"ws://127.0.0.1:{PORT}", max_size=None); break
            except OSError:
                continue
    await ws_.send(json.dumps({"cmd": "hello", "token": (tmp / "cfg/token").read_text().strip()}))
    events, replies, cnt = [], {}, [0]

    async def call(cmd, **args):
        cnt[0] += 1; i = cnt[0]
        await ws_.send(json.dumps({"id": i, "cmd": cmd, "args": args}))
        for _ in range(1200):
            if i in replies:
                return replies.pop(i)
            await asyncio.sleep(0.05)

    async def reader():
        async for raw in ws_:
            e = json.loads(raw)
            if e["type"] == "reply":
                replies[e["id"]] = e
            elif e["type"] != "level":
                events.append(e)
    rd = asyncio.ensure_future(reader())
    await asyncio.sleep(2)

    async def say(text, limit=25):
        events.clear()
        await call("say", text=text)
        end = time.monotonic() + limit
        while time.monotonic() < end:
            if [e for e in events if e["type"] == "answer" and e.get("done")]:
                await asyncio.sleep(0.3); return True
            await asyncio.sleep(0.1)
        return False
    for q in ("Привет, как дела?", "Сколько давление в шинах велосипеда?", "Расскажи короткий анекдот"):
        await say(q)
    rd.cancel()
    await ws_.close()

try:
    asyncio.run(run())
finally:
    core.terminate(); mock.terminate()
    try:
        core.wait(10)
    except Exception:  # noqa: BLE001
        core.kill()
reqs = [r for r in requests() if r.get("messages") and r["messages"][0].get("role") == "system" and r.get("tools")
        and len(r["messages"][0]["content"]) > 2000]           # без проверки инструментов (короткий пробный запрос)
ok(len(reqs) >= 3, f"компьютер: три вопроса — три запроса к модели с инструментами ({len(reqs)})")
if len(reqs) >= 3:
    sys0 = [r["messages"][0]["content"] for r in reqs[:3]]
    ok(sys0[0] == sys0[1] == sys0[2], "компьютер: системная подсказка не меняется от вопроса к вопросу")
    ok(reqs[0]["tools"] == reqs[1]["tools"] == reqs[2]["tools"], "компьютер: список инструментов тот же")
    ok(not re.search(r"Сейчас [а-я]+, \d", sys0[0]) and "Ближайшие дни" not in sys0[0] and "давление в шинах" not in sys0[0],
       "компьютер: в подсказке нет ни времени, ни недели, ни найденного в записях")
    last = [r["messages"][-1] for r in reqs[:3]]
    txt = [m["content"] if isinstance(m["content"], str) else "" for m in last]
    ok(all(re.search(r"Сейчас .*\d{1,2}:\d{2}", t) for t in txt) and txt[0].rstrip().endswith("Привет, как дела?"),
       "компьютер: время — справкой в начале последнего сообщения, сам вопрос — в конце")
    # 23.10 (письмо пользователя: «навязчивые разговоры про календарь и дни рождения»): неделя и «считай calc» — только к
    # вопросам про даты, не к «как дела»
    ok("Ближайшие дни" not in txt[0] and "день рождения" not in txt[0], "компьютер: к «как дела» — без недели и без «дней рождения»")
    ok("3 бара" in txt[1] or "давление" in txt[1].split("(Сообщение пользователя)")[0], "компьютер: найденное в записях — в справке к своему вопросу")
    hist = [m for m in reqs[2]["messages"][1:-1] if m.get("role") == "user"]
    ok(hist and not any("(Справка к этому сообщению" in (m["content"] if isinstance(m["content"], str) else "") for m in hist),
       "компьютер: в историю разговора справка не попадает")

# ---------- телефон ----------
www = tmp / "www"
subprocess.run(["bash", str(ROOT / "android/tools/www_assemble.sh"), str(www)], check=True, capture_output=True)
seen = []


def llm(route):
    req = route.request
    if req.method == "GET":
        return route.fulfill(status=200, body=json.dumps({"data": [{"id": "m"}]}), headers={"Content-Type": "application/json"})
    seen.append(json.loads(req.post_data or "{}"))
    sse = 'data: {"choices":[{"delta":{"content":"Хорошо."}}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    route.fulfill(status=200, body=sse, headers={"Content-Type": "text/event-stream"})


from playwright.sync_api import sync_playwright  # noqa: E402
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 393, "height": 852}); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.route("http://llm.test/**", llm)
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(1000)
    pg.evaluate("""() => { Object.assign(S.llm, { kind: "openai", base_url: "http://llm.test/v1", model: "m", api_key: "k", name: "тест" }); S.textMode = true; saveS();
        document.querySelectorAll('.sheet.on').forEach(s => s.classList.remove('on')); }""")
    for q in ("Привет", "Что нового?"):
        pg.evaluate(f"ask({json.dumps(q)})"); pg.wait_for_function("!busy", timeout=15000); pg.wait_for_timeout(200)
    b.close()
chat = [r for r in seen if r.get("messages")]
ok(len(chat) >= 2, f"телефон: два запроса ({len(chat)})")
(tmp / "phone.json").write_text(json.dumps(chat, ensure_ascii=False), encoding="utf-8")
if len(chat) >= 2:
    s0, s1 = chat[0]["messages"][0]["content"], chat[1]["messages"][0]["content"]
    ok(s0 == s1 and not re.search(r"Сейчас [а-я]+, \d", s0) and "Ближайшие дни" not in s0 and "ЧТО БЫЛО НЕДАВНО" not in s1,
       "телефон: подсказка та же и без времени (и без растущего «что было недавно»)")
    lt = chat[1]["messages"][-1]["content"]
    lt = lt if isinstance(lt, str) else ""
    ok("Сейчас " in lt and "Ближайшие дни" not in lt and lt.rstrip().endswith("Что нового?"), "телефон: время — справкой в последнем сообщении, неделя — только к вопросам о датах")
    ok(not any("(Справка к этому сообщению" in str(m.get("content")) for m in chat[1]["messages"][1:-1]), "телефон: в истории справки нет")
ok(not errs, f"телефон без ошибок: {errs[:2]}")
shutil.rmtree(tmp, ignore_errors=True)
print("\nИТОГ:", "всё прошло" if not fails else f"не прошло {len(fails)}")
sys.exit(1 if fails else 0)
