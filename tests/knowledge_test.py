"""«Знания» на этом компьютере и «Обо мне»: извлечение текста, поиск по формам слов, инструмент только при
наличии документов, память целиком — модулем и через живое ядро."""
import asyncio, base64, io, json, os, subprocess, sys, tempfile, time, zipfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
os.environ["DEWIAR_CONFIG_DIR"] = str(tmp / "cfg0"); os.environ["DEWIAR_STATE_DIR"] = str(tmp / "st0")
sys.path.insert(0, str(ROOT / "core"))


def docx(text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        paras = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in text.split("\n"))
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="x"><w:body>{paras}</w:body></w:document>')
    return b.getvalue()


def odt(text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("content.xml", f'<?xml version="1.0"?><office:document-content xmlns:office="o" xmlns:text="t"><office:body><text:p>{text}</text:p></office:body></office:document-content>')
    return b.getvalue()


def pdf(text):
    """Одностраничный PDF руками (латиница — стандартный шрифт Helvetica)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return out


REGL = "Регламент отпусков.\nЕжегодный оплачиваемый отпуск согласуется с руководителем за две недели.\nЗаявление подают через отдел кадров."
from settings import Settings  # noqa: E402
from workspace import Workspace  # noqa: E402
from knowledge import Knowledge, stem  # noqa: E402
from tools import Toolbox  # noqa: E402

s = Settings(); s.update({"workspace": {"path": str(tmp / "ws")}})
ws = Workspace(s); ws.ensure()
kn = Knowledge(ws, tmp / "kn.json")
tb = Toolbox(s, None); tb.hooks["knowledge"] = kn
ok(not tb.policy("search_documents")[0], "пустые «Знания» — инструмента у модели нет")
ok((kn.root / "О папке.md").exists(), "в папке «Знания» есть объяснение")
kn.add("Регламент.docx", docx(REGL))
kn.add("Склад.odt", odt("Инвентаризация склада проводится раз в квартал, последняя пятница месяца."))
kn.add("Заметка.md", "# Пароль от Wi-Fi\nГостевая сеть: dacha-guest".encode())
try:
    import pypdf  # noqa: F401
    kn.add("Policy.pdf", pdf("Remote work is allowed two days per week"))
    has_pdf = True
except ImportError:
    has_pdf = False
try: kn.add("вирус.exe", b"MZ"); ok(False, "exe не принимается")
except ValueError: ok(True, "чужие типы файлов не принимаются")
files = {f["path"]: f for f in kn.list()}
ok(files["Регламент.docx"]["chunks"] >= 1 and not files["Регламент.docx"]["error"], "Word: текст извлечён")
ok(files["Склад.odt"]["chunks"] >= 1, "ODT: текст извлечён")
if has_pdf:
    ok(files["Policy.pdf"]["chunks"] >= 1, f"PDF: текст извлечён ({files['Policy.pdf']['error'] or 'ok'})")
ok(stem("отпуска") == stem("отпуск") == stem("отпуском"), "формы слова сводятся к одной основе")
hits = kn.search("когда согласовывать отпуск?")
ok(hits and hits[0]["path"] == "Регламент.docx" and "две недели" in hits[0]["text"], f"поиск по формам слов: {hits[0]['path'] if hits else '—'}")
ok(kn.search("инвентаризацию склада")[0]["path"] == "Склад.odt", "поиск во втором документе")
if has_pdf:
    ok(kn.search("remote work")[0]["path"] == "Policy.pdf", "поиск в PDF")
ok(kn.search("квантовая хромодинамика") == [], "нет совпадений — пусто, без выдумок")
ok(tb.policy("search_documents")[0], "есть документы — инструмент у модели есть")
(kn.root / "Заметка.md").write_text("# Пароль\nНовая гостевая сеть: dacha-5g", encoding="utf-8")
time.sleep(0.05); os.utime(kn.root / "Заметка.md")
ok("dacha-5g" in kn.search("гостевая сеть")[0]["text"], "изменённый файл переиндексирован")
kn.remove("Заметка.md")
ok("Заметка.md" not in {f["path"] for f in kn.list()}, "файл убран из «Знаний»")
ok(Knowledge(ws, tmp / "kn.json").search("отпуск")[0]["path"] == "Регламент.docx", "индекс переживает перезапуск")
ws.memory_set("- Зовут Анна\n- Работает в бухгалтерии")
ok("бухгалтерии" in ws.memory_text() and ws.memory_get().strip().startswith("- Зовут Анна"), "память: правка из студии видна модели")
ws.remember("Любит короткие ответы")
ok("короткие" in ws.memory_get() and "Зовут Анна" in ws.memory_get(), "память: дописывание не стирает правку")

# ---------- живое ядро ----------
import websockets  # noqa: E402
PORT, LLM_PORT = 8799, 8131
(tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({
    "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False, "follow_up_seconds": 0},
    "workspace": {"path": str(tmp / "ws2")},
    "llm": {"active": "m", "profiles": {"m": {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "model": "chat"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT), DEWIAR_ASSUME_AEC="1")
sil = TESTS / "silence90.wav"
if not sil.exists():
    import wave
    with wave.open(str(sil), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000 * 90)
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM_PORT)])
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(sil), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
events, R = [], {}


async def run():
    ws_ = None
    for _ in range(300):
        await asyncio.sleep(0.2)
        if (tmp / "cfg/token").exists():
            try: ws_ = await websockets.connect(f"ws://127.0.0.1:{PORT}", max_size=None); break
            except OSError: continue
    await ws_.send(json.dumps({"cmd": "hello", "token": (tmp / "cfg/token").read_text().strip()}))
    n = [0]; replies = {}
    async def call(cmd, **args):
        n[0] += 1; i = n[0]; await ws_.send(json.dumps({"id": i, "cmd": cmd, "args": args}))
        for _ in range(600):
            if i in replies: return replies.pop(i)
            await asyncio.sleep(0.05)
    async def wait_for(pred, limit):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            if any(pred(e) for e in events): return True
            await asyncio.sleep(0.1)
    async def reader():
        async for raw in ws_:
            e = json.loads(raw)
            if e["type"] == "reply": replies[e["id"]] = e
            elif e["type"] != "level": events.append(e)
    rd = asyncio.ensure_future(reader())
    await wait_for(lambda e: e["type"] == "status" and e["status"].get("input"), 60)
    R["empty"] = await call("knowledge_list")
    R["add"] = await call("knowledge_add", name="Регламент.docx", b64="data:application/octet-stream;base64," + base64.b64encode(docx(REGL)).decode())
    R["search"] = await call("knowledge_search", query="отпуск")
    R["mem_set"] = await call("memory_set", text="- Меня зовут Олег")
    R["mem_get"] = await call("memory_get")
    await call("say", text="Что в моих документах про отпуск?")
    R["answered"] = await wait_for(lambda e: e["type"] == "answer" and e.get("done") and "Регламент" in e.get("text", ""), 40)
    rd.cancel()

try:
    asyncio.run(run())
finally:
    core.terminate(); mock.terminate(); core.wait(); mock.wait()
ok(R["empty"]["ok"] and R["empty"]["data"]["files"] == [], "студия: пустой список")
ok(R["add"]["ok"] and R["add"]["data"]["added"] == "Регламент.docx", "студия: файл добавлен")
ok(R["search"]["ok"] and R["search"]["data"] and R["search"]["data"][0]["path"] == "Регламент.docx", "студия: проверка поиска")
ok(R["mem_get"]["ok"] and "Олег" in R["mem_get"]["data"], "студия: память сохраняется и читается")
ok(R.get("answered"), "голосовой вопрос → документы → ответ из регламента")
