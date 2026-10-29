"""Модели со своего сайта (dewiar.pro/dwrapp): publish_update.py --models собирает папку из «исходных сайтов»
(здесь — локальные подделки alphacephei и Hugging Face), ядро берёт оттуда Vosk, GigaAM, Vosk TTS и Piper
с проверкой sha256; подмена отвергается; без сайта — последний сохранённый список."""
import functools, json, os, shutil, subprocess, sys, tempfile, threading, zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
SRC = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
os.environ.update(DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_CONFIG_DIR=str(tmp / "cfg"))
sys.path.insert(0, str(ROOT / "core"))
from updater import Updater, DEFAULT_URL  # noqa: E402
from settings import MODELS_DIR, VOICES_DIR  # noqa: E402

def ok(c, m): print(("✅ " if c else "❌ ") + m)

www = tmp / "www"
srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(SimpleHTTPRequestHandler, directory=str(www)))
SimpleHTTPRequestHandler.log_message = lambda *a: None
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"

# «исходные сайты»
alpha = www / "alpha"; alpha.mkdir(parents=True)
real_vosk = next((p for p in [Path("/opt/dewiar/data/vosk-model-small-ru-0.22.zip"), SRC / "data/vosk-model-small-ru-0.22.zip"] if p.exists()), None)
if real_vosk:
    shutil.copy(real_vosk, alpha / "vosk-model-small-ru-0.22.zip")
(alpha / "model-list.json").write_text(json.dumps([{"name": "vosk-model-tts-ru-0.9-multi"}, {"name": "vosk-model-tts-ru-0.10-multi"},
                                                   {"name": "vosk-model-small-ru-0.22"}]))
with zipfile.ZipFile(alpha / "vosk-model-tts-ru-0.10-multi.zip", "w") as z:
    z.writestr("vosk-model-tts-ru-0.10-multi/model.onnx", b"onnx" * 100)
    z.writestr("vosk-model-tts-ru-0.10-multi/config.json", "{}")
hf = www / "hf"
g = hf / "istupakov/gigaam-v3-onnx/resolve/main"; g.mkdir(parents=True)
gfiles = ["config.json", "v3_e2e_rnnt_encoder.int8.onnx", "v3_e2e_rnnt_decoder.int8.onnx", "v3_e2e_rnnt_joint.int8.onnx",
          "v3_e2e_rnnt_vocab.txt", "v3_e2e_rnnt_encoder.onnx", "v3_ctc.int8.onnx"]
for n in gfiles:
    (g / n).write_bytes(n.encode() * 50)
(hf / "api/models/istupakov").mkdir(parents=True)
(hf / "api/models/istupakov/gigaam-v3-onnx").write_text(json.dumps({"siblings": [{"rfilename": n} for n in gfiles]}))
for vid in ["ru_RU-denis-medium", "ru_RU-dmitri-medium", "ru_RU-ruslan-medium", "ru_RU-irina-medium"]:
    _, name, q = vid.split("-", 2)
    d = hf / f"rhasspy/piper-voices/resolve/main/ru/ru_RU/{name}/{q}"; d.mkdir(parents=True)
    (d / f"{vid}.onnx").write_bytes(vid.encode() * 1000); (d / f"{vid}.onnx.json").write_text("{}")

site = www / "dwrapp"
deb = tmp / "dewiar-assistant_99.2.0_all.deb"; deb.write_bytes(b"!<arch>\n" + os.urandom(3000))
env = dict(os.environ, DEWIAR_SRC_ALPHACEPHEI=f"{base}/alpha", DEWIAR_SRC_HF=f"{base}/hf")
r = subprocess.run([sys.executable, str(SRC / "tools/publish_update.py"), str(deb), str(site), "--models"], capture_output=True, text=True, env=env)
man = json.loads((site / "latest.json").read_text())
m = man.get("models", {})
ok(r.returncode == 0 and man["version"] == "99.2.0" and {"gigaam", "vosk_tts", "piper"} <= set(m), "publish_update --models: пакет и модели в папке сайта")
ok(m["vosk_tts"]["name"] == "vosk-model-tts-ru-0.10-multi" and man["mirrors"]["vosk_tts"] == m["vosk_tts"]["url"]
   and all(isinstance(v, str) for v in man["mirrors"].values()), "голоса — самая свежая модель; зеркало для клиентов 0.11.0 — строкой")
with zipfile.ZipFile(site / m["gigaam"]["url"]) as z:
    names = sorted(z.namelist())
ok(names == sorted(["config.json", "v3_e2e_rnnt_encoder.int8.onnx", "v3_e2e_rnnt_decoder.int8.onnx", "v3_e2e_rnnt_joint.int8.onnx",
                    "v3_e2e_rnnt_vocab.txt"]), "GigaAM: в архиве только нужные файлы int8")
ok(len(m["piper"]["files"]) == 8, "Piper: 4 голоса с суммами")
if real_vosk:
    ok(m.get("vosk_small", {}).get("sha256") == "961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4", "модель имён Vosk — с проверкой суммы")
r2 = subprocess.run([sys.executable, str(SRC / "tools/publish_update.py"), str(site), "--models"], capture_output=True, text=True, env=env)
man2 = json.loads((site / "latest.json").read_text())
ok(r2.returncode == 0 and man2["version"] == "99.2.0" and man2["sha256"] == man["sha256"], "повторный запуск без пакета: версия и пакет не тронуты")

url = f"{base}/dwrapp/latest.json"
class S:
    def get(self, *k): return url if k == ("updates", "url") else None
u = Updater(S(), "0.11.2", tmp / "st")
gm = u.model("gigaam")
ok(gm and gm["url"] == f"{base}/dwrapp/models/gigaam-v3-e2e-rnnt-int8.zip" and len(gm["sha256"]) == 64, "ядро: адрес модели на сайте — полный, с суммой")

from stt import GigaAMSTT  # noqa: E402
st = GigaAMSTT(); st.mirror = gm
ok(not st.cached(), "GigaAM ещё не скачан")
st._from_mirror()
ok(st._local_ok() and st.cached() and (MODELS_DIR / "gigaam-v3-e2e-rnnt" / "v3_e2e_rnnt_vocab.txt").exists(), "GigaAM скачан с сайта в свою папку")

from tts import PiperTTS, VoskTTS  # noqa: E402
pt = PiperTTS(); pt.mirror = u.model("piper")
pt.ensure("ru_RU-irina-medium")
ok(pt.has("ru_RU-irina-medium") and (VOICES_DIR / "ru_RU-irina-medium.onnx").read_bytes().startswith(b"ru_RU-irina"), "Piper: голос с сайта")

vt = VoskTTS(MODELS_DIR); vm = u.model("vosk_tts"); vt.mirror, vt.mirror_sha = vm["url"], vm["sha256"]
vt._load = lambda path, synth=None: setattr(vt, "path", path)
vt.ensure()
ok(vt.path and vt.path.name == "vosk-model-tts-ru-0.10-multi", "Vosk TTS: модель с сайта, проверена по sha256")

if real_vosk:
    from listen import ensure_vosk  # noqa: E402
    d = ensure_vosk(tmp / "nodata", MODELS_DIR, mirror=u.model("vosk_small")["url"])
    ok((d / "am").exists(), "модель имён Vosk — с сайта")

# подмена на сайте
(site / m["gigaam"]["url"]).write_bytes(b"PK-fake")
shutil.rmtree(MODELS_DIR / "gigaam-v3-e2e-rnnt")
try:
    st._from_mirror(); bad = False
except RuntimeError as e:
    bad = "контрольная сумма" in str(e)
ok(bad and not st._local_ok(), "подменённый архив GigaAM отвергнут")

class Down:
    def get(self, *k): return f"{base}/nope/latest.json" if k == ("updates", "url") else None
u2 = Updater(Down(), "0.11.2", tmp / "st")
ok(u2.model("piper") and u2.model("gigaam"), "сайт недоступен — последний сохранённый список моделей")

class Old:
    def get(self, *k): return "https://dewiar.pro/dwr/latest.json" if k == ("updates", "url") else None
ok(Updater(Old(), "0.11.2", tmp / "st2").url == DEFAULT_URL == "https://dewiar.pro/dwrapp/latest.json", "старый адрес dwr → dwrapp")
