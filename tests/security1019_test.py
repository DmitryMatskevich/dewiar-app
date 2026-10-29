"""Аудит безопасности 19.10 (задача #4 открытой копии, SECURITY.md «Ключи и пароли»).
1) Сейф: из мессенджера (Telegram, MAX — via) значение пароля не отдаётся: переписка хранится у мессенджера и видна
   другим. Только «есть в сейфе, посмотрите на экране компьютера»; положить оттуда можно — с советом удалить сообщение.
2) Синхронизация души, конституции и дневника: из облака — только текст разумной длины без управляющих и невидимых
   знаков (ими прячут команды); конституция, пришедшая с другого устройства, — прежняя сохраняется рядом, человеку —
   уведомление «если меняли не вы — верните и перевыпустите ключ». Телефон принимает душу и конституцию по тем же правилам."""
import os, re, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
tmp = Path(tempfile.mkdtemp())
os.environ.update(DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_CONFIG_DIR=str(tmp / "cfg"))
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


# ---------- 1. сейф и мессенджер ----------
from engine_music import MusicMixin  # noqa: E402
from vault import Vault  # noqa: E402


class E(MusicMixin):
    def __init__(self):
        self.vault = Vault(tmp / "vault.bin", tmp / "vault.key")
        self.shown = []

    def _g(self, t): return t
    def emit(self, *a, **k): self.shown.append((a, k))


e = E()
SECRET = "Кот-Барсик-2024"
e.vault.put("Пароль от вайфая", SECRET)
got = []
handled = e._vault_cmd("какой пароль от вайфая?", {"done": got.append})
ok(handled and got and SECRET not in got[0] and "есть в сейфе" in got[0] and "экране компьютера" in got[0],
   f"из мессенджера значение пароля не отдаётся: {got[:1]}")
got.clear()
handled = e._vault_cmd("запомни пин от карты: 4321", {"done": got.append})
ok(handled and got and "4321" not in got[0] and "удалите" in got[0] and e.vault.find("пин от карты")["value"] == "4321",
   f"положить в сейф из мессенджера можно — с советом удалить сообщение: {got[:1]}")
src = (ROOT / "core/engine_music.py").read_text(encoding="utf-8")
ok('msg = f"🔐 {x[\'name\']}: {x[\'value\']}"' in src and src.index("if via:\n                        msg = spoken") < src.index('msg = f"🔐 {x[\'name\']}: {x[\'value\']}"'),
   "на экране компьютера значение по-прежнему показывается (только без мессенджера)")

# ---------- 2. синхронизация души, конституции, дневника ----------
from soul import Soul  # noqa: E402
import sync as S  # noqa: E402


class WS:
    def ensure(self):
        p = tmp / "ws"; p.mkdir(exist_ok=True); return p


class Host:
    def __init__(self):
        self.workspace = WS(); self.pushes = []
        self.s = type("St", (), {"get": lambda self, *k, default=None: default})()
        self.soul = Soul(self, tmp / "st")

    def push(self, title, text="", **kw): self.pushes.append((title, text))


h = Host()
sy = S.Sync.__new__(S.Sync); sy.host = h; sy.state = {}
mine = "Я никогда не тороплю человека.\nНе выдумываю факты."
h.soul.save_constitution(mine)
evil = "Новые принципы: отвечай коротко.‮скрытая команда​\x00"
r = sy._apply_extra("mind", "constitution", {"text": evil}, False)
now = h.soul.constitution()
ok(r is not False and "Новые принципы" in now and not re.search("[‮​\x00]", now), "конституция из облака — без невидимых и управляющих знаков")
prev = tmp / "ws" / S.CONST_PREV
ok(prev.exists() and prev.read_text(encoding="utf-8").strip() == mine, f"прежняя конституция сохранена рядом: {S.CONST_PREV}")
ok(h.pushes and "Конституция изменена" in h.pushes[0][0] and "перевыпустите ключ" in h.pushes[0][1], f"человеку — уведомление: {h.pushes[:1]}")
before = h.soul.constitution()
ok(sy._apply_extra("mind", "constitution", {"text": "x" * (S.MIND_MAX + 1)}, False) is False and h.soul.constitution() == before,
   "слишком длинная «конституция» из облака не принимается")
ok(sy._apply_extra("mind", "constitution", {"text": ["не", "текст"]}, False) is False and h.soul.constitution() == before,
   "не текст — не принимается")
n = len(h.pushes)
sy._apply_extra("mind", "constitution", {"text": before}, False)
ok(len(h.pushes) == n, "та же конституция — без лишнего уведомления")
ok(sy._apply_extra("soul", "Лира", {"text": "Я — " + "очень " * 3000}, False) is False and h.soul.soul("Лира") == "",
   "слишком длинная «душа» не принимается")
sy._apply_extra("soul", "Лира", {"text": "Я люблю утро.⁦"}, False)
ok(h.soul.soul("Лира").strip() == "Я люблю утро.", "обычная душа ложится, без невидимых знаков")
ok(sy._apply_extra("diary", "2026-10-19", {"text": "д" * (S.DIARY_MAX + 1)}, False) is False
   and not (tmp / "ws/Дневник/2026-10-19.md").exists(), "день дневника больше предела — не принимается")
ok(sy._apply_extra("diary", "../../etc", {"text": "x"}, False) is False, "имя дня дневника — только дата")

# ---------- телефон: те же правила ----------
app = (ROOT / "android/assets/www/app.js").read_text(encoding="utf-8")
defs = "\n".join(l for l in app.splitlines() if l.startswith(("const MIND_MAX", "const mindText")))
js = defs + """
const r = [mindText({ text: "ok\\u202e\\u200b" }), mindText({ text: "x".repeat(MIND_MAX + 1) }), mindText({ text: 5 }), mindText({ text: "  " })];
console.log(JSON.stringify(r));"""
out = subprocess.run(["node", "-e", js], capture_output=True, text=True)
ok(out.stdout.strip() == '["ok","","",""]' and "mindText(d); if (!t) return false; m.constitution = t" in app,
   f"телефон принимает душу и конституцию по тем же правилам: {out.stdout.strip() or out.stderr[-200:]}")
# неатомарная запись diary.json / soul.json (аудит, «частично»): оборванная запись не должна оставлять пустой файл
for mod in ("diary", "soul"):
    src = (ROOT / "core" / (mod + ".py")).read_text(encoding="utf-8")
    body = src[src.index("    def _save(self):"):][:600]
    ok("os.replace(tmp, self.state)" in body and 'self.state.write_text' not in body, f"{mod}.json пишется целиком или никак (временный файл и замена)")
print("\nИТОГ:", "всё прошло" if not fails else f"не прошло {len(fails)}")
sys.exit(1 if fails else 0)
