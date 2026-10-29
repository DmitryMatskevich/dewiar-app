"""№231 (24.10, телефон): «попросил напоминание на 27.10 — создала на завтра (24.10)», и так три раза. Телефон не понимал
дат: «на 27» читал как час (27 — нет такого часа → «не понял время»), а «27 октября в 10» — как «в 10» без дня, то есть
завтра. Теперь parseWhen понимает «27 октября», «на 27.10», «27.10.2026», «2026-10-27», «27-го», «27 числа»; число даты
не путает со временем, «в 10.30» — по-прежнему время."""
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
NOW = "new Date(2026, 9, 24, 11, 25)"          # суббота, 24 октября 2026, 11:25 — как в отчёте
CASES = [
    ("27 октября в 10:00", "2026-10-27 10:00"),
    ("27 октября", "2026-10-27 09:00"),
    ("на 27.10", "2026-10-27 09:00"),
    ("на 27.10 в 15:30", "2026-10-27 15:30"),
    ("можешь 27 октября поставить напоминание", "2026-10-27 09:00"),
    ("27.10.2026 в 10", "2026-10-27 10:00"),
    ("2026-10-27 10:00", "2026-10-27 10:00"),
    ("на 27-е в 8 утра", "2026-10-27 08:00"),
    ("27 числа в 12", "2026-10-27 12:00"),
    ("5 января в 9", "2027-01-05 09:00"),          # прошло в этом году — в следующем
    ("на 2-е", "2026-11-02 09:00"),                # число прошло в этом месяце — в следующем
    ("в 10.30", "2026-10-25 10:30"),               # время, а не дата; уже прошло — завтра
    ("завтра в 9", "2026-10-25 09:00"),
    ("в пятницу в 18:30", "2026-10-30 18:30"),
    ("в 18:30", "2026-10-24 18:30"),
    ("1 мая", "2027-05-01 09:00"),
    ("3 марта в 7 вечера", "2027-03-03 19:00"),
]
try:
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(900)
        for q, want in CASES:
            got = pg.evaluate("""([q, now]) => { const r = parseWhen(q, eval(now)); if (!r) return null; const d = r.at, z = (n) => String(n).padStart(2, '0');
                return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}`; }""", [q, NOW])
            ok(got == want, f"«{q}» → {got} (нужно {want})")
        r = pg.evaluate("(now) => remindWhat(parseWhen('27 октября в 10 перевести деньги Ушаковым', eval(now)).rest)", NOW)
        ok("27" not in r and "октябр" not in r and "перевести деньги" in r, f"о чём напомнить — без даты: «{r}»")
        ok(not errs, f"без ошибок: {errs[:2]}")
        b.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if fails:
    raise SystemExit(1)
