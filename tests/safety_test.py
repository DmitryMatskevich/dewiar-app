"""Безопасность и компьютер голосом: защита не даёт удалить системные папки, домашнюю папку, «всё» и стереть диск;
обычное удаление — только в корзину и с вопросом; скрипты задач не удаляют вне своей папки; «громче», «тише»,
«выключи звук», «выключи компьютер» (через минуту, «отмени выключение»), «заблокируй экран» — без модели."""
import asyncio, json, os, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
sys.path.insert(0, str(ROOT / "core"))
HOME = tmp / "home"
(HOME / "Документы").mkdir(parents=True)
(HOME / "Загрузки").mkdir()
os.environ["HOME"] = str(HOME)
os.environ["XDG_DATA_HOME"] = str(HOME / ".local/share")
import guard  # noqa: E402
from pc import parse  # noqa: E402

c = guard.check_command
blocked = ["rm -rf /", "sudo rm -rf / --no-preserve-root", "rm -rf ~", "rm -rf $HOME", "rm -rf ~/*", "rm -rf *",
           "rm -rf /usr/lib", "rm -rf ~/Документы", "rm -r ~/Загрузки/*", "mkfs.ext4 /dev/sda1", "dd if=/dev/zero of=/dev/sda",
           ":(){ :|:& };:", "cd /tmp && rm -rf /etc", "Remove-Item -Recurse -Force C:\\Windows", "format c:",
           "bash -c 'rm -rf /boot'", "find / -delete", "rm -rf /home"]
bad = [x for x in blocked if c(x)["level"] != "block" and not (x.startswith("find") and c(x)["level"] == "warn")]
ok(not bad, f"опасные команды запрещены ({len(blocked)} видов: корень, домашняя папка, «всё», системные папки, диск)"
   + (f" — пропущены: {bad}" if bad else ""))
fine = ["ls ~", "df -h", "echo привет", "git status", "python3 -V", "cat /etc/os-release"]
ok(all(c(x)["level"] == "ok" for x in fine), "обычные команды не мешают: ls, df, git status, cat")
w = c(f"rm {HOME}/Загрузки/старое.txt")
ok(w["level"] == "warn" and "без корзины" in w["reason"], "удаление обычного файла командой — только с предупреждением")
ok(c("sudo apt remove firefox")["level"] == "warn" and c("shutdown now")["level"] == "warn",
   "sudo, удаление программ, выключение — с предупреждением в вопросе")

# корзина
f = HOME / "Загрузки" / "старое.txt"
f.write_text("x")
d = HOME / "Загрузки" / "проект"
(d / "src").mkdir(parents=True)
(d / "src/a.py").write_text("print(1)")
plan = guard.plan_delete([str(f), str(d)])
q = guard.describe_delete(plan)
ok("в корзину" in q.lower() and "проект" in q and "Восстановить" in q, f"вопрос понятный: {q}")
os.environ["PATH"] = "/nonexistent"                      # без gio — своя корзина по стандарту freedesktop
guard.trash(plan)
tr = HOME / ".local/share/Trash"
ok(not f.exists() and not d.exists() and (tr / "files/старое.txt").exists() and (tr / "files/проект/src/a.py").exists()
   and (tr / "info/старое.txt.trashinfo").exists(), "удалённое лежит в корзине — можно восстановить")
os.environ["PATH"] = "/usr/bin:/bin"
blocked = []
for p_ in (HOME, HOME / "Документы", "/", "/usr", "/etc/hosts"):
    try:
        guard.plan_delete([str(p_)])
    except guard.Blocked:
        blocked.append(p_)
ok(len(blocked) == 5, f"домашнюю папку, «Документы», корень, системные файлы удалить нельзя даже в корзину (отказано {len(blocked)} из 5)")
guard.WORKSPACE[:] = [tmp / "ws"]
(tmp / "ws/Заметки").mkdir(parents=True)
(tmp / "ws/Заметки/один.md").write_text("1")
try:
    guard.plan_delete([str(tmp / "ws/Заметки")]); ok(False, "папка ассистента защищена")
except guard.Blocked:
    ok(bool(guard.plan_delete([str(tmp / "ws/Заметки/один.md")])), "папка ассистента и её разделы защищены, отдельная заметка — можно")

# скрипт задачи
task = tmp / "task"
(task / "outputs").mkdir(parents=True)
(task / "outputs/tmp.txt").write_text("1")
victim = HOME / "Документы" / "важное.txt"
victim.write_text("не трогать")
code = (f"import os, shutil, pathlib\nos.remove('outputs/tmp.txt')\nprint('свой файл удалён')\n"
        f"try:\n    pathlib.Path({str(victim)!r}).unlink()\nexcept PermissionError as e:\n    print('ЗАПРЕТ', e)\n"
        f"try:\n    shutil.rmtree({str(HOME / 'Документы')!r})\nexcept PermissionError as e:\n    print('ЗАПРЕТ2')\n")
(task / "s.py").write_text(code, encoding="utf-8")
env = dict(os.environ, PYTHONPATH=guard.site_dir(task / "_run"), DEWIAR_ALLOW_DEL=str(task.resolve()))
r = subprocess.run([sys.executable, "s.py"], cwd=task, env=env, capture_output=True, text=True)
ok("свой файл удалён" in r.stdout and "ЗАПРЕТ" in r.stdout and "ЗАПРЕТ2" in r.stdout and victim.exists(),
   "скрипт задачи удаляет только в своей папке; чужие файлы — запрет" + ("" if "ЗАПРЕТ2" in r.stdout else f" ({r.stdout}{r.stderr[-300:]})"))

# голосом
cases = {"громче": {"op": "volume", "change": 10}, "Сделай потише": {"op": "volume", "change": -10},
         "громкость 40": {"op": "volume", "percent": 40}, "громкость на пятьдесят процентов": {"op": "volume", "percent": 50},
         "выключи звук": {"op": "volume", "mute": True}, "включи звук": {"op": "volume", "mute": False},
         "выключи компьютер": {"op": "power", "action": "shutdown"}, "Перезагрузи ноутбук": {"op": "power", "action": "reboot"},
         "переведи компьютер в спящий режим": {"op": "power", "action": "sleep"}, "заблокируй экран": {"op": "power", "action": "lock"},
         "отмени выключение": {"op": "power_cancel"}}
wrong = {k: parse(k) for k, v in cases.items() if parse(k) != v}
ok(not wrong, f"фразы поняты: громче, тише, громкость 40, выключи звук, выключи компьютер, сон, блокировка ({len(cases)})"
   + (f" — ошибки: {wrong}" if wrong else ""))
ok(all(parse(x) is None for x in ("выключи свет", "выключи музыку", "громче всех кричал петух", "тише едешь — дальше будешь")),
   "чужие фразы не путает: «выключи свет», «выключи музыку», поговорки")

# сквозной: ядро
import websockets  # noqa: E402
PORT, LLM_PORT = 8817, 8147
dry = tmp / "dry.jsonl"
(tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "dewiar", "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False, "follow_up_seconds": 0},
    "llm": {"active": "m", "profiles": {"m": {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "model": "chat"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT),
           DEWIAR_ASSUME_AEC="1", DEWIAR_WORKSPACE_DIR=str(tmp / "ws2"), DEWIAR_DRY_TOOLS=str(dry), PATH="/usr/bin:/bin")
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM_PORT)])
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
events, R = [], {}
old = HOME / "Загрузки" / "черновик.txt"
old.write_text("x")


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
    await call("say", text="Громче")
    R["vol"] = await wait_for(lambda e: e["type"] == "answer" and e.get("done") and e.get("text"), 8)
    events.clear()
    await call("say", text="Выключи компьютер")
    R["pw"] = await wait_for(lambda e: e["type"] == "power" and e.get("action") == "shutdown" and e.get("seconds") == 60, 8)
    R["pw_say"] = await wait_for(lambda e: e["type"] == "answer" and "через минуту" in e.get("text", ""), 8)
    await asyncio.sleep(1)
    await call("say", text="Отмени выключение")
    R["cancel"] = await wait_for(lambda e: e["type"] == "power" and e.get("cancelled"), 8)
    events.clear()
    await call("say", text="Удали через терминал всё из домашней папки")
    R["guard"] = await wait_for(lambda e: e["type"] == "tool" and e.get("status") == "error" and "защита" in e.get("error", ""), 15)
    R["guard_ans"] = await wait_for(lambda e: e["type"] == "answer" and e.get("done"), 15)
    R["no_confirm"] = not any(e["type"] == "confirm" for e in events)
    events.clear()
    await call("say", text=f"Удали файл {old}")
    R["ask"] = await wait_for(lambda e: e["type"] == "confirm", 15)
    await call("confirm_answer", ok=True)
    R["done"] = await wait_for(lambda e: e["type"] == "tool" and e.get("name") == "delete_files" and e.get("status") in ("done", "error"), 15)
    rd.cancel()

try:
    asyncio.run(run())
finally:
    for p in (core, mock):
        p.terminate()
    core.wait()
log = (tmp / "core.log").read_text(encoding="utf-8")
dl = dry.read_text(encoding="utf-8") if dry.exists() else ""
ok(R.get("vol") and '["volume"' in dl, "«громче» — сразу, без модели")
ok(R.get("pw") and R.get("pw_say") and R.get("cancel") and '["power", "shutdown"]' not in dl,
   "«выключи компьютер» — через минуту с отсчётом в виджете; «отмени выключение» — отменено, компьютер не выключен")
ok(R.get("guard") and R.get("no_confirm") and "Защита" in log, "«удали всё через терминал» — защита отказала сразу, даже не спрашивая")
ok(R.get("ask") and "корзин" in R["ask"].get("text", "").lower() and R.get("done") and R["done"].get("status") == "done"
   and '["trash"' in dl, "«удали файл» — вопрос «в корзину?», после «да» — в корзину")
