"""Светлый неоморфизм — облик без серости (отзыв 18.10: «в стиле "Небесный" … иконка Лиры в центре смотрится серо-грязно,
быстрее всего не должно быть внутренней тени, может быть и обводки»). У «Жемчужного», «Небесного» и «Мятного» свой тон
облика "neu": ни тени внутрь, ни белой рамки; за портретом — мягкий свет студии, края портрета растворяются в выпуклом
диске, диск шире портрета (поле того же материала). «Нет связи» — не тёмная пелена, а уход в материал.
Патрик — без чёрного квадрата (фон портрета прозрачный, как у Лиры: tools/live_face.py заливал фон «с макушки» и
молча не красил). Тёмные волосы Патрику пробовали (18.10) — вышло хуже, остались светлые."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


skins = (ROOT / "ui/assets/skins.js").read_text(encoding="utf-8")
for sk in ("neu", "neusky", "neumint"):
    line = next(x for x in skins.splitlines() if f'id: "{sk}"' in x)
    ok('tone: "neu"' in line and "light: true" in line, f"«{sk}»: свой тон облика — светлый неоморфизм")
ent = (ROOT / "ui/assets/entities.js").read_text(encoding="utf-8")
rim = ent.split("function portraitRim", 1)[1][:400]
ok('if (e.tone === "neu") return;' in rim, "у светлого неоморфизма нет ни тени внутрь, ни рамки")
css = (ROOT / "android/assets/www/app.css").read_text(encoding="utf-8")
hud = (ROOT / "ui/hud.html").read_text(encoding="utf-8")
ok("body.neu.light #orbwrap::before { width: 72%; height: 72%; }" in css and "body.neu.light #orb::before { width: 72%; height: 72%; }" in hud,
   "телефон и виджет: диск шире портрета")

from PIL import Image  # noqa: E402
pat = Image.open(ROOT / "ui/assets/live/patrik.webp")
ok(pat.mode == "RGBA" and pat.getpixel((5, 5))[3] == 0 and pat.getpixel((180, 180))[3] == 255,
   f"Патрик: фон прозрачный, лицо на месте: {pat.mode}, {pat.getpixel((5, 5))}, {pat.getpixel((180, 180))}")

from playwright.sync_api import sync_playwright  # noqa: E402
tmp = Path(tempfile.mkdtemp()); www = tmp / "www"
shutil.copytree(ROOT / "android/assets/www", www)
for x in ("entities.js", "speechnorm.js", "skins.css", "skins.js"):
    shutil.copy(ROOT / "ui/assets" / x, www / x)
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)
shutil.copytree(ROOT / "ui/assets/skins", www / "skins", dirs_exist_ok=True)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
with sync_playwright() as pw:
    b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 412, "height": 880}); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(2500)

    def edge(sk, state="idle"):
        """Яркость у края портрета внутри круга и фона рядом с диском (по снимку экрана)."""
        pg.evaluate("([sk, st]) => { S.skin = sk; applyLook(); orb.state = st; }", [sk, state]); pg.wait_for_timeout(700)
        r = pg.evaluate("() => { const b = document.getElementById('orb').getBoundingClientRect(); return [b.left, b.top, b.width]; }")
        pg.screenshot(path=str(tmp / "s.png"))
        im = Image.open(tmp / "s.png").convert("L"); cx, cy, w = r[0] + r[2] / 2, r[1] + r[2] / 2, r[2]
        R = w / 2 * 0.8 * 0.78                         # радиус портрета (entities.js: r = PR·0.8, R = r·0.78)
        inner = sum(im.getpixel((int(cx + s * R * 0.93), int(cy - R * 0.55))) for s in (-1, 1)) / 2
        face = im.getpixel((int(cx), int(cy - R * 0.1)))
        return inner, face
    for sk in ("neusky", "neu", "neumint"):
        inner, face = edge(sk)
        bg = Image.open(tmp / "s.png").convert("L").getpixel((20, 300))
        ok(inner >= bg - 4, f"«{sk}»: у края портрета не темнее фона — нет тени внутрь ({inner:.0f} против фона {bg})")
    _, face_on = edge("neusky", "idle")
    _, face_off = edge("neusky", "offline")
    ok(face_off > face_on, f"«Небесный», нет связи: облик бледнеет, а не темнеет ({face_off} против {face_on})")
    ok(not errs, f"без ошибок на странице: {errs[:2]}")
    b.close()
shutil.rmtree(tmp, ignore_errors=True)                # не оставлять копии страниц в /tmp
print("\nИТОГ:", "всё прошло" if not fails else f"не прошло {len(fails)}")
sys.exit(1 if fails else 0)
