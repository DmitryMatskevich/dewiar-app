"""Телефон (app.js): голоса персонажей. Было: голос устройства один на всех — даже Патрик говорил женским (только чуть
ниже). Теперь героям — мужской голос устройства (признак пола у голоса или голоса Google по имени: ru-ru-x-rud/ruf —
мужские, ruc/rue — женские), героиням — женский; выбранный в настройках важнее. ElevenLabs своим ключом: голос героя или
героини, ключ — заголовком xi-api-key. Свой сервер голоса — тоже с голосом героев отдельно."""
import json, os, shutil, subprocess, sys, tempfile
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
GOOGLE = [{"name": "ru-ru-x-ruc-local", "offline": True, "features": []}, {"name": "ru-ru-x-rud-local", "offline": True, "features": []},
          {"name": "ru-ru-x-rue-network", "offline": False, "features": []}, {"name": "ru-ru-x-ruf-local", "offline": True, "features": []}]
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto((www / "index.html").as_uri())
    pg.wait_for_timeout(800)
    R = pg.evaluate("""(voices) => {
        const spoken = [], remote = [];
        D.voices = () => JSON.stringify(voices); D.ttsReady = () => true; voicesCache = null;
        D.speak = (id, t, rate, pitch, voice) => spoken.push({ voice, pitch });
        D.speakRemote = (id, url, key, body) => remote.push({ url, key, body: JSON.parse(body) });
        const as = (pid, fn) => { const was = S.persona; S.persona = pid; fn(); S.persona = was; };
        S.tts = Object.assign({}, DEF.tts); S.textMode = false;
        as("patrik", () => say("Привет.")); as("lira", () => say("Привет.")); as("patrik", () => say("Привет."));
        const auto = spoken.splice(0);
        S.tts.sys_voice = "ru-ru-x-rue-network"; as("lira", () => say("Привет.")); as("patrik", () => say("Привет."));
        const chosen = spoken.splice(0);
        S.tts.sys_voice_m = "ru-ru-x-ruf-local"; as("patrik", () => say("Привет."));
        const chosenM = spoken.splice(0);
        D.voices = () => JSON.stringify([{ name: "ru-RU-language", offline: true, features: [] }]); voicesCache = null; S.tts = Object.assign({}, DEF.tts);
        as("patrik", () => say("Привет.")); as("lira", () => say("Привет."));
        const single = spoken.splice(0);
        S.tts = Object.assign({}, DEF.tts, { engine: "eleven", el_key: "sk_test" });
        as("patrik", () => say("Привет.")); as("lira", () => say("Привет."));
        S.tts = Object.assign({}, DEF.tts, { engine: "server", url: "http://10.0.0.5:8091/v1", voice: "nova", voice_m: "onyx" });
        as("patrik", () => say("Привет.")); as("lira", () => say("Привет."));
        D.voices = () => JSON.stringify(voices); voicesCache = null; setTab = "voice"; renderSettings();
        const ui = { el: !!document.querySelector("#tts-eng option[value=eleven]"), m: !!document.querySelector("#tts-voice-m"),
                     marks: document.querySelector("#tts-voice") ? document.querySelector("#tts-voice").textContent : "" };
        return { auto, chosen, chosenM, single, remote, ui };
    }""", GOOGLE)
    b.close()

a = R["auto"]
ok(a[0]["voice"] in ("ru-ru-x-rud-local", "ru-ru-x-ruf-local") and a[0]["pitch"] == 1.0, f"Патрик — мужской голос устройства: {a[0]}")
ok(a[1]["voice"] == "ru-ru-x-ruc-local" and a[1]["pitch"] == 1.0, f"Лира — женский: {a[1]}")
ok(a[2]["voice"] == a[0]["voice"], "Патрик снова — тот же мужской голос")
c = R["chosen"]
ok(c[0]["voice"] == "ru-ru-x-rue-network" and c[1]["voice"] in ("ru-ru-x-rud-local", "ru-ru-x-ruf-local"),
   f"выбран голос героинь — у героинь он, героям всё равно мужской: {c}")
ok(R["chosenM"][0]["voice"] == "ru-ru-x-ruf-local", "выбран голос героев — у героев он")
sg = R["single"]
ok(sg[0]["pitch"] < 1 < sg[1]["pitch"], f"голос в устройстве один — как раньше, героям ниже, героиням выше: {sg}")
rm = R["remote"]
ok("JBFqnCBsd6RMkjVDRZzb" in rm[0]["url"] and "EXAVITQu4vr4xnSDxMaL" in rm[1]["url"] and rm[0]["key"] == "xi-api-key:sk_test"
   and rm[0]["body"]["model_id"] == "eleven_flash_v2_5" and rm[0]["body"]["text"] == "Привет.",
   f"ElevenLabs: голос героя и героини, ключ заголовком xi-api-key: {rm[:2]}")
ok(rm[2]["body"]["voice"] == "onyx" and rm[3]["body"]["voice"] == "nova" and rm[2]["url"].endswith("/audio/speech"),
   f"свой сервер голоса: героям — свой голос: {[x['body']['voice'] for x in rm[2:]]}")
ok(R["ui"]["el"] and R["ui"]["m"] and "муж." in R["ui"]["marks"] and "жен." in R["ui"]["marks"], f"настройки: ElevenLabs, голос героев, пометки пола: {R['ui']}")
ok(not errors, "без ошибок JavaScript" + (f": {errors}" if errors else ""))
print("ALL OK" if not fails else f"ошибок: {len(fails)}")
sys.exit(1 if fails else 0)
