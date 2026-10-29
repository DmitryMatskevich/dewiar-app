"""Скриншоты виджета и студии в Chromium против живого ядра (ядро с тестовой моделью)."""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots"); OUT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp()); (tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({"persona": os.environ.get("P", "patrik"),
    "llm": {"active": "a", "profiles": {"a": {"name": "vLLM — домашний сервер", "kind": "openai", "base_url": "http://127.0.0.1:8124/v1", "model": "Qwen3-30B-A3B"},
                                        "b": {"name": "Ollama", "kind": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "llama3.2"}}},
    "stt": {"engine": "vosk"}, "tts": {"engine": "espeak"}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT="8798", DEWIAR_ASSUME_AEC="1")
sil = tmp / "sil.wav"; subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "120", "-sample_fmt", "s16", str(sil)])
core = subprocess.Popen([sys.executable, str(ROOT / "core/main.py"), "--wav", str(sil), "--null-audio"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(4)
tok = (tmp / "cfg/token").read_text().strip()
q = f"?token={tok}&port=8798"
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 340, "height": 520}, device_scale_factor=2)
        pg.goto((ROOT / "ui/hud.html").as_uri() + q)
        pg.add_style_tag(content="html{background:#1b2436 url('') }")
        time.sleep(2)
        pg.evaluate("""() => { entity.set({state:'speaking', level:.55}); state='speaking'; document.getElementById('say').textContent='говорю';
          showCap('Какая погода будет завтра в Москве?', 'Завтра в Москве облачно, около двенадцати градусов, к вечеру возможен небольшой дождь — зонт лучше взять.'); document.body.classList.add('h'); document.getElementById('bar').classList.add('pinned'); }""")
        time.sleep(1.2); pg.screenshot(path=str(OUT / "hud.png"))
        for pid in ["patrik", "lira"]:
            pg.evaluate(f"() => {{ applyPersona('{pid}'); entity.set({{state:'listening', level:.3}}); document.getElementById('cap').classList.remove('show'); document.getElementById('say').textContent='слушаю'; }}")
            time.sleep(0.9); pg.screenshot(path=str(OUT / f"hud-{pid}.png"))
        st = b.new_page(viewport={"width": 1120, "height": 760}, device_scale_factor=1.5)
        st.goto((ROOT / "ui/settings.html").as_uri() + q); time.sleep(2.5)
        st.screenshot(path=str(OUT / "studio-personas.png"))
        st.click('nav button[data-p="brain"]'); time.sleep(0.5); st.click(".prof .head .btn"); time.sleep(0.4)
        st.screenshot(path=str(OUT / "studio-brain.png"))
        st.click('nav button[data-p="personas"]'); time.sleep(0.3); st.click(".card.new"); time.sleep(1.2)
        st.fill("#e-name", "Нова"); st.screenshot(path=str(OUT / "studio-editor.png"))
        st.click('#e-cancel'); st.click('nav button[data-p="voice"]'); time.sleep(0.5)
        st.screenshot(path=str(OUT / "studio-voice.png"))
        st.click('nav button[data-p="hands"]'); time.sleep(0.8)
        st.screenshot(path=str(OUT / "studio-hands.png"))
        pg.evaluate("""() => { applyPersona('dewiar'); entity.set({state:'listening', level:.3}); setState('listening');
          showCap('Удали старые логи из загрузок', 'Выполнить команду: rm ~/Downloads/*.log?'); document.getElementById('act').className='on';
          document.getElementById('act').textContent='Команды терминала…'; document.getElementById('yn').classList.add('on'); }""")
        time.sleep(1); pg.screenshot(path=str(OUT / "hud-confirm.png"))
        errs = []
        b.close()
finally:
    core.terminate(); core.wait()
print("ok", sorted(x.name for x in OUT.iterdir()))
