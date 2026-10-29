"""Этап 5: MCP (stdio, HTTP, HTTP+SSE), подключение по адресу с кодом доступа, экспорт, навыки."""
import base64, io, json, os, subprocess, sys, tempfile, threading, time, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
tmp = Path(tempfile.mkdtemp()); os.environ["DEWIAR_CONFIG_DIR"] = str(tmp / "cfg"); os.environ["DEWIAR_STATE_DIR"] = str(tmp / "st")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "core"))
from settings import Settings
import provision
from mcp import MCPManager
from skills import Skills, SkillError
ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(("✅" if cond else "❌"), name)

# ---- MCP ----
hp = [subprocess.Popen([sys.executable, str(HERE / "mcp_test_server.py"), "--http", "8151"]),
      subprocess.Popen([sys.executable, str(HERE / "mcp_test_server.py"), "--http", "8152", "--sse"])]
time.sleep(1)
s = Settings()
s.update({"mcp": {"servers": {
    "notes": {"type": "stdio", "command": sys.executable, "args": [str(HERE / "mcp_test_server.py")]},
    "web": {"type": "http", "url": "http://127.0.0.1:8151/mcp"},
    "sse": {"type": "http", "url": "http://127.0.0.1:8152/mcp", "trust": True},
    "broken": {"type": "stdio", "command": "no-such-mcp-binary"}}}})
m = MCPManager(s); m.reconcile()
for _ in range(50):
    st = m.status()
    if all(v["status"] != "connecting" for v in st.values()): break
    time.sleep(0.1)
st = m.status()
check("stdio-сервер подключён, 2 инструмента", st["notes"]["status"] == "ok" and len(st["notes"]["tools"]) == 2)
check("HTTP-сервер подключён", st["web"]["status"] == "ok")
check("HTTP+SSE-сервер подключён", st["sse"]["status"] == "ok")
check("битый сервер — понятная ошибка, остальные работают", st["broken"]["status"] == "error" and "не найдена программа" in st["broken"]["error"])
names = [x["name"] for x in m.specs()]
check("инструменты видны модели как mcp_<сервер>_<имя>", "mcp_notes_echo" in names and "mcp_web_write_note" in names)
check("вызов stdio", m.call("mcp_notes_echo", {"text": "привет"}) == "эхо: привет")
check("вызов HTTP", m.call("mcp_web_write_note", {"text": "x"}) == "записано, всего 1")
check("вызов SSE", m.call("mcp_sse_echo", {"text": "a"}) == "эхо: a")
check("только чтение — без подтверждения", not m.needs_confirm("mcp_notes_echo"))
check("меняет данные — с подтверждением", m.needs_confirm("mcp_notes_write_note"))
check("доверенный сервер — без подтверждения", not m.needs_confirm("mcp_sse_write_note"))
s.update({"mcp": {"servers": {"web": s.get("mcp", "servers")["web"]}}}); m.reconcile()
check("удалённые из настроек серверы отключаются", set(m.status()) == {"web"})
m.close(); [p.terminate() for p in hp]

# ---- подключение по адресу ----
MAN = {"dewiar": 1, "name": "Dewiar Cloud", "key": {"required": True, "hint": "Код из кабинета"},
       "llm": {"active": "cloud", "profiles": {"cloud": {"name": "Dewiar Cloud", "kind": "openai", "base_url": "http://127.0.0.1:8153/v1", "api_key": "${KEY}"}}},
       "stt": {"engine": "remote", "remote": {"base_url": "http://127.0.0.1:8153/v1", "api_key": "${KEY}"}},
       "tts": {"engine": "remote", "remote": {"base_url": "http://127.0.0.1:8153/v1", "model": "qwen3-tts", "sample_rate": 24000}},
       "skills": [{"name": "hello", "url": "http://127.0.0.1:8153/skills/hello.zip"}],
       "mcp": {"servers": {"local": {"type": "stdio", "command": "npx", "args": ["-y", "some-server"]}}}}
buf = io.BytesIO(); z = zipfile.ZipFile(buf, "w")
z.writestr("hello/SKILL.md", "---\nname: hello\ndescription: Поздороваться красиво\n---\nСкажи привет стихами.\n")
z.writestr("hello/references/extra.md", "дополнительно"); z.close(); ZIP = buf.getvalue()
class S(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/.well-known/dewiar.json":
            b = json.dumps(MAN).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
        elif self.path == "/skills/hello.zip":
            b = ZIP; self.send_response(200); self.send_header("Content-Type", "application/zip")
        else:
            b = b"{}"; self.send_response(404)
        self.end_headers(); self.wfile.write(b)
srv = ThreadingHTTPServer(("127.0.0.1", 8153), S); threading.Thread(target=srv.serve_forever, daemon=True).start()
check("адрес: 127.0.0.1:8153 → http://…", provision.normalize_address("127.0.0.1:8153") == "http://127.0.0.1:8153")
check("адрес: dewiar.ru → https://…", provision.normalize_address("dewiar.ru") == "https://dewiar.ru")
check("ссылка dewiar://connect?server=…", provision.normalize_address("dewiar://connect?server=https%3A%2F%2Fa.ru") == "https://a.ru")
man = provision.fetch_manifest("127.0.0.1:8153")
pv = provision.preview(man, s.snapshot())
check("нашёл /.well-known/dewiar.json", man["url"].endswith("/.well-known/dewiar.json"))
check("предпросмотр: просит код и предупреждает о программе MCP", pv["need_key"] and any("npx" in w for w in pv["warnings"]))
try:
    provision.to_patch(man, ""); check("без кода — отказ", False)
except provision.ProvisionError:
    check("без кода — отказ", True)
s.update({"llm": {"profiles": {"home": {"name": "Мой vLLM", "kind": "openai", "base_url": "http://10.0.0.5:8000/v1"}}, "active": "home"}})
patch, skl = provision.to_patch(man, "SECRET-42")
provision.apply_patch(s, patch)
snap = s.snapshot()
check("код подставлен в адреса", snap["llm"]["profiles"]["cloud"]["api_key"] == "SECRET-42" and snap["stt"]["remote"]["api_key"] == "SECRET-42")
check("свой vLLM не потерялся", "home" in snap["llm"]["profiles"] and snap["llm"]["active"] == "cloud")
check("запомнил, откуда обновляться", snap["provision"]["url"].endswith("dewiar.json"))
exp = provision.export_settings(snap)
check("экспорт без ключей", exp["llm"]["profiles"]["cloud"]["api_key"] == "" and "ui" not in exp)
check("экспорт с ключами", provision.export_settings(snap, True)["llm"]["profiles"]["cloud"]["api_key"] == "SECRET-42")
check("экспорт снова импортируется", provision.preview(json.loads(json.dumps(exp)), snap)["lines"])

# ---- навыки ----
sk = Skills(s)
check("встроенные навыки найдены", {"dewiar-help", "morning-brief"} <= set(sk.items))
check("навык с сервера по ссылке", sk.install_url(skl[0]["url"]) == "hello" and "hello" in sk.items)
check("use_skill: инструкции + список файлов", "стихами" in sk.use("hello") and "references/extra.md" in sk.use("hello"))
check("read_skill_file", sk.read_file("hello", "references/extra.md") == "дополнительно")
try:
    sk.read_file("hello", "../../settings.json"); check("выход из папки навыка заблокирован", False)
except SkillError:
    check("выход из папки навыка заблокирован", True)
bad = io.BytesIO(); z = zipfile.ZipFile(bad, "w"); z.writestr("x/SKILL.md", "---\nname: evil\n---\n"); z.writestr("x/../../../pwn.txt", "!"); z.close()
try:
    sk.install_zip(bad.getvalue()); check("архив с «../» отвергнут", False)
except SkillError:
    check("архив с «../» отвергнут", True)
s.update({"skills": {"disabled": ["morning-brief"]}})
check("выключенный навык не виден модели", "morning-brief" not in sk.prompt() and "hello" in sk.prompt())
print("ALL OK" if ok else "FAIL"); sys.exit(0 if ok else 1)
