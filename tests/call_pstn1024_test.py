"""24.10, владелец (телефон): «Позвони на обычный мобильный телефон Исаевой Людмиле» — звонок ушёл контакту «Телефон
На Громова 46»: в поиск контакта уходила вся фраза, и «телефон», «на» совпали точно. Теперь «на обычный / мобильный /
сотовый телефон», «по номеру», «не через Dewiar» — это просьба позвонить обычным звонком (не через «Близких») и не часть
имени; в контакты уходит только имя."""
import os, shutil, subprocess, sys, tempfile
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
shutil.copy(ROOT / "ui/assets/entities.js", www / "entities.js")
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
try:
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(900)
        for q, who, phone in (("на обычный мобильный телефон исаевой людмиле", "исаевой людмиле", True),
                              ("исаевой людмиле на мобильный", "исаевой людмиле", True),
                              ("Людмиле по сотовому", "Людмиле", True),
                              ("маме на номер", "маме", True),
                              ("Людмиле не через Dewiar", "Людмиле", True),
                              ("Людмиле Исаевой", "Людмиле Исаевой", False),
                              ("Наташе МТС", "Наташе МТС", False)):
            r = pg.evaluate("q => callTarget(q)", q)
            ok(r == {"who": who, "phone": phone}, f"«{q}» → кому «{r['who']}», обычным звонком: {r['phone']}")
        res = pg.evaluate("""async () => {
            window._q = []; window._dial = []; window._close = [];
            D.has = () => true; D.contact = (q) => { window._q.push(q); return JSON.stringify({ name: 'Исаева Людмила', number: '+375290000001' }); };
            D.call = (n) => { window._dial.push(n); return true; };
            clCall = async (pid, v) => { window._close.push(pid); };
            clLoad = () => ({ people: [{ pid: 'p1', name: 'Людмила Исаева', devs: ['d'] }] }); window.DewiarCall = window.DewiarCall || {};
            const r1 = await doCall('на обычный мобильный телефон исаевой людмиле');
            const r2 = await doCall('Людмиле Исаевой');
            return { r1: r1.text, r2: r2.text, q: window._q, dial: window._dial, close: window._close }; }""")
        ok(res["q"] == ["исаевой людмиле"] and res["dial"] == ["+375290000001"] and "Исаева Людмила" in res["r1"],
           f"«на обычный мобильный телефон» — обычный звонок, в контакты только имя: {res}")
        ok(res["close"] == ["p1"] and "через Dewiar" in res["r2"], f"без «на телефон» — как раньше, близкому через Dewiar: {res['r2']}")
        ok(not errs, f"без ошибок: {errs[:2]}")
        b.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if fails:
    raise SystemExit(1)
