"""Портрет смотрит на человека — «параллакс» без камеры (просьба 14.10):
— телефон (по умолчанию выключено, «Оформление» → «Портрет следит за наклоном»): наклон (deviceorientation) → диск чуть поворачивается к человеку, лицо сдвигается в глубину, глаза живого лица
  переводятся (DewiarEntity.look); важна перемена наклона, а не поза — через пару секунд всё возвращается к центру;
  выключатель «Портрет следит за наклоном», «уменьшить движение» и альбомный экран — датчик не слушаем;
— компьютер: только по желанию (студия, ui.cursor_look, по умолчанию выключено — 14.10: «на компьютере не нравится»);
  телефон — без увеличения лица («еле вмещается в круге»)."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


tmp = Path(tempfile.mkdtemp()); www = tmp / "www"
shutil.copytree(ROOT / "android/assets/www", www)
for f in ("entities.js", "rich.js"):
    shutil.copy(ROOT / "ui/assets" / f, www / f)
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
errs = []
ORIENT = "(o) => window.dispatchEvent(Object.assign(new Event('deviceorientation'), { alpha: 0, beta: o[0], gamma: o[1] }))"
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 400, "height": 820})
    pg.on("pageerror", lambda x: errs.append(str(x)))
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(1200)
    ok(pg.evaluate("() => TILT.on") is False, "телефон: по умолчанию наклон выключен (отзыв 14.10: «быстро надоедает»)")
    pg.evaluate("() => { S.tilt = true; tiltSync(); }")
    ok(pg.evaluate("() => TILT.on"), "включили в «Оформлении» — датчик наклона слушаем")
    pg.evaluate(ORIENT, [55, 0]); pg.wait_for_timeout(100)
    for _ in range(6):                                     # повернули вправо (правый край от себя) и чуть к себе
        pg.evaluate(ORIENT, [61, 10]); pg.wait_for_timeout(40)
    pg.wait_for_timeout(500)
    T = pg.evaluate("() => ({ x: TILT.x, y: TILT.y, lx: orb.lookX, ly: orb.lookY, sm: orb._lx, looking: orb._looking, tr: document.getElementById('orb').style.transform })")
    ok(T["x"] < -0.4 and T["y"] > 0.2 and T["looking"] and T["sm"] < -0.2, f"наклон → «зритель» слева и снизу, лицо смотрит туда: {T}")
    ok("rotateY(-" in T["tr"] and "rotateX(-" in T["tr"], f"диск чуть повернулся к человеку: {T['tr']}")
    ok(abs(T["x"]) <= 1 and abs(float(T["tr"].split("rotateY(")[1].split("deg")[0])) <= 7.01, "поворот — не больше 7°")
    for _ in range(40):                                    # держит неподвижно — портрет возвращается к центру
        pg.evaluate(ORIENT, [61, 10]); pg.wait_for_timeout(100)
    back = pg.evaluate("() => [TILT.x, TILT.y]")
    ok(abs(back[0]) < 0.35 and abs(back[1]) < 0.35, f"держит неподвижно 4 с — к центру (важна перемена, а не поза): {back}")
    pg.evaluate("() => { S.tilt = false; tiltSync(); }")
    off = pg.evaluate("() => [TILT.on, document.getElementById('orb').style.transform, orb._looking]")
    ok(off == [False, "", False], f"выключили — датчик не слушаем, портрет прямо: {off}")
    pg.evaluate("() => { S.tilt = true; tiltSync(); }")
    pg.evaluate("() => { openSheet('#sh-more'); }") if pg.evaluate("() => !!document.getElementById('sh-more')") else None
    b2 = b.new_page(viewport={"width": 400, "height": 820}, reduced_motion="reduce")
    b2.goto((www / "index.html").as_uri()); b2.wait_for_timeout(1000)
    ok(b2.evaluate("() => { S.tilt = true; tiltSync(); return TILT.on; }") is False, "«уменьшить движение» в системе — без наклона, даже если включён")
    b2.close()
    # компьютер: эффект убран (отзыв 14.10: «на компьютере не нравится») — курсор не двигает портрет
    hw = b.new_page(viewport={"width": 300, "height": 420})
    hw.on("pageerror", lambda x: errs.append("виджет: " + str(x)))
    hw.goto((ROOT / "ui/hud.html").as_uri() + "?token=x&port=1&mode=card"); hw.wait_for_timeout(1000)
    box = hw.evaluate("() => { const r = document.getElementById('cv').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }")
    hw.mouse.move(box[0] + 120, box[1] - 60); hw.wait_for_timeout(800)
    H = hw.evaluate("() => ({ looking: entity._looking, lx: entity.lookX, tr: document.getElementById('cv').style.transform })")
    ok(H == {"looking": False, "lx": 0, "tr": ""}, f"компьютер по умолчанию: курсор над виджетом — портрет неподвижен: {H}")
    hw.evaluate("() => applyLook({ cursor_look: true })"); hw.mouse.move(box[0] - 100, box[1] + 40); hw.wait_for_timeout(600)
    H2 = hw.evaluate("() => ({ looking: entity._looking, lx: entity.lookX, gain: entity.lookGain, tr: document.getElementById('cv').style.transform })")
    ok(H2["looking"] and H2["lx"] < 0 and H2["gain"] == 0.5 and H2["tr"] == "", f"включили в студии — смотрит на курсор, вдвое мягче, круг не поворачивается: {H2}")
    hw.evaluate("() => applyLook({})")
    ok(hw.evaluate("() => entity._looking") is False, "выключили — сразу прямо")
    b.close()
ok('data-k="ui.cursor_look"' in (ROOT / "ui/settings.html").read_text(encoding="utf-8") and '"cursor_look": False' in (ROOT / "core/settings.py").read_text(encoding="utf-8"),
   "студия: «Портрет смотрит на курсор» — переключатель, по умолчанию выключен")
ent = (ROOT / "ui/assets/entities.js").read_text(encoding="utf-8")
ok("const gk = e.lookGain, z = 1, ox" in ent, "телефон: живое лицо не увеличивается — портрет целиком, как раньше (отзыв 14.10)")
ok(not errs, "без ошибок JavaScript" + (f": {errs}" if errs else ""))
shutil.rmtree(tmp, ignore_errors=True)
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
