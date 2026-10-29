"""Радио «играет», а звука нет (отзыв 13.10, Ubuntu: «включи музыку» — «уже играет рок», а тишина). ffmpeg жив, но
станция зависла на подключении — звук не приходит:
— Radio.sounding различает «процесс жив» и «правда звучит»;
— play_radio ждёт звук и, не дождавшись, берёт другую станцию, а не докладывает «играет»;
— «включи музыку», когда радио молчит, — включает заново, а не «уже играет»."""
import os, sys, tempfile, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
d = tempfile.mkdtemp(); os.environ.update(DEWIAR_CONFIG_DIR=d + "/cfg", DEWIAR_STATE_DIR=d + "/st")
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


if os.name == "nt":
    print("Windows — пропускаю"); sys.exit(0)
tmp = Path(tempfile.mkdtemp())
hang = tmp / "ffmpeg-hang"; hang.write_text("#!/bin/sh\nexec sleep 60\n"); hang.chmod(0o755)          # подключается вечно
good = tmp / "ffmpeg-good"; good.write_text("#!/bin/sh\nexec head -c 2000000 /dev/zero\n"); good.chmod(0o755)
from audio_io import Player  # noqa: E402
import radio as R  # noqa: E402
import tools as T  # noqa: E402

FF = {"p": str(hang)}
r = R.Radio(lambda: Player(device="", null=True), lambda: FF["p"])
r.play({"name": "Зависшая", "url": "http://127.0.0.1:9/x", "tags": "rock"})
time.sleep(1.5)
ok(r.playing and not r.sounding, "ffmpeg жив, звука нет — «играет», но не звучит")
ok(not r.wait_sound(1.5), "дождаться звука — не вышло")
r.stop()


class Music:
    def match(self, q):
        return [{"name": "Зависшая", "url": "http://a/1", "tags": "rock"}, {"name": "Живая", "url": "http://a/2", "tags": "rock"}]


plays = []
orig = r.play


def play(st):
    FF["p"] = str(hang) if st["name"] == "Зависшая" else str(good)
    plays.append(st["name"]); orig(st)


r.play = play
out = T.TOOLS["play_radio"][3]({"query": "рок"}, {"radio": r, "music": Music()})
ok(plays == ["Зависшая", "Живая"] and "Живая" in out and r.sounding, f"play_radio: зависшая станция — сразу другая, доклад правдивый: {plays} {out[:60]}")
r.stop()
FF["p"] = str(hang); plays.clear()
try:
    T.TOOLS["play_radio"][3]({"query": "рок"}, {"radio": r, "music": type("M", (), {"match": lambda self, q: [{"name": "Зависшая", "url": "http://a/1", "tags": ""}]})()})
    ok(False, "одна зависшая станция — должна быть ошибка, а не «играет»")
except T.ToolError as e:
    ok("не играет" in str(e) and not r.playing, f"одна зависшая станция — честно «не играет», процесс остановлен: {e}")
src = (ROOT / "core/engine_music.py").read_text(encoding="utf-8")
ok("self.radio.playing and not self.radio.sounding" in src and "Радио молчало — включаю заново" in src, "«включи музыку», когда радио молчит, — включает заново")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
