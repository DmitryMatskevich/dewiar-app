"""Облики в окнах не грузят компьютер: в студии (много обликов на странице) в покое — один неподвижный кадр, оживают
при наведении; когда памяти мало (status.tight), облик в виджете рисуется редко. Считаем кадры в Chromium."""
import os, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


d = Path(tempfile.mkdtemp())
(d / "p.html").write_text(f"""<!doctype html><meta charset=utf-8><body style="background:#111">
<script src="{(ROOT / 'ui/assets/entities.js').as_uri()}"></script><script>
window.cnt = {{}};
window.mk = (id, opt) => {{ const c = document.createElement("canvas"); c.style.cssText = "width:120px;height:120px;display:inline-block";
  document.body.appendChild(c); const e = new DewiarEntity(c, opt); const ctx = c.getContext("2d"), cr = ctx.clearRect.bind(ctx);
  cnt[id] = 0; ctx.clearRect = (...a) => {{ cnt[id]++; return cr(...a); }}; return e; }};
window.E = {{}};
["nexus", "rings", "particles", "orbital", "strings", "holobrain", "lattice", "aura", "tactical", "brain"].forEach((s, i) => E["s" + i] = mk("s" + i, {{ style: s, color: "#8B8CF7", still: true }}));
E.live = mk("live", {{ style: "nexus", color: "#8B8CF7" }});
E.calm = mk("calm", {{ style: "nexus", color: "#8B8CF7" }}); E.calm.calm = true;
</script>""", encoding="utf-8")
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto((d / "p.html").as_uri())
    pg.wait_for_timeout(2200)                                # первые 1,5 с облик дорисовывается (фото, лица) — потом замирает
    pg.evaluate("() => Object.keys(cnt).forEach(k => cnt[k] = 0)")
    pg.wait_for_timeout(2000)
    c = pg.evaluate("() => cnt")
    still = sum(c[f"s{i}"] for i in range(10))
    ok(still == 0, f"десять обликов студии в покое — ни одного кадра за 2 с: {still}")
    ok(c["live"] >= 30, f"облик без «still» живёт как раньше: {c['live']} кадров за 2 с")
    ok(0 < c["calm"] <= 15, f"мало памяти (calm) — облик рисуется редко: {c['calm']} кадров за 2 с")
    pg.evaluate("() => { E.s3.set({ state: 'speaking', level: .35 }); cnt.s3 = 0; }")
    pg.wait_for_timeout(1000)
    ok(pg.evaluate("() => cnt.s3") >= 20, "наведение — облик оживает")
    pg.evaluate("() => E.s3.set({ state: 'idle', level: 0 })")
    pg.wait_for_timeout(1700)
    pg.evaluate("() => { cnt.s3 = 0; }")
    pg.wait_for_timeout(1000)
    ok(pg.evaluate("() => cnt.s3") == 0, "мышь ушла — плавно вернулся в покой и замер")
    ok(not errs, f"без ошибок: {errs[:2]}")
    b.close()
src = (ROOT / "ui/studio/personas.js").read_text(encoding="utf-8")
ok(src.count("still: true") >= 3 and "studioTight" in (ROOT / "ui/studio/brain.js").read_text(encoding="utf-8")
   and "st.tight" in (ROOT / "ui/hud.html").read_text(encoding="utf-8"), "студия: карточки и стили — неподвижные; виджет и студия слушают «мало памяти»")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
print("ALL OK" if not fails else "")
sys.exit(1 if fails else 0)
