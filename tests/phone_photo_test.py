"""Телефон, съёмка 17.10: «Сфотографируй чек и скажи, сколько всего я заплатил и что самое дорогое» — модель не звала камеру
и выдумывала чек; камера открылась только на «включи камеру и сфотографируй», а снимать пришлось руками. Теперь на
«сфотографируй / сфоткай / сделай снимок / включи камеру» телефон сам снимает (отсчёт 3-2-1) и задаёт вопрос уже со
снимком; «меня / селфи» — фронтальная камера; тёмный кадр или нет камеры — понятный ответ, без выдумок."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


from playwright.sync_api import sync_playwright  # noqa: E402
www = Path(tempfile.mkdtemp()) / "www"
shutil.copytree(ROOT / "android/assets/www", www)
for x in ("entities.js", "speechnorm.js"):
    shutil.copy(ROOT / "ui/assets" / x, www / x)
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 400, "height": 820}); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(900)
    r = pg.evaluate("""async () => { S.onboarded = true; const got = [], asked = [];
        window.takePhoto = async (f) => { got.push(f); return { ok: true, dark: false, dataUrl: 'data:image/jpeg;base64,/9j/AAA=' }; };
        const real = ask; window.ask = async (t, o = {}) => { asked.push([t, (o.attach || []).map(a => a.kind + ':' + a.url.slice(0, 15))]); if ((o.attach || []).length) return; return real(t, o); };
        await ask('Сфотографируй чек и скажи, сколько всего я заплатил и что в нём самое дорогое');
        await ask('Сделай селфи и скажи, как я выгляжу');
        await ask('Сфотографируй стикер, прочитай и поставь мне напоминание на это время');
        await ask('Расскажи, что ты умеешь');
        const n = got.length, said = []; window.answerLocal = (r) => said.push(r.say);
        window.takePhoto = async () => ({ ok: true, dark: true, dataUrl: 'x' });
        await ask('Сфоткай, что передо мной');
        const dark = said.join(' ');
        return { got, asked, dark, n }; }""")
    ok(r["got"][:3] == ["environment", "user", "environment"], f"«сфотографируй чек / стикер» — основная камера, «селфи» — фронтальная, снимает сам: {r['got']}")
    withimg = [a for a in r["asked"] if a[1]]
    ok(len(withimg) == 3 and all(a[1] == ["image:data:image/jpeg"] for a in withimg) and withimg[0][0].startswith("Сфотографируй чек"),
       f"вопрос уходит модели уже со снимком: {withimg}")
    ok(r["n"] == 3, "обычный вопрос камеру не включает")
    ok("почти чёрным" in r["dark"], f"тёмный кадр — понятный ответ, без выдумок: {r['dark'][:60]}")
    ok(not errs, f"без ошибок на странице: {errs[:3]}")
    b.close()
print("\nИТОГ:", "всё прошло" if not fails else f"не прошло {len(fails)}")
sys.exit(1 if fails else 0)
