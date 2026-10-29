"""Телефон: прокси для серверов Dewiar. Java-часть (NetProxy — без зависимостей от Android) — на обычной JDK против
того же стенда, что и компьютер: свой «dewiar.pro» по HTTPS, SOCKS5 с логином, HTTP-прокси (CONNECT).
Интерфейс (app.js) — в Chromium: разбор адреса, скрытый пароль, нерабочий прокси не сохраняется, адреса своего сайта."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
TESTS = Path(__file__).resolve().parent
for k in [k for k in os.environ if k.lower().endswith("_proxy")]:
    os.environ.pop(k)
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
if not shutil.which("javac"):
    print("нет JDK — пропускаю"); print("ALL OK"); sys.exit(0)
tmp = Path(tempfile.mkdtemp())
sys.path.insert(0, str(TESTS))
import proxykit  # noqa: E402
K = proxykit.start(tmp)
SOCKS = f"socks5://anna:s3cret@127.0.0.1:{K.SP}"

src = tmp / "src/pro/dewiar/app"; src.mkdir(parents=True)
shutil.copy(ROOT / "android/src/pro/dewiar/app/NetProxy.java", src)
(src / "ProxyMain.java").write_text('''package pro.dewiar.app;
import java.io.*; import java.net.*;
public class ProxyMain { public static void main(String[] a) throws Exception {
  BufferedReader in = new BufferedReader(new InputStreamReader(System.in, "UTF-8")); String line;
  while ((line = in.readLine()) != null) {
    String[] p = line.split(" ", 3);
    try {
      if (p[0].equals("set")) { NetProxy.set(p[1].equals("-") ? "" : p[1], p.length > 2 ? p[2] : ""); System.out.println("OK"); }
      else if (p[0].equals("uses")) System.out.println(NetProxy.uses(new URL(p[1])));
      else { HttpURLConnection c = (HttpURLConnection) NetProxy.open(new URL(p[1])); c.setConnectTimeout(5000); c.setReadTimeout(5000);
        int code = c.getResponseCode(); InputStream s = code >= 400 ? c.getErrorStream() : c.getInputStream();
        String b = s == null ? "" : new String(s.readAllBytes(), "UTF-8"); System.out.println(code + " " + b.replace("\\n", " ")); }
    } catch (Throwable e) { System.out.println("ERR " + e); }
    System.out.flush();
  }
}}
''')
env = {k: v for k, v in os.environ.items() if k != "JAVA_TOOL_OPTIONS"}
subprocess.run(["javac", "-d", str(tmp / "cls")] + [str(p) for p in src.glob("*.java")], check=True, env=env, capture_output=True)
subprocess.run(["keytool", "-importcert", "-noprompt", "-alias", "ca", "-file", K.CA, "-keystore", str(tmp / "ts.jks"), "-storepass", "changeit"],
               check=True, env=env, capture_output=True)
URL = "https://dewiar.pro/dwrapp/latest.json"
cmds = ["set -", f"get {URL}", f"set {SOCKS}", f"get {URL}", f"get http://127.0.0.1:{K.PLAIN}/dwrapp/latest.json",
        "uses https://cdn.dewiar.pro/x", "uses https://example.com/x", f"get https://my.site.test/dwrapp/latest.json",
        f"set {SOCKS} https://my.site.test/dwrapp,http://127.0.0.1:9", f"get https://my.site.test/dwrapp/latest.json", "uses http://127.0.0.1:9/x",
        f"set http://127.0.0.1:{K.HP}", f"get {URL}", f"set socks5://anna:wrong@127.0.0.1:{K.SP}", f"get {URL}", "set socks5://host",
        f"set http://user1:pa%20ss@127.0.0.1:{K.HPA}", f"get {URL}", f"get {URL}", f"set http://user1:wrong@127.0.0.1:{K.HPA}", f"get {URL}",
        f"set http://127.0.0.1:{K.HPF}", f"get {URL}", f"get {URL}"]
marks = {}


def at(i):
    """Сколько раз прокси трогали к моменту команды i — снимаем по ходу выполнения."""
    return marks.get(i)


# «Провайдер блокирует DNS»: телефон сам адрес dewiar.pro не узнаёт. Обычная JDK тогда отдаёт прокси имя сайта — как Android
# (OkHttp для SOCKS всегда передаёт имя, адрес узнаёт прокси). Без этого JDK узнала бы IP сама и пошла на настоящий dewiar.pro.
(tmp / "hosts").write_text("127.0.0.1 localhost\n")
p = subprocess.Popen(["java", "-cp", str(tmp / "cls"), f"-Djdk.net.hosts.file={tmp / 'hosts'}", f"-Djavax.net.ssl.trustStore={tmp / 'ts.jks'}", "-Djavax.net.ssl.trustStorePassword=changeit",
                      "-Djdk.http.auth.proxying.disabledSchemes=", "pro.dewiar.app.ProxyMain"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
out = []
for i, c in enumerate(cmds):
    marks[i] = (K.SEEN["socks"], K.SEEN["http"], len(K.SEEN["site"]))
    p.stdin.write(c + "\n"); p.stdin.flush()
    out.append(p.stdout.readline().strip())
marks[len(cmds)] = (K.SEEN["socks"], K.SEEN["http"], len(K.SEEN["site"]))
p.stdin.close(); p.wait(10)
o = dict(enumerate(out))
d = lambda i, k: marks[i + 1][k] - marks[i][k]          # сколько раз за команду i тронули прокси/сайт
ok(d(1, 2) == 0 and d(1, 0) == 0, f"без прокси — напрямую, наш сайт за прокси не тронут: {o[1][:60]}")
ok(o[3].startswith("200") and "0.0.1" in o[3] and d(3, 0) == 1, f"https://dewiar.pro через SOCKS5 с логином: {o[3][:40]}")
ok(o[4].startswith("200") and d(4, 0) == 0, "другие адреса — напрямую, мимо прокси")
ok(o[5] == "true" and o[6] == "false", "поддомены dewiar.pro — через прокси, чужие сайты — нет")
ok(not o[7].startswith("200") and d(7, 0) == 0, "свой сайт без настройки — не через прокси")
ok(o[9].startswith("200") and d(9, 0) == 1 and o[10] == "false", "свой сайт аккаунта — через прокси; 127.0.0.1 — никогда")
ok(o[12].startswith("200") and d(12, 1) == 1, f"HTTP-прокси (CONNECT) для https: {o[12][:40]}")
ok(o[14].startswith("ERR") and d(14, 2) == 0, f"неверный пароль прокси — ошибка, а не обход напрямую: {o[14][:70]}")
ok(o[15].startswith("ERR") and "адрес и порт" in o[15] or "socks5://" in o[15], f"адрес без порта — понятная ошибка: {o[15][:90]}")
ok(o[17].startswith("200") and o[18].startswith("200") and d(17, 1) >= 1, f"HTTP-прокси с логином (платный, рвёт связь без логина) для https: {o[17][:50]} / {o[18][:30]}")
ok((o[20].startswith("ERR") or o[20].startswith("407")) and d(20, 2) == 0, f"HTTP-прокси, неверный пароль — 407, а не обход: {o[20][:70]}")
ok(o[22].startswith("200") and o[23].startswith("200") and K.SEEN.get("http_502", 0) >= 4,
   f"прокси «не с первого раза» (502 Bad Gateway) — телефон сам повторяет, запрос проходит: {o[22][:30]} / {o[23][:30]} (502: {K.SEEN.get('http_502')})")

# ---------------- интерфейс телефона ----------------
from playwright.sync_api import sync_playwright  # noqa: E402
www = tmp / "www"
shutil.copytree(ROOT / "android/assets/www", www)
shutil.copy(ROOT / "ui/assets/entities.js", www / "entities.js")
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)   # живое лицо Эвы
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
R = {}
with sync_playwright() as pw:
    b = pw.chromium.launch(); pg = b.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.route("https://dewiar.pro/**", lambda r: r.abort())                 # «провайдер блокирует»
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(800)
    R["norm"] = pg.evaluate(f"""() => [normProxy("tg://socks?server=127.0.0.1&port={K.SP}&user=anna&pass=s3cret"), normProxy("10.0.0.5:3128"),
        normProxy("socks://1.2.3.4:1080"), normProxy(""), hideProxy("{SOCKS}")]""")
    R["bad"] = pg.evaluate("""() => ["tg://proxy?server=1&port=443&secret=ee", "ftp://a:21", "socks5://host"].map(t => { try { normProxy(t); return "принят"; } catch (e) { return e.message; } })""")
    R["save_bad"] = pg.evaluate(f"""async () => {{ const res = document.createElement("div"); const r = await saveProxy("{SOCKS}", res, null);
        return [r, res.textContent, S.net.proxy, window._proxy.spec]; }}""")
    R["save_off"] = pg.evaluate("""async () => { const res = document.createElement("div"); S.net.proxy = "socks5://a:b@1.2.3.4:1080"; const r = await saveProxy("", res, null);
        return [r, res.textContent, S.net.proxy, window._proxy.spec, JSON.parse(Dewiar.pref("settings")).net.proxy]; }""")
    R["keep"] = pg.evaluate("""async () => { S.net.proxy = "socks5://a:b@1.2.3.4:1080"; const res = document.createElement("div");
        await saveProxy("socks5://a:•••@1.2.3.4:1080", res, null); return S.net.proxy; }""")
    R["hosts"] = pg.evaluate("""() => { S.account.server = "https://my.site.test"; S.net.proxy = "socks5://1.2.3.4:1080"; applyProxy(); return window._proxy; }""")
    R["login"] = pg.evaluate("""async () => { S.net.proxy = ""; try { await accountLogin("", "anna", "pw"); return "вошли"; } catch (e) { return e.message; } }""")
    pg.evaluate("() => { S.net.proxy = 'socks5://anna:s3cret@127.0.0.1:1080'; openSettings('brain'); }"); pg.wait_for_timeout(300)
    R["card"] = (pg.inner_text("#px-card"), pg.input_value("#px-in"))
    pg.locator("#px-card").screenshot(path=str(tmp / "px.png"))
    R["errs"] = errs
    b.close()
ok(R["norm"] == [SOCKS, "http://10.0.0.5:3128", "socks5://1.2.3.4:1080", "", f"socks5://anna:•••@127.0.0.1:{K.SP}"], f"разбор адреса как на компьютере: {R['norm']}")
ok("MTProto" in R["bad"][0] and all(x != "принят" for x in R["bad"]), f"неподходящие адреса — понятный отказ: {R['bad']}")
ok(R["save_bad"][0] is False and "не сохраняю" in R["save_bad"][1] and R["save_bad"][2] == "" and R["save_bad"][3] == "",
   f"dewiar.pro через прокси не открылся — не сохранён, прокси снят: {R['save_bad'][1][:70]}")
ok(R["save_off"][0] is True and R["save_off"][2] == "" and R["save_off"][3] == "" and R["save_off"][4] == "", "пустое поле — прокси выключен и сохранён")
ok(R["keep"] == "socks5://a:b@1.2.3.4:1080", "«Сохранить» со скрытым паролем не портит прокси")
ok(R["hosts"]["spec"] == "socks5://1.2.3.4:1080" and "https://my.site.test" in R["hosts"]["hosts"], f"свой сайт аккаунта передаётся в Java: {R['hosts']}")
ok("«Сеть»" in R["login"], f"вход не прошёл — понятно, где прокси: {R['login'][-80:]}")
ok("Провайдер блокирует dewiar.pro" in R["card"][0] and R["card"][1] == "socks5://anna:•••@127.0.0.1:1080", f"карточка «Сеть»: пароль скрыт ({R['card'][1]})")
ok(not R["errs"], "без ошибок JavaScript" + (f": {R['errs']}" if R["errs"] else ""))
print(f"   (снимок карточки: {tmp}/px.png)")
