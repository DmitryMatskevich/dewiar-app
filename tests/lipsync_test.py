"""Живое лицо (Лира, Патрик) говорит губами только под звук (отзыв 14.10: «голос с сервера приходит через 7–10 секунд,
а они уже болтают губами»). ui/assets/entities.js на настоящих слоях лица:
— виджет компьютера (voiceLevels): «speaking», а звука ещё нет — рот закрыт все 2,5 с ожидания; пошёл уровень своего
  голоса — рот двигается; звук кончился — через полсекунды закрыт;
— телефон (audible): то же по началу и концу фразы; уровень 0,3, который телефон ставит в «speaking», рот не открывает;
— облик без сведений о звуке (студия) — как раньше: рот «говорит», пока speaking;
— приложение телефона: до начала фразы облик знает, что звука нет; tts_start — звучит, tts_done — тишина, «стоп» — тишина."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


tmp = Path(tempfile.mkdtemp())
(tmp / "page.html").write_text(f"""<!doctype html><meta charset="utf-8"><body style="background:#000">
<canvas id="a" style="width:240px;height:240px"></canvas><canvas id="b" style="width:240px;height:240px"></canvas>
<canvas id="c" style="width:240px;height:240px"></canvas>
<script src="{(ROOT / 'ui/assets/entities.js').as_uri()}"></script>""", encoding="utf-8")

# наблюдатель: каждые 40 мс записывает, открыт ли рот (mTarget ≠ 0) у трёх обликов
WATCH = """(ms) => new Promise(done => { const out = { a: [], b: [], c: [] }; const t0 = performance.now();
  const iv = setInterval(() => { for (const k of ["a", "b", "c"]) out[k].push(!!(E[k]._live && E[k]._live.mTarget));
    if (performance.now() - t0 > ms) { clearInterval(iv); done(out); } }, 40); })"""
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page()
    pg.on("pageerror", lambda x: errs.append(str(x)))
    pg.goto((tmp / "page.html").as_uri())
    pg.evaluate("""() => { window.E = {
        a: new DewiarEntity(document.getElementById("a"), { style: "photo", color: "#9BD4FF", live: "lira", voiceLevels: true }),
        b: new DewiarEntity(document.getElementById("b"), { style: "photo", color: "#FFB547", live: "patrik" }),
        c: new DewiarEntity(document.getElementById("c"), { style: "photo", color: "#9BD4FF", live: "lira" }) };
      E.b.set({ audible: false }); }""")
    pg.wait_for_timeout(1200)                                   # слои лица загрузились
    pg.evaluate("() => { E.a.set({ state: 'speaking' }); E.b.set({ state: 'speaking', level: 0.3 }); E.c.set({ state: 'speaking', level: 0.3 }); }")
    w = pg.evaluate(WATCH, 2500)
    ok(len(w["a"]) > 40 and not any(w["a"]), f"компьютер: ответ готов, звука ещё нет — рот закрыт все 2,5 с ({sum(w['a'])} из {len(w['a'])})")
    ok(not any(w["b"]), f"телефон: до начала фразы рот закрыт, уровень 0,3 его не открывает ({sum(w['b'])} из {len(w['b'])})")
    ok(sum(w["c"]) > 5, f"облик без сведений о звуке (студия) — говорит, как раньше ({sum(w['c'])} из {len(w['c'])})")
    # пошёл звук
    pg.evaluate("""() => { window.lv = setInterval(() => E.a.set({ level: 0.25 + Math.random() * 0.4, voice: true }), 66);
                           E.b.set({ audible: true }); }""")
    w = pg.evaluate(WATCH, 1500)
    ok(sum(w["a"]) > 5, f"компьютер: пошёл свой голос — рот двигается ({sum(w['a'])} из {len(w['a'])})")
    ok(sum(w["b"]) > 5, f"телефон: фраза звучит — рот двигается ({sum(w['b'])} из {len(w['b'])})")
    # звук кончился, а «speaking» ещё держится (пауза между фразами, следующая фраза ещё синтезируется)
    pg.evaluate("() => { clearInterval(lv); E.a.set({ level: 0, voice: false }); E.b.set({ audible: false }); }")
    pg.wait_for_timeout(600)
    w = pg.evaluate(WATCH, 1500)
    ok(not any(w["a"]) and not any(w["b"]), f"пауза между фразами — рот закрыт: компьютер {sum(w['a'])}, телефон {sum(w['b'])}")
    ok(pg.evaluate("() => [E.a.sounding(), E.b.sounding(), E.c.sounding()]") == [False, False, True], "sounding(): звучит ли голос прямо сейчас")
    b.close()

# приложение телефона: облик узнаёт о начале и конце фраз
www = tmp / "www"
shutil.copytree(ROOT / "android/assets/www", www)
shutil.copy(ROOT / "ui/assets/entities.js", www / "entities.js")
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page()
    pg.on("pageerror", lambda x: errs.append(str(x)))
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(800)
    r = pg.evaluate("""() => { const o = []; const snap = () => o.push([orb._audibleKnown, orb._audible]);
        snap(); setState("speaking"); snap();
        N.ev({ type: "tts_start", id: "s1" }); snap();
        N.ev({ type: "tts_start", id: "s2" }); N.ev({ type: "tts_done", id: "s1" }); snap();
        N.ev({ type: "tts_done", id: "s2" }); snap();
        N.ev({ type: "tts_start", id: "s3" }); stopVoice(); snap();
        return o; }""")
    ok(r == [[True, False], [True, False], [True, True], [True, True], [True, False], [True, False]],
       f"телефон: звука нет до tts_start, звучит до последнего tts_done, «стоп» — тишина: {r}")
    b.close()
ok(not errs, "без ошибок JavaScript" + (f": {errs}" if errs else ""))
shutil.rmtree(tmp, ignore_errors=True)
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
