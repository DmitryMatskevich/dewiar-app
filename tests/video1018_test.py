"""Конспект видео по ссылке (18.10, комментарий под видео: «вставить ссылку на YouTube или Rutube, сделать конспект и
сохранить вместе со ссылкой»; ответ автора «должно справляться» — а Лира получала только описание: страница YouTube
текста ролика не отдаёт). Теперь read_webpage для ссылки на видео отдаёт текст из субтитров (core/video.py, yt-dlp):
свои субтитры лучше автоматических, русские — лучше прочих; длинное видео — отрывками по всему ролику с метками
времени. Субтитров нет — честно: название и описание, «не выдумывай». Новых инструментов и правил в подсказке нет.
Телефон: страница видео — только описание, и прямо сказано, что текста ролика нет (конспект — компьютеру)."""
import json, os, shutil, subprocess, sys, tempfile, types
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


import video as V  # noqa: E402
urls = {"https://www.youtube.com/watch?v=lXRiHj9j9Pk": True, "https://youtu.be/lXRiHj9j9Pk": True, "https://m.youtube.com/shorts/abc": True,
        "https://rutube.ru/video/0123abcd/": True, "https://vk.com/video-1_2": True, "https://www.youtube.com/@MrDewiar": False,
        "https://dewiar.pro/dwrapp/": False, "https://habr.com/ru/articles/1/": False}
ok({u: V.is_video(u) for u in urls} == urls, "ссылки на видео узнаются: YouTube (watch, youtu.be, shorts), Rutube, VK; канал и статьи — нет")

j3 = json.dumps({"events": [{"tStartMs": 0, "segs": [{"utf8": "Всем привет."}]}, {"tStartMs": 1500, "segs": [{"utf8": "Всем привет."}]},
                            {"tStartMs": 3000, "segs": [{"utf8": "Сегодня "}, {"utf8": "про ИИ."}]}, {"tStartMs": 4000}]})
vtt = "WEBVTT\nKind: captions\n\n00:00:01.000 --> 00:00:03.000\n<c>Раз</c> два\n\n01:02:03.500 --> 01:02:05.000\nтри\n"
ok(V._parse(j3, "json3") == [(0.0, "Всем привет."), (3.0, "Сегодня про ИИ.")] and V._parse(vtt, "vtt") == [(1, "Раз два"), (3723, "три")],
   "субтитры json3 и vtt — текст с метками, без повторов и тегов")
info = {"subtitles": {"en": [{"ext": "vtt", "url": "u-en"}]}, "automatic_captions": {"ru": [{"ext": "json3", "url": "u-ru-tr"}], "ru-orig": [{"ext": "json3", "url": "u-ru"}]}}
ok(V._pick(info)[0]["url"] == "u-ru" and V._pick({"subtitles": {"ru": [{"ext": "vtt", "url": "own"}]}, "automatic_captions": {"ru-orig": [{"ext": "json3", "url": "a"}]}})[0]["url"] == "own"
   and V._pick({"automatic_captions": {}}) is None, "выбор: свои русские → автоматические русские (исходные) → английские; нет — None")


class FakeYDL:
    INFO = {}

    def __init__(self, opts): self.opts = opts
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def extract_info(self, url, download=False): assert download is False and self.opts.get("skip_download"); return FakeYDL.INFO


sys.modules["yt_dlp"] = types.SimpleNamespace(YoutubeDL=FakeYDL)
bodies = {}
V.httpx = types.SimpleNamespace(get=lambda u, **k: types.SimpleNamespace(text=bodies[u], raise_for_status=lambda: None))
FakeYDL.INFO = {"title": "Я сделал Джарвиса", "channel": "Михаил Исаев", "duration": 4433, "description": "Описание",
                "automatic_captions": {"ru-orig": [{"ext": "json3", "url": "cap"}]}}
bodies["cap"] = j3
tr = V.transcript("https://www.youtube.com/watch?v=lXRiHj9j9Pk")
out = V.for_model("https://www.youtube.com/watch?v=lXRiHj9j9Pk", tr)
ok("Видео «Я сделал Джарвиса» — Михаил Исаев, 1:13:53" in out and "[0:00] Всем привет. Сегодня про ИИ." in out and "автоматические" in out
   and "сохрани заметкой вместе со ссылкой" in out, f"короткое видео — весь текст с метками и ссылкой: {out[:160]}")
long = json.dumps({"events": [{"tStartMs": i * 5000, "segs": [{"utf8": f"Фраза номер {i} про разные вещи и подробности."}]} for i in range(3000)]})
bodies["cap"] = long
out = V.for_model("https://youtu.be/x", V.transcript("https://youtu.be/x"))
import re as _re  # noqa: E402
stamps = [int(h) * 3600 + int(m) * 60 for h, m in _re.findall(r"\[(\d+):(\d{2}):\d{2}\]", out)]
ok(len(out) < 30000 and "[0:00]" in out and stamps and max(stamps) > 3 * 3600 and "отрывки по всему видео" in out,
   f"длинное видео — отрывки по всему ролику (и из конца), в пределах контекста: {len(out)} знаков")
FakeYDL.INFO = {"title": "Без субтитров", "description": "Про котов", "automatic_captions": {}}
try:
    V.transcript("https://rutube.ru/video/1/"); ok(False, "без субтитров — исключение")
except V.NoTranscript as e:
    t = V.no_transcript_text("https://rutube.ru/video/1/", e)
    ok("нет субтитров" in t and "Про котов" in t and "Не выдумывай" in t, f"без субтитров — честно: название, описание, не выдумывать: {t[:120]}")

import tools  # noqa: E402
seen = []
V.transcript = lambda u, timeout=25: seen.append(u) or {"title": "T", "channel": "", "duration": 0, "lang": "ru", "auto": False, "lines": [(0, "Текст ролика")]}
r = tools.t_read_webpage({"url": "https://youtu.be/abc"}, {})
ok(seen == ["https://youtu.be/abc"] and "Текст ролика" in r, "read_webpage: ссылка на видео — текст ролика, без новых инструментов")
del sys.modules["yt_dlp"]
import importlib  # noqa: E402
V2 = importlib.reload(V)
sys.modules["yt_dlp"] = None                     # библиотеки нет
try:
    V2.transcript("https://youtu.be/abc"); ok(False, "нет yt-dlp — исключение")
except V2.NoTranscript as e:
    ok("yt-dlp" in str(e), "нет yt-dlp — понятная причина, а не падение")
ok("yt-dlp" in (ROOT / "requirements-extra.txt").read_text(encoding="utf-8"), "yt-dlp ставится с программой (необязательной библиотекой)")

# ---------- телефон ----------
from playwright.sync_api import sync_playwright  # noqa: E402
tmp = Path(tempfile.mkdtemp()); www = tmp / "www"
shutil.copytree(ROOT / "android/assets/www", www)
for x in ("entities.js", "speechnorm.js", "skins.css", "skins.js"):
    shutil.copy(ROOT / "ui/assets" / x, www / x)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(1000)
    r = pg.evaluate("""async () => { readPage = async (u) => "Описание видео";                 // eslint-disable-line
      const v = await runTool("read_webpage", { url: "https://www.youtube.com/watch?v=lXRiHj9j9Pk" }), s = await runTool("read_webpage", { url: "https://habr.com/ru/articles/1/" });
      return [v, s]; }""")
    ok("Описание видео" in r[0] and "не выдумывай" in r[0] and "без текста ролика" in r[0] and r[1] == "Описание видео",
       f"телефон: страница видео — честно, что это только описание; статьи — как раньше: {r[0][:140]}")
    ok(not errs, f"без ошибок: {errs[:2]}")
    b.close()
shutil.rmtree(tmp, ignore_errors=True)
print("\nИТОГ:", "всё прошло" if not fails else f"не прошло {len(fails)}")
sys.exit(1 if fails else 0)
