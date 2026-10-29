"""Пакет здоровья 22.10, №189 (телефон): отчёт «связь с мозгом: openrouter.ai — unexpected end of stream», а ответ на тот
же вопрос пришёл через 5 с — повтор помог. Сбой, который прошёл сам внутри вопроса, теперь не идёт разработчику
отчётом (netHealed), а «unexpected end of stream» с первого раза — старое соединение, которое сервер уже закрыл:
повтор сразу (0,3 с), без ожидания 2 с."""
import json, os, subprocess, sys
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
js = (ROOT / "android/assets/www/app.js").read_text(encoding="utf-8")
a = js.index("function netIncident(e, count = true)")
b = js.index("function problem(sig, summary, err)")
code = """
const P = {}; const prefGet = k => P[k]; const prefSet = (k, v) => { P[k] = v; };
const sent = []; const reportSend = (a, b, t) => { sent.push(t); return Promise.resolve(); };
const hCount = () => {}, hWhy = () => {}, hProblem = () => {}, plog = () => {};
const S = { report: { auto: true } };
""" + js[a:b] + """
const out = {};
netIncident({ url: "https://openrouter.ai/api/v1/chat/completions", raw: "unexpected end of stream on com.android.okhttp.Address@1" }, false);
netHealed(1); netRecovered(); out.healed = sent.length; out.left = P.net_inc;
netIncident({ url: "https://openrouter.ai/x", raw: "timeout" }, false);
netIncident({ url: "https://openrouter.ai/x", raw: "timeout" }, true);
netRecovered(); out.real = sent.slice();
sent.length = 0; P.net_inc = JSON.stringify([{ ts: 1, host: "dewiar.pro", err: 'Unable to resolve host "dewiar.pro": No address associated with hostname', online: false, type: "none" }]);
netRecovered(); out.offline = sent.length; out.offline_left = P.net_inc;
out.stale = STALE_CONN.test("unexpected end of stream on com.android.okhttp.Address@d930ece4") && !STALE_CONN.test("timeout");
console.log(JSON.stringify(out));
"""
r = subprocess.run(["node", "-e", code], capture_output=True, text=True)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


try:
    o = json.loads(r.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    print(r.stdout, r.stderr); sys.exit(1)
ok(o["healed"] == 0 and o["left"] == "[]", f"сбой, который прошёл сам при повторе, — не отчёт разработчику: {o['healed']}, {o['left']}")
ok(len(o["real"]) == 1 and "2 сбоев" in o["real"][0], f"настоящие сбои (вопрос так и не прошёл) — отчётом, как раньше: {o['real']}")
ok(o["offline"] == 0 and o["offline_left"] == "[]", "телефон был без интернета (имя сайта не находилось) — не отчёт разработчику (№208)")
ok(o["stale"], "«unexpected end of stream» узнаётся как оборванное старое соединение")
ok("STALE_CONN.test(" in js and "? 300 :" in js and "if (tries) netHealed(tries)" in js, "повтор после такого обрыва — сразу; удачный повтор помечает сбои")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
