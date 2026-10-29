"""Студия целиком: открыть на живом ядре каждый раздел меню и его вкладки — без ошибок JavaScript, каждый раздел
показывает своё. Отпечаток разделов (заголовки, кнопки, поля) пишется в файл из STUDIO_FP — чтобы сравнить студию до и
после переделки кода (например, когда settings.html разбит на файлы): видимое должно совпасть."""
import json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
TESTS = Path(__file__).resolve().parent
PY = os.environ.get("DEWIAR_PY") or sys.executable
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


tmp = Path(tempfile.mkdtemp())
LLM, PORT = free(), free()
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM)])
cfg = tmp / "cfg"; cfg.mkdir()
(cfg / "settings.json").write_text(json.dumps({"persona": "dewiar", "audio": {"sounds": False}, "ui": {"news_seen": "99.0.0", "welcome": "done", "memory_motion": "off"},
                                               "stt": {"engine": "vosk"}, "tts": {"engine": "off"},
                                               "llm": {"active": "m", "profiles": {"m": {"name": "Мок", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM}/v1", "model": "mock"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(cfg), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT), DEWIAR_ASSUME_AEC="1",
           DEWIAR_WORKSPACE_DIR=str(tmp / "ws"), DEWIAR_FAKE_RAM_GB="16", DEWIAR_FAKE_CORES="8")
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
for _ in range(150):
    time.sleep(0.2)
    if (cfg / "token").exists():
        break
time.sleep(2)
tok = (cfg / "token").read_text().strip()
FP, errs = {}, []
try:
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1400, "height": 1000})
        pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
        pg.on("console", lambda m: errs.append("console: " + m.text) if m.type == "error" and "Failed to load resource" not in m.text
              and "net::" not in m.text else None)
        pg.goto((ROOT / "ui/settings.html").as_uri() + f"?token={tok}&port={PORT}"); time.sleep(3)
        ok(pg.evaluate("typeof core === 'object' && document.querySelectorAll('nav button[data-p]').length > 15"), "студия загрузилась и подключилась к ядру")
        pages = pg.evaluate("[...document.querySelectorAll('nav button[data-p]')].map(b => b.dataset.p)")
        fp = """(id) => { const s = document.querySelector('#p-' + id) || document.querySelector('section.cur');
            if (!s) return null;
            const t = (sel) => [...s.querySelectorAll(sel)].filter(e => e.offsetParent !== null).map(e => (e.innerText || e.placeholder || e.id || '').trim().slice(0, 40)).filter(Boolean);
            return { h: t('h1, h2, h3'), btn: t('button').slice(0, 60), inp: [...s.querySelectorAll('input, select, textarea')].map(e => e.id || e.dataset.k || e.name || e.type).slice(0, 80) }; }"""
        for pid in pages:
            pg.evaluate("(p) => { const b = document.querySelector(`nav button[data-p=\"${p}\"]`); b && b.click(); }", pid)
            time.sleep(0.6)
            FP[pid] = pg.evaluate(fp, pid)
            for tab in pg.evaluate("(p) => { const s = document.querySelector('#p-' + p); return s ? [...s.querySelectorAll('.stabs button, .tabs button[data-t]')].filter(e => e.offsetParent !== null).map((e, i) => i) : []; }", pid)[:6]:
                pg.evaluate("([p, i]) => { const s = document.querySelector('#p-' + p); const l = [...s.querySelectorAll('.stabs button, .tabs button[data-t]')].filter(e => e.offsetParent !== null); l[i] && l[i].click(); }", [pid, tab])
                time.sleep(0.3)
        ok(len(FP) >= 15 and all(FP[k] for k in FP), f"открыты все разделы ({len(FP)}): {', '.join(sorted(k for k in FP if not FP[k])) or 'все показали содержимое'}")
        ok(not errs, "без ошибок JavaScript: " + ("; ".join(errs[:5]) if errs else "нет"))
        b.close()
finally:
    core.terminate(); mock.terminate()
    try:
        core.wait(10)
    except subprocess.TimeoutExpired:
        core.kill()
if os.environ.get("STUDIO_FP"):
    Path(os.environ["STUDIO_FP"]).write_text(json.dumps(FP, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
print("ИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
