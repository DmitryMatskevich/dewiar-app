"""22.10, владелец: «Встречи — можно и на компе, но чтобы работало с телефона; забавно и прикольно; и чтобы отличалось от
радара». Студия → «Близкие» → «Встречи»: доска с бейджами «Привет, меня зовут…» (не радар). То же пространство, что у
телефона (core: close_meet → /meet/*): отметка с телефона видна здесь; помахать, ответить, написать, заблокировать —
отсюда; отметиться с компьютера — по месту телефона (свежее 30 минут), продлить и изменить — без места (сайт берёт прежнее)."""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time, types, urllib.request
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


# ---------- ядро: close.meet ----------
import close as CM  # noqa: E402
calls = []


class R:
    def __init__(self, d):
        self.d = d
    def json(self):
        return self.d


c = CM.Close.__new__(CM.Close)
c._lock = __import__("threading").Lock()
c._req = lambda m, p, body=None, **k: (calls.append((m, p, body)), R({"ok": True, "me": {}, "people": []}))[1]
c.geo_state = lambda: {"me": None, "people": {}}
try:
    c.meet("/meet/here", {"name": "Миша", "lat": 1, "lon": 2}); err = ""
except CM.CloseError as e:
    err = str(e)
ok("отметьтесь на телефоне" in err and not calls, f"место телефона неизвестно — отметиться отсюда нельзя, сказано почему: {err[:60]}")
c.geo_state = lambda: {"me": {"lat": 53.9, "lon": 27.56, "ts": time.time() * 1000 - 60000}, "people": {}}
c.meet("/meet/here", {"name": "Миша", "lat": 0, "lon": 0})
ok(calls[-1][0] == "POST" and calls[-1][2]["lat"] == 53.9 and calls[-1][2]["lon"] == 27.56, "место — телефона, не то, что прислала страница")
c.geo_state = lambda: {"me": None, "people": {}}
c.meet("/meet/here", {"name": "Миша", "keep": True, "hours": 3})
ok("lat" not in calls[-1][2] and "keep" not in calls[-1][2], "продлить/изменить — без места (сайт возьмёт прежнее)")
c.meet("/meet/state")
ok(calls[-1][:2] == ("GET", "/meet/state"), "состояние — GET")
try:
    c.meet("/close/remove"); bad = False
except CM.CloseError:
    bad = True
ok(bad, "через «Встречи» — только пути /meet/*")

# сайт (продлить и изменить без места) — проверка в meet1021_test

# ---------- студия: доска ----------
from playwright.sync_api import sync_playwright  # noqa: E402
FAKE = "window.pywebview = { api: new Proxy({}, { get: () => () => Promise.resolve(true) }) };"
CORE = r"""
(() => { window.calls = []; const now = Date.now() / 1000;
  window.ST = { me: { adult: true, ban_until: 0, here: { mid: "m0", name: "Миша", about: "люблю джаз", place: "Филармония", since: now - 1800, until: now + 5400 } },
    people: [{ mid: "a1", name: "Оля", about: "впервые на органе", place: "Филармония", since: now - 600, waved: false, waves_me: true, match: false, pid: "" },
             { mid: "b2", name: "Дима", about: "", place: "", since: now - 3000, waved: true, waves_me: false, match: false, pid: "" },
             { mid: "c3", name: "Аня", about: "ищу компанию на кофе", place: "фойе", since: now - 120, waved: false, waves_me: false, match: true, pid: "p9" },
             { mid: "d4", name: "Костя", about: "", place: "", since: now - 60, waved: false, waves_me: false, match: false, pid: "" }], radius_m: 700, max_h: 3 };
  window.GEO = null;
  const ready = () => { if (!window.core) return setTimeout(ready, 5);
    core.retry = () => {}; core.connect = () => {}; if (core.ws) { core.ws.onclose = null; core.ws.onerror = null; try { core.ws.close(); } catch (e) {} }
    core.call = async (c, a) => { calls.push([c, a]);
      if (c === "close_state") return { me: { name: "Миша" }, people: [{ pid: "p9", name: "Аня" }], offers: [], geo: { me: GEO, people: {} } };
      if (c === "close_history") return [];
      if (c === "close_meet") {
        if (a.path === "/meet/wave") { const p = ST.people.find(x => x.mid === a.body.mid); p.waved = !a.body.undo; if (p.waves_me) { p.match = true; p.pid = "p1"; return Object.assign({ match: true, pid: "p1" }, ST); } return ST; }
        if (a.path === "/meet/leave") { ST.me.here = null; return ST; }
        if (a.path === "/meet/block") { ST.people = ST.people.filter(x => x.mid !== a.body.mid); return ST; }
        if (a.path === "/meet/here") { ST.me.here = { mid: "m0", name: a.body.name, about: a.body.about, place: a.body.place, since: Date.now() / 1000, until: Date.now() / 1000 + a.body.hours * 3600 }; return ST; }
        return ST; }
      return {}; }; };
  ready(); })();
"""
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    for theme in ("neusky", "graphite", "glass"):
        pg = b.new_page(viewport={"width": 1300, "height": 900}); pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.add_init_script(FAKE); pg.add_init_script(CORE)
        pg.goto((ROOT / "ui/settings.html").as_uri() + f"?port=1&skin={theme}"); pg.wait_for_timeout(900)
        pg.click('nav button[data-p="close"]'); pg.wait_for_timeout(400)
        pg.click('#cl-tabs button[data-t="meet"]'); pg.wait_for_timeout(600)
        ok(pg.is_visible("#cl-meet .mt-board") and not pg.is_visible(".cl-box") and not pg.is_visible("#cl-near"), f"[{theme}] вкладка «Встречи» — доска, переписка и радар скрыты")
        if theme != "neusky":
            pg.locator("#cl-meet").screenshot(path=f"/tmp/claude-0/meet_{theme}.png"); pg.close(); continue
        tags = pg.locator("#cl-meet .mt-tag")
        ok(tags.count() == 4 and "Филармония" in pg.inner_text(".mt-here") and "видно ещё" in pg.inner_text(".mt-here"), "моя отметка и 4 бейджа")
        ok("Машет вам" in pg.inner_text('.mt-tag[data-mid="a1"] header') and "Вы в «Близких»" in pg.inner_text('.mt-tag[data-mid="c3"] header')
           and "ждём ответа" in pg.inner_text('.mt-tag[data-mid="b2"]'), "кто машет, кто уже в «Близких», кому помахали — видно по бейджу")
        pg.locator("#cl-meet").screenshot(path="/tmp/claude-0/meet_light.png")
        pg.click('.mt-tag[data-mid="a1"] [data-wave]'); pg.wait_for_timeout(500)
        ok("Вы в «Близких»" in pg.inner_text('.mt-tag[data-mid="a1"] header') and "теперь в «Близких»" in pg.inner_text("#toast"), "помахал в ответ — сразу в «Близких»")
        pg.click('.mt-tag[data-mid="d4"] .mt-more'); pg.wait_for_timeout(150)
        pg.click('.mt-tag[data-mid="d4"] [data-block]'); pg.wait_for_timeout(150)
        ok(pg.locator('.mt-tag[data-mid="d4"]').count() == 1, "«Заблокировать» — сначала переспрашиваем")
        pg.click('.mt-tag[data-mid="d4"] [data-block]'); pg.wait_for_timeout(400)
        ok(pg.locator('.mt-tag[data-mid="d4"]').count() == 0, "второе нажатие — заблокирован, с доски ушёл")
        pg.click("#mt-more1"); pg.wait_for_timeout(400)
        c = [x for x in pg.evaluate("calls") if x[0] == "close_meet" and x[1]["path"] == "/meet/here"]
        ok(c and c[-1][1]["body"]["keep"] is True and c[-1][1]["body"]["hours"] == 3, f"+1 час — без места, до 3 ч: {c[-1:]}")
        pg.click('.mt-tag[data-mid="c3"] [data-chat]'); pg.wait_for_timeout(500)
        ok(pg.is_visible(".cl-box") and not pg.is_visible("#cl-meet"), "«Написать» — переписка с этим человеком")
        # ушёл — без места телефона отметиться отсюда нельзя: подсказка про телефон
        pg.click('#cl-tabs button[data-t="meet"]'); pg.wait_for_timeout(400)
        pg.click("#mt-leave"); pg.wait_for_timeout(400)
        ok("Отметьтесь с телефона" in pg.inner_text("#cl-meet"), "без отметки и без места телефона — «отметьтесь с телефона»")
        pg.evaluate("GEO = { lat: 53.9, lon: 27.56, ts: Date.now() - 120000 }"); pg.click("#mt-check"); pg.wait_for_timeout(600)
        ok(pg.is_visible("#mt-go") and pg.is_visible(".mt-tag.preview"), "место телефона есть — анкета с живым бейджем-образцом")
        pg.fill("#mt-name", "Миша"); pg.fill("#mt-place", "Филармония"); pg.wait_for_timeout(100)
        ok(pg.inner_text("#mt-pv-p") == "Филармония", "бейдж-образец меняется, пока пишете")
        pg.locator("#cl-meet").screenshot(path="/tmp/claude-0/meet_form.png")
        pg.click("#mt-go"); pg.wait_for_timeout(500)
        ok(pg.is_visible(".mt-here"), "отметился — снова доска")
        pg.close()
    ok(not errs, f"без ошибок на странице: {errs[:2]}")
    b.close()
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
