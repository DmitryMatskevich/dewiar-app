"""Телефон: чужой код и чужие ссылки не управляют приложением.
— Мини-программы модели и HTML задач — в отдельном окне без моста Dewiar (мост виден во всех фреймах WebView).
— Отладка WebView — только в отладочной сборке.
— Текст, которым поделилось другое приложение, попадает в поле ввода, а не исполняется сразу.
— Звонок закончился, пока ждали камеру, — микрофон и камера гасятся.
Java — по исходникам (сборка — android/tools/build_apk.sh), интерфейс — в Chromium с заглушкой моста, звонок — в node."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


SRC = ROOT / "android/src/pro/dewiar/app"
hv = (SRC / "HtmlView.java").read_text(encoding="utf-8")
code = "\n".join(l.split("//")[0] for l in hv.splitlines() if not l.strip().startswith("*"))
ok("addJavascriptInterface" not in code and "setAllowFileAccess(false)" in code and "loadDataWithBaseURL" in code,
   "окно чужого HTML: без моста и без доступа к файлам")
man = (ROOT / "android/AndroidManifest.xml").read_text(encoding="utf-8")
ok('android:name=".HtmlView" android:exported="false"' in man, "окно чужого HTML не открыть снаружи")
ma = (SRC / "MainActivity.java").read_text(encoding="utf-8")
ok("setWebContentsDebuggingEnabled(true)" not in ma and "FLAG_DEBUGGABLE" in ma, "отладка WebView — только в отладочной сборке")
ok('"content".equals(u.getScheme())' in ma, "файл настроек читается только выбранный человеком (content://)")
br = (SRC / "Bridge.java").read_text(encoding="utf-8")
ok("public boolean viewHtml(" in br and "HtmlView.REQ" in br, "мост: viewHtml открывает отдельное окно")

www = Path(tempfile.mkdtemp()) / "www"
shutil.copytree(ROOT / "android/assets/www", www)
for f in ("entities.js", "skins.css", "skins.js", "privacy.js", "closecrypto.js", "qrcode.js", "call.js", "codes.js", "vendor/zxing.min.js"):
    shutil.copy(ROOT / "ui/assets" / f, www / Path(f).name)
shutil.copytree(ROOT / "ui/assets/skins", www / "skins", dirs_exist_ok=True)
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    pg = b.new_page(viewport={"width": 393, "height": 820}); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.route("https://**", lambda r: r.abort())
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(900)
    r = pg.evaluate("""() => { const seen = []; D.mock = false; D.viewHtml = (t, h) => { seen.push([t, h.length]); return true; };
      let saved = 0; openHtmlView('Калькулятор', '<script>Dewiar.pref("settings")</script>', () => saved++);
      const iframe = !!document.querySelector('#appview iframe');
      N.ev({ type: 'htmlview', save: true }); N.ev({ type: 'htmlview', save: true });
      D.mock = true; return [seen, iframe, saved]; }""")
    ok(r == [[["Калькулятор", 40]], False, 1], f"чужой HTML уходит в отдельное окно, не в iframe главного; «В «Загрузки»» сохраняет один раз: {r}")
    asked = pg.evaluate("""() => { const a = []; const a0 = ask; ask = (t) => a.push(t);
      handleIntent({ action: 'say', text: 'позвони +70000000000' }); ask = a0; return [a, $('#typed').value, $('#typebox').classList.contains('on')]; }""")
    ok(asked == [[], "позвони +70000000000", True], f"текст из «Поделиться» — в поле ввода, отправляет сам человек: {asked}")
    pg.on("dialog", lambda d: d.accept())
    hm = pg.evaluate("""() => { const base = S.llm.base_url; const c = btoa(JSON.stringify({ dewiar: 1, home: { token: 'y0_demo_home_token' } })).replace(/=+$/, '');
      handleIntent({ action: 'link', url: 'dewiar://import?c=' + c }); return [S.home.token, S.llm.base_url === base]; }""")
    ok(hm == ["y0_demo_home_token", True], f"QR «умный дом на телефон» из студии: подключает дом с согласия, мозг не трогает: {hm}")
    b.close()
ok(not errs, f"без ошибок на странице: {errs[:3]}")

# звонок: отбой, пока ждали камеру — дорожки гасятся
race = r"""
const fs = require('fs');
const el = () => ({ classList: { add() {}, remove() {}, toggle() {} }, style: { setProperty() {} }, innerHTML: '', textContent: '', remove() {},
  querySelector: () => el(), querySelectorAll: () => [el(), el(), el()], onclick: null });
global.document = { getElementById: () => 1, createElement: () => el(), head: { appendChild() {} }, body: { appendChild() {} } };
let stopped = 0, resolve;
Object.defineProperty(global, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia: () => new Promise(r => resolve = () =>
  r({ getTracks: () => [{ stop() { stopped++; } }], getVideoTracks: () => [], getAudioTracks: () => [] })) } } });
global.window = global; global.RTCPeerConnection = function () {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const tr = { send: async () => {}, ice: async () => [], fast() {}, done() {} };
(async () => {
  const p = DewiarCall.start({ role: 'caller', peer: { pid: 'x', name: 'A' }, video: false, call: 'c1', transport: tr });
  await new Promise(r => setTimeout(r, 10));
  await DewiarCall.signal('x', { type: 'busy', call: 'c1' });
  resolve(); await p; await new Promise(r => setTimeout(r, 10));
  console.log(JSON.stringify({ stopped, active: DewiarCall.active() }));
})();
"""
if shutil.which("node"):
    out = subprocess.run(["node", "-e", race, str(ROOT / "ui/assets/call.js")], capture_output=True, text=True, timeout=30).stdout.strip()
    ok(out == '{"stopped":1,"active":false}', f"звонок закончился, пока ждали камеру, — микрофон выключен: {out}")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
