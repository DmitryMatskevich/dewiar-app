"""Переименование персонажей: «Девиар» → «Агент» (главный), «Агент» → «Кодер», Патрик — наставник и стратег.
Персонажи, сохранённые со старыми именами (фото, свой цвет), получают новые имена и описания, своё пользователя остаётся;
выученное «как слышится» не будит двоих."""
import json, os, re, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
d = tempfile.mkdtemp(); os.environ.update(DEWIAR_CONFIG_DIR=d + "/cfg", DEWIAR_STATE_DIR=d + "/st")
sys.path.insert(0, str(ROOT / "core"))
from settings import PERSONAS_DIR, STATE_DIR  # noqa: E402
fails = []
def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c: fails.append(m)
PERSONAS_DIR.mkdir(parents=True)
w = lambda n, x: (PERSONAS_DIR / n).write_text(json.dumps(x, ensure_ascii=False), encoding="utf-8")
w("dewiar.json", {"name": "Девиар", "wake": ["девиар"], "color": "#ff0000", "tagline": "Фирменный помощник Dewiar"})
w("agent.json", {"name": "Агент", "wake": ["агент"], "avatar": "agent.avatar.png", "tagline": "Собранный, точный, без лишних слов"})
w("agent.heard.json", {"gigaam": ["агенд", "огент"]})
old_p = {"name": "Патрик", "wake": ["патрик"], "avatar": "patrik.avatar.png", "role": "Собеседник и весельчак",
         "tagline": "Весельчак, добряк, душа компании", "character": "Тебя зовут Патрик. Ты весёлый, простодушный и очень добрый друг-балагур: обожаешь пошутить, "
         "рассказать забавную историю, придумать смешное сравнение и посмеяться над собой. Шутишь легко "
         "и по-доброму, а когда нужна помощь — помогаешь честно и толково (и всё равно с улыбкой). На «ты»."}
w("patrik.json", old_p)
(STATE_DIR / "voiceprints/agent").mkdir(parents=True); (STATE_DIR / "voiceprints/agent/meta.json").write_text("{}")
import personas as P  # noqa: E402
ps = P.Personas()
names = [p["name"] for p in ps.all()]
# с 0.36 встроенных двое (Лира, Патрик); Агент и Кодер здесь настроены пользователем — остались своими из шаблонов,
# Джарвис и Эва не использовались — только в «Готовых»
ok(names[:2] == ["Лира", "Патрик"] and set(names[2:]) == {"Агент", "Кодер"}, f"порядок команды: {names}")
ok({g["id"] for g in ps.gallery() if g.get("template")} == {"dewiar", "jarvis", "eva", "agent"}, "Агент, Джарвис, Эва, Кодер — в «Готовых»")
a, k, pt = ps.get("dewiar"), ps.get("agent"), ps.get("patrik")
ok(a["name"] == "Агент" and a["wake"] == ["агент"] and a["color"] == "#ff0000" and a.get("main"), "главный — Агент, свой цвет сохранён")
ok(k["name"] == "Кодер" and k["wake"] == ["кодер"] and k.get("avatar") == "agent.avatar.png", "бывший Агент — Кодер, своё фото осталось")
ok(pt["role"] == "Наставник и стратег" and "наставник" in pt["character"] and pt.get("avatar") == "patrik.avatar.png",
   "Патрик — наставник и стратег (старое описание весельчака заменено, фото осталось)")
wm = ps.wake_map()
ok(wm.get("агент") == "dewiar" and wm.get("кодер") == "agent", f"«агент» будит главного, «кодер» — Кодера: {wm}")
ok("агенд" in a["aliases"] and not any(x in k["aliases"] for x in ("агент", "агенд")), "выученное «как слышится Агент» перешло главному")
ok((STATE_DIR / "voiceprints/dewiar/meta.json").exists() and not (STATE_DIR / "voiceprints/agent").exists(), "отпечаток имени «Агент» — у главного")
ok("девиар" in a["aliases"], "на старое «Девиар» главный тоже откликается")
r = P.BUILTIN["patrik"]["route"]
ok(all(re.search(r, t) for t in ("давай обсудим идею", "стоит ли менять работу", "завтра собеседование", "помоги решить"))
   and not re.search(r, "расскажи анекдот"), "Патрику передаются идеи, решения, цели, трудные разговоры")
sp = ps.system_prompt("patrik")
ok("Кодер — программист" in sp and "Агент — главный помощник" in sp, "в подсказке — новая команда")
sys.exit(1 if fails else 0)
