"""Обновление пакета с настройками сервера: включать сервер уже настроенному пользователю, только если он отвечает."""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), "8133"]); time.sleep(0.8)
ok = True
for label, url, expect in (("сервер отвечает → включён", "http://127.0.0.1:8133/v1", "srv"),
                           ("сервер недоступен → добавлен, но прежняя модель осталась", "http://127.0.0.1:9/v1", "old")):
    tmp = Path(tempfile.mkdtemp()); (tmp / "cfg").mkdir()
    (tmp / "cfg/settings.json").write_text(json.dumps({"persona": "patrik", "stt": {"engine": "vosk"}, "tts": {"engine": "espeak"},
        "llm": {"active": "old", "profiles": {"old": {"name": "old", "kind": "openai", "base_url": "http://127.0.0.1:8133/v1"}}}}))
    (tmp / "prov.json").write_text(json.dumps({"dewiar": 1, "name": "Dewiar Server", "revision": 1, "apply_on_upgrade": True, "persona": "dewiar",
        "llm": {"active": "srv", "profiles": {"srv": {"name": "srv", "kind": "openai", "base_url": url, "model": "chat"}}}}))
    env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT="8784", DEWIAR_PROVISION=str(tmp / "prov.json"))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "8", "-sample_fmt", "s16", str(tmp / "s.wav")])
    core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(tmp / "s.wav"), "--null-audio"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(9); core.terminate(); core.wait()
    s = json.loads((tmp / "cfg/settings.json").read_text())
    good = s["llm"]["active"] == expect and "srv" in s["llm"]["profiles"] and "old" in s["llm"]["profiles"] and s["provision"]["packaged_rev"] == 1
    good &= (s["persona"] == "dewiar") == (expect == "srv")
    ok &= good; print(("✅" if good else "❌"), label, "| активна:", s["llm"]["active"], "| персонаж:", s["persona"])
mock.terminate()
print("ALL OK" if ok else "FAIL"); sys.exit(0 if ok else 1)
