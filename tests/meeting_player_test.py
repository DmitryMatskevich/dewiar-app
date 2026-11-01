"""«Прошлые встречи»: кнопки медиа, проигрыватель (дорожки + кадры + текст по шкале записи), папки в итоге.

Без звуковых устройств: файлы встречи создаются на диске, локальная раздача проверяется настоящими HTTP-запросами.
"""
import json
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from meeting_media import MediaServer, player_data  # noqa: E402
from prompter import Prompter, meeting_frame_preview, meetings_index  # noqa: E402


def wav(path, seconds, amp=3000):
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(16000 * seconds)) / 16000
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.sin(2 * np.pi * 440 * t) * amp).astype(np.int16).tobytes())


with tempfile.TemporaryDirectory() as tmp:
    ws = Path(tmp)
    d = ws / "Встречи"
    stem = "2026-10-30 1412 Демо"
    (d / "Расшифровки" / stem / "Кадры").mkdir(parents=True)
    (d / f"{stem}.md").write_text("# Демо\n\n**Аудиозапись:** [x](dwr-file:x)\n", encoding="utf-8")
    wav(d / "Аудио" / f"{stem}.wav", 3.0)
    wav(d / "Аудио" / stem / "Вы.wav", 3.0, 1500)
    wav(d / "Аудио" / stem / "Собеседник.wav", 3.0, 2500)
    (d / "Расшифровки" / f"{stem}.txt").write_text("# Демо\n\n[00:00] Собеседник: Сколько стоит?\n[00:02] Вы: Сорок восемь\n", encoding="utf-8")
    (d / "Расшифровки" / f"{stem}.lines.json").write_text(json.dumps({"version": 1, "lines": [
        {"who": "them", "text": "Сколько стоит?", "t": 0.4},
        {"who": "screen", "text": "Слайд", "t": 0.5},
        {"who": "me", "text": "Сорок восемь", "t": 2.1}]}, ensure_ascii=False), encoding="utf-8")
    frames = []
    for n, color in ((1, "red"), (2, "blue")):
        p = d / "Расшифровки" / stem / "Кадры" / f"000{n}-abc.jpg"
        Image.new("RGB", (2400, 1350), color).save(p, "JPEG")
        p.with_suffix(".jpg.json").write_text(json.dumps({"audio_seconds": n - 0.5}), encoding="utf-8")
        frames.append(f"{stem}/Кадры/{p.name}")
    # шкала кадров: красный → синий → снова красный (возврат к слайду)
    (d / "Расшифровки" / stem / "frames.jsonl").write_text("\n".join(json.dumps({"path": f, "audio_seconds": t}) for f, t in
                                                                     ((frames[0], 0.0), (frames[1], 1.2), (frames[0], 2.5))) + "\n",
                                                          encoding="utf-8")
    # встреча без звука: причина берётся из протокола
    (d / "2026-10-29 1837 Встреча.md").write_text(
        "# Встреча\n\n**Аудиозапись:** [a](dwr-file:a)\n\n**Статус аудиозаписи:** на Mac нет виртуального входа\n", encoding="utf-8")
    (d / "2026-10-28 1000 Без записи.md").write_text("# Без записи\n", encoding="utf-8")

    # ---------- 1. Список «Прошлые встречи» ----------
    rows = {r["name"]: r for r in meetings_index(d)}
    demo = rows[stem]
    assert demo["audio"] == f"Встречи/Аудио/{stem}.wav"
    assert set(demo["tracks"]) == {"me", "them"} and demo["tracks"]["me"].endswith("/Вы.wav")
    assert demo["frames_count"] == 2 and demo["frames"][0] == f"Встречи/Расшифровки/{stem}/Кадры/0001-abc.jpg"
    assert demo["folder"] == f"Встречи/Расшифровки/{stem}" and demo["transcript"].endswith(".txt")
    assert rows["2026-10-29 1837 Встреча"]["audio"] == "" and "виртуального входа" in rows["2026-10-29 1837 Встреча"]["audio_error"]
    assert rows["2026-10-28 1000 Без записи"]["audio_error"] == "звук этой встречи не записывался"
    assert rows["2026-10-28 1000 Без записи"]["folder"] == "Встречи"

    # ---------- 2. Просмотр кадра: уменьшенный, только из «Кадры» ----------
    pv = meeting_frame_preview(ws, demo["frames"][0])
    assert pv["url"].startswith("data:image/jpeg;base64,") and pv["t"] == 0.5 and len(pv["url"]) < 400_000
    for bad in ("Встречи/" + stem + ".md", "Встречи/Расшифровки/../../x.jpg", "Заметки/a/Кадры/x.jpg"):
        try:
            meeting_frame_preview(ws, bad)
        except ValueError:
            pass
        else:
            raise AssertionError("чужой путь не должен открываться: " + bad)

    # ---------- 3. Проигрыватель: дорожки, кадры по времени, реплики ----------
    media = MediaServer(lambda: d)
    try:
        pd = player_data(d, f"Встречи/{stem}.md", media)
        assert [t["key"] for t in pd["tracks"]] == ["mix", "me", "them"] and abs(pd["tracks"][1]["seconds"] - 3.0) < 0.01
        assert [f["t"] for f in pd["frames"]] == [0.0, 1.2, 2.5] and pd["frames"][2]["name"] == "0001-abc.jpg", pd["frames"]
        assert [(x["who"], x["t"]) for x in pd["lines"]] == [("them", 0.4), ("me", 2.1)], "экранные строки не идут в реплики"
        assert pd["duration"] >= 3.0 and pd["video"] == ""

        # ---------- 4. Локальная раздача: Range для перемотки, ключ, только медиа внутри «Встречи» ----------
        url = pd["tracks"][1]["url"]
        assert url.startswith("http://127.0.0.1:") and media.key in url
        req = urllib.request.Request(url, headers={"Range": "bytes=0-99"})
        with urllib.request.urlopen(req, timeout=5) as r:
            body = r.read()
            assert r.status == 206 and len(body) == 100 and body[:4] == b"RIFF"
            assert r.headers["Content-Range"].endswith("/" + str((d / "Аудио" / stem / "Вы.wav").stat().st_size))
            assert r.headers["Content-Type"] == "audio/wav" and r.headers["Accept-Ranges"] == "bytes"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=-10"}), timeout=5) as r:
            assert r.status == 206 and len(r.read()) == 10
        with urllib.request.urlopen(pd["frames"][0]["url"], timeout=5) as r:
            assert r.headers["Content-Type"] == "image/jpeg" and r.read()[:2] == b"\xff\xd8"
        base = url.split(media.key)[0]
        for bad in (url.replace(media.key, "x" * len(media.key)), f"{base}{media.key}/" + urllib.parse.quote(stem + ".md"),
                    f"{base}{media.key}/../../etc/passwd", f"{base}{media.key}/%2E%2E/%2E%2E/x.wav"):
            try:
                urllib.request.urlopen(bad, timeout=5)
            except urllib.error.HTTPError as e:
                assert e.code == 404, (bad, e.code)
            else:
                raise AssertionError("не должно отдаваться: " + bad)
        try:
            player_data(d, "Встречи/../x.md", media)
        except ValueError:
            pass
        else:
            raise AssertionError("итог вне «Встречи» не открывается")
    finally:
        media.close()

    # ---------- 5. Без .lines.json реплики берутся из .txt ----------
    (d / "Расшифровки" / f"{stem}.lines.json").unlink()
    media = MediaServer(lambda: d)
    try:
        pd = player_data(d, f"Встречи/{stem}.md", media)
        assert [(x["who"], x["t"], x["text"]) for x in pd["lines"]] == [("them", 0, "Сколько стоит?"), ("me", 2, "Сорок восемь")]
    finally:
        media.close()

    # ---------- 6. Итог: раздел «Папки встречи» со ссылками внутри «Встречи» ----------
    eng = SimpleNamespace(workspace=SimpleNamespace(ensure=lambda: ws), s=SimpleNamespace(active_llm=lambda: None, get=lambda *a: {}),
                          events=[], stopping=SimpleNamespace(wait=lambda *_: True))
    eng.emit = lambda *a, **k: eng.events.append((a, k))
    p = Prompter(eng)
    p.meeting = {"title": "Папки", "started": time.time(), "ended": time.time() + 30}
    p._transcript_open("Папки")
    p._record_open("Папки", tracks=("them", "me"))
    p._record_write_tracks(b"\1\0" * 512, b"\2\0" * 512)
    p._record_close()
    sess = Path(p._transcript_path).parent / Path(p._transcript_path).stem
    (sess / "Кадры").mkdir(parents=True)
    p._ask_model = lambda *a, **k: "## Коротко\nок"
    p._summarize()
    body = p.summary
    assert "## Папки встречи" in body, body
    links = [x for x in body.splitlines() if "file://" in x]
    assert any("Кадры:" in x for x in links) and any("Дорожки" in x for x in links) and any("Все встречи:" in x for x in links)
    assert all((ws / "Встречи").resolve().as_uri() in x for x in links), "только папки внутри «Встречи»"
    assert not any("Видео:" in x for x in links), "несуществующая папка не показывается"

    # ---------- 7. Текст записи: шапка со всеми участниками, ссылка на него в итоге ----------
    txt = Path(p._transcript_path).read_text(encoding="utf-8")
    assert "# Участники: не указаны" in txt and "«Вы» — ваш микрофон" not in txt
    assert "**Текст записи встречи (все участники):** [Расшифровки/" in body
    p2 = Prompter(eng)
    p2.meeting = {"title": "Имена", "started": time.time(), "ended": time.time() + 30, "dual": True}
    p2._transcript_open("Имена")
    assert "«Вы» — ваш микрофон, «Собеседник» — звук компьютера" in Path(p2._transcript_path).read_text(encoding="utf-8")
    p2.lines = [{"who": "them", "text": "Анна, пришлите договор.", "t": 1.0}, {"who": "me", "text": "Хорошо, Пётр.", "t": 3.0}]
    for x in p2.lines:
        p2._transcript_write(x)
    p2._ask_model = lambda *a, **k: "## Коротко\nок"
    p2._summarize()
    head = Path(p2._transcript_path).read_text(encoding="utf-8").splitlines()
    assert head[2] == "# Участники: Анна, Пётр", head[:4]
    assert any(ln.startswith("[00:03] Вы: Хорошо, Пётр.") for ln in head)

print("OK: «Прошлые встречи» — звук/дорожки/видео/кадры/папка, проигрыватель с шкалой записи, Range-раздача, папки в итоге")
