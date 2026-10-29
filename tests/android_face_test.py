"""Телефон: уведомления от лица персонажа. Интерфейс рисует облик (фото или облик в круге с буквой имени) и отдаёт его
Dewiar.setFace — Faces.java кладёт его в крупный значок уведомления и на экран будильника; напоминание помнит, кто его
ставил (поле who), чтобы и через неделю пришло от Лиры, а не от того, кто отвечает сейчас. Картинки — в папку из аргумента."""
import base64, os, shutil, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
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
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 393, "height": 852}); errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.add_init_script("window._png = {}")
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(800)
    R = pg.evaluate("""async () => {
        const keep = Dewiar.setFace; Dewiar.setFace = (id, n, c, png) => { window._png[id] = png; keep(id, n, c, png); };
        const ids = PERSONAS.map(p => p.id), out = {};
        for (const id of ids) { S.persona = id; applyPersona(); }
        await new Promise(r => setTimeout(r, 800));
        S.persona = ids[1] || ids[0]; applyPersona();
        window._r = await runTool("set_reminder", { text: "Полить цветы", in_minutes: 60 });
        const who = JSON.parse(Dewiar.alarms()).map(a => a.who);
        return { ids, faces: window._faces, who, cur: S.persona, names: Object.fromEntries(PERSONAS.map(p => [p.id, p.name])) };
    }""")
    ok(set(R["ids"]) <= set(R["faces"] or {}), f"облик каждого персонажа отдан телефону: {sorted((R['faces'] or {}).keys())}")
    ok(all(f["png"] > 2000 and f["name"] == R["names"][i] for i, f in R["faces"].items()), "в облике — картинка, имя и цвет")
    ok(R["who"] == [R["cur"]], f"напоминание помнит, кто его ставил: {R['who']}")
    pngs = pg.evaluate("window._png")
    for pid, b64 in pngs.items():
        raw = base64.b64decode(b64)
        ok(raw[:8] == b"\x89PNG\r\n\x1a\n", f"{pid}: картинка — PNG")
        if SHOTS:
            (SHOTS / f"face-{pid}.png").write_bytes(raw)
    ok(not errors, f"без ошибок {errors}")
    b.close()
src = (ROOT / "android/src/pro/dewiar/app")
ok("Faces.dress" in (src / "AlarmReceiver.java").read_text() and "Faces.dress" in (src / "Bridge.java").read_text(),
   "будильники и сообщения ассистента одеваются в облик (Faces.dress)")
print("ИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
