"""Резервная копия и сейф: копия без пароля — без сейфа и пароля почты; с паролем — всё; неверный пароль — отказ;
восстановление на «другом компьютере» возвращает заметки, деньги, напоминания, персонажей и сейф и ничего не удаляет;
сейф голосом: «запомни пароль от вайфая: …» → в сейф (не в заметки), «какой пароль от вайфая?» — на экране, не вслух,
в историю разговора (а значит, в модель) не попадает."""
import asyncio, json, os, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
sys.path.insert(0, str(ROOT / "core"))
from backup import Backup, BackupError  # noqa: E402
from crypt import seal_pw, unseal_pw, CryptError  # noqa: E402
from vault import Vault  # noqa: E402

blob = seal_pw("секрет".encode(), "пароль")
try:
    unseal_pw(blob, "не тот"); bad = False
except CryptError:
    bad = True
ok(unseal_pw(blob, "пароль").decode() == "секрет" and bad and b"\xd1\x81\xd0\xb5" not in blob, "шифрование: свой пароль открывает, чужой — нет")

A = tmp / "A"
cfg, st, ws = A / "cfg", A / "st", A / "ws"
for d in (cfg / "personas", st / "voiceprints", ws / "Заметки", ws / "Финансы", ws / "Задачи/2026-10-01 отчёт/рабочая копия"):
    d.mkdir(parents=True)
(cfg / "settings.json").write_text(json.dumps({"persona": "eva", "audio": {"input": "alsa_input.usb"}, "cloud": {"key": "dw-секрет-облака", "url": "u"},
    "llm": {"profiles": {"a": {"name": "vLLM", "api_key": "sk-ключ-модели", "max_tokens": 900}}},
    "mcp": {"servers": [{"name": "gh", "env": {"GITHUB_TOKEN": "ghp_токен"}}]}}), encoding="utf-8")
(cfg / "telegram.json").write_text(json.dumps({"token": "123:AAH-бот", "owner": {"id": 5}}), encoding="utf-8")
(cfg / "ssh").mkdir()
(cfg / "ssh/id_ed25519").write_text("-----BEGIN OPENSSH PRIVATE KEY-----")
(cfg / "token").write_text("секретный-токен-сокета")
(cfg / "money.json").write_text(json.dumps({"items": [{"amount": 8.5}]}), encoding="utf-8")
(cfg / "mail.json").write_text(json.dumps({"address": "a@b.ru", "password": "почтовый-пароль"}), encoding="utf-8")
(cfg / "personas/eva.webp").write_bytes(b"RIFF....WEBP")
(st / "reminders.json").write_text("[]")
(st / "voiceprints/dewiar.json").write_text("{}")
(ws / "Заметки/Отпуск.md").write_text("# Отпуск\nМоре", encoding="utf-8")
(ws / "Финансы/2026-10.md").write_text("кофе 8,50", encoding="utf-8")
(ws / "Задачи/2026-10-01 отчёт/рабочая копия/big.py").write_text("x")
va = Vault(cfg / "vault.bin", cfg / "vault.key")
va.put("Пароль от вайфая", "кот123")
ok(b"\xd0\xba\xd0\xbe\xd1\x82123" not in (cfg / "vault.bin").read_bytes() and va.find("вайфай")["value"] == "кот123",
   "сейф зашифрован на диске, находится по слову «вайфай»")
b = Backup(cfg, st, ws, va, "0.18.0")
out = tmp / "копии"
plain = b.make(out)
import zipfile, io  # noqa: E402
man, z = b.open(plain["path"])
names = set(z.namelist())
mail = json.loads(z.read("config/mail.json"))
ok("workspace/Заметки/Отпуск.md" in names and "config/money.json" in names and "state/reminders.json" in names
   and "config/personas/eva.webp" in names and "state/voiceprints/dewiar.json" in names,
   f"копия: заметки, деньги, напоминания, персонажи, отпечатки ({plain['size_text']})")
ok("config/token" not in names and "vault.json" not in names and "config/vault.bin" not in names and "password" not in mail
   and not any("рабочая копия" in n for n in names), "без пароля в копии нет сейфа, пароля почты, служебного токена, рабочих копий")
raw_all = b"".join(z.read(n) for n in names)
st_plain = json.loads(z.read("config/settings.json"))
ok(not any(x.encode() in raw_all for x in ("dw-секрет-облака", "sk-ключ-модели", "ghp_токен", "123:AAH-бот", "OPENSSH"))
   and st_plain["llm"]["profiles"]["a"]["max_tokens"] == 900 and st_plain["cloud"]["url"] == "u"
   and json.loads(z.read("config/telegram.json"))["owner"]["id"] == 5,
   "без пароля в копии нет ключей моделей и облака, токенов ботов и MCP, ключей серверов — остальное на месте")
b.host = "старый-ноутбук"
locked = b.make(out, password="Секрет-1")
try:
    b.open(locked["path"], "неверный"); wrong = False
except BackupError:
    wrong = True
man2, z2 = b.open(locked["path"], "Секрет-1")
ok(wrong and "vault.json" in z2.namelist() and json.loads(z2.read("config/mail.json")).get("password") == "почтовый-пароль"
   and locked["encrypted"], "с паролем — всё, включая сейф и пароль почты; неверный пароль — отказ")
ok(len(b.list(out)) == 2 and b.list(out)[0]["counts"]["workspace"] >= 2, "список копий с описанием")

# «другой компьютер»
B = tmp / "B"
cfg2, st2, ws2 = B / "cfg", B / "st", B / "ws"
for d in (cfg2, st2, ws2 / "Заметки"):
    d.mkdir(parents=True)
(ws2 / "Заметки/Новая.md").write_text("уже здесь", encoding="utf-8")
(cfg2 / "token").write_text("свой-токен")
vb = Vault(cfg2 / "vault.bin", cfg2 / "vault.key")
b2 = Backup(cfg2, st2, ws2, vb, "0.18.0")
r = b2.restore(locked["path"], "Секрет-1")
s2 = json.loads((cfg2 / "settings.json").read_text(encoding="utf-8"))
ok((ws2 / "Заметки/Отпуск.md").read_text(encoding="utf-8").endswith("Море") and (ws2 / "Заметки/Новая.md").exists()
   and (cfg2 / "money.json").exists() and (cfg2 / "token").read_text() == "свой-токен",
   f"восстановлено {r['files']} файлов; своё не удалено, служебный токен не тронут")
ok(vb.find("вайфай") and vb.find("вайфай")["value"] == "кот123" and r["vault"] == 1, "сейф переехал на новый компьютер (новым ключом)")
ok(s2.get("persona") == "eva" and s2["audio"]["input"] == "", "настройки вернулись; микрофон другого компьютера — по умолчанию")
ok(r.get("before") and Path(r["before"]).exists(), "перед восстановлением сделана копия текущего состояния")

# сквозной: ядро — сейф голосом
import websockets  # noqa: E402
PORT, LLM_PORT = 8821, 8151
(tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "dewiar", "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False, "follow_up_seconds": 0},
    "backup": {"dir": str(tmp / "core-копии"), "auto": False},
    "llm": {"active": "m", "profiles": {"m": {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "model": "chat"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT),
           DEWIAR_ASSUME_AEC="1", DEWIAR_WORKSPACE_DIR=str(tmp / "ws3"))
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM_PORT)])
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio"], env=env,
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
            for e in events:
                if pred(e): return e
            await asyncio.sleep(0.1)
    async def reader():
        async for raw in ws_:
            e = json.loads(raw)
            if e["type"] == "reply": replies[e["id"]] = e
            elif e["type"] != "level": events.append(e)
    rd = asyncio.ensure_future(reader())
    await asyncio.sleep(2)
    await call("say", text="Запомни пароль от вайфая: кот123")
    R["saved"] = await wait_for(lambda e: e["type"] == "answer" and "сейф" in e.get("text", ""), 8)
    R["heard_masked"] = any(e["type"] == "heard" and "кот123" not in e.get("text", "") and "•••" in e.get("text", "") for e in events)
    events.clear()
    await call("say", text="Какой пароль от вайфая?")
    R["got"] = await wait_for(lambda e: e["type"] == "answer" and "кот123" in e.get("text", ""), 8)
    R["list"] = (await call("vault_list"))["data"]
    R["reveal"] = (await call("vault_reveal", id=R["list"]["items"][0]["id"]))["data"] if R["list"]["items"] else {}
    R["make"] = (await call("backup_make"))["data"]
    R["info"] = (await call("backup_info"))["data"]
    rd.cancel()

try:
    asyncio.run(run())
finally:
    for p in (core, mock):
        p.terminate()
    core.wait()
log = (tmp / "core.log").read_text(encoding="utf-8")
notes = list((tmp / "ws3").rglob("*.md"))
ok(R.get("saved") and R.get("heard_masked") and not any("кот123" in p.read_text(encoding="utf-8") for p in notes),
   "«запомни пароль от вайфая: кот123» — в сейф, не в заметки; в виджете — «•••»")
ok(R.get("got") and "кот123" not in log, "«какой пароль от вайфая?» — значение на экране; в журнале его нет")
ok(R.get("list") and "value" not in R["list"]["items"][0] and R.get("reveal", {}).get("value") == "кот123",
   "студия: список без значений, «показать» — по нажатию")
ok(R.get("make") and R.get("info") and len(R["info"]["items"]) == 1 and R["info"]["vault"] == 1, "копия из студии одной кнопкой")

# копия без пароля на компьютере, где ключи уже есть: ключи остаются свои
C = tmp / "C"
cfg3, st3, ws3 = C / "cfg", C / "st", C / "ws"
for d in (cfg3, st3, ws3):
    d.mkdir(parents=True)
(cfg3 / "settings.json").write_text(json.dumps({"persona": "lira", "cloud": {"key": "dw-свой", "url": "old"},
    "llm": {"profiles": {"a": {"name": "старое", "api_key": "sk-свой"}}}}), encoding="utf-8")
(cfg3 / "telegram.json").write_text(json.dumps({"token": "777:свой", "owner": {"id": 1}}), encoding="utf-8")
b3 = Backup(cfg3, st3, ws3, Vault(cfg3 / "vault.bin", cfg3 / "vault.key"), "0.20.0")
b3.restore(plain["path"])
s3 = json.loads((cfg3 / "settings.json").read_text(encoding="utf-8"))
t3 = json.loads((cfg3 / "telegram.json").read_text(encoding="utf-8"))
ok(s3["persona"] == "eva" and s3["cloud"] == {"key": "dw-свой", "url": "u"} and s3["llm"]["profiles"]["a"]["api_key"] == "sk-свой"
   and s3["llm"]["profiles"]["a"]["name"] == "vLLM" and t3 == {"token": "777:свой", "owner": {"id": 5}},
   "восстановление копии без пароля: настройки из копии, ключи и токены этого компьютера — на месте")
