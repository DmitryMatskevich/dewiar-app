"""Живые персонажи (отзыв 13.10: «на qwen персонажи отвечают примитивно, банально — даже не верится»): у каждого — характер
с мнениями, вкусом и манерой речи, плюс общее правило TALK_RULES (своё мнение, конкретика вместо общих слов, без
«Отличный вопрос» и «обращайся», спорить, когда не согласен). Сохранённая копия с прежним заводским характером (ставили
фото) получает новый, а свой текст пользователя не трогается; телефон получает то же правило (personas.js → systemPrompt)."""
import json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
d = tempfile.mkdtemp(); os.environ.update(DEWIAR_CONFIG_DIR=d + "/cfg", DEWIAR_STATE_DIR=d + "/st")
sys.path.insert(0, str(ROOT / "core"))
from settings import PERSONAS_DIR  # noqa: E402
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


PERSONAS_DIR.mkdir(parents=True)
OLD_LIRA = ("Тебя зовут Лира. Ты творческая и мягкая собеседница: говоришь образно и красиво, любишь музыку, книги и идеи, "
            "умеешь поддержать. Общаешься на «ты».")
(PERSONAS_DIR / "lira.json").write_text(json.dumps({"character": OLD_LIRA, "color": "#123456"}, ensure_ascii=False), encoding="utf-8")
(PERSONAS_DIR / "patrik.json").write_text(json.dumps({"character": "Тебя зовут Патрик. Ты мой личный Грокс: язвительный и честный."},
                                                     ensure_ascii=False), encoding="utf-8")
import personas as P  # noqa: E402
ps = P.Personas()
lira, patrik = ps.get("lira"), ps.get("patrik")
ok(lira["character"] == P.LIRA_CHAR and lira["color"] == "#123456", "сохранённая Лира с прежним заводским характером — получила новый, свой цвет остался")
ok("Грокс" in patrik["character"], "свой характер пользователя не тронут")
sp = ps.system_prompt("lira", "Миша", "вторник")
ok(sp.startswith(P.LIRA_CHAR) and sp.index(P.TALK_RULES) < sp.index(P.VOICE_RULES), "в подсказке: характер, сразу за ним — как говорить интересно, потом правила голоса")
for k in ("своим мнением", "Не начинай с «Отличный вопрос»", "Вместо общих слов — конкретика", "Не соглашайся со всем подряд"):
    ok(k in P.TALK_RULES or k in sp, f"правило: {k}")
chars = {k: getattr(P, k) for k in ("LIRA_CHAR", "PATRIK_CHAR", "AGENT_CHAR", "CODER_CHAR", "JARVIS_CHAR", "EVA_CHAR")}
ok(all(250 <= len(v) <= 1200 for v in chars.values()), f"характеры — развёрнутые, но в пределах (Telegram-бот берёт до 1200 знаков): { {k: len(v) for k, v in chars.items()} }")
ok("иронией" in P.PATRIK_CHAR and "скепсис" in P.PATRIK_CHAR and "мнения" in P.LIRA_CHAR, "Патрик — ирония и скепсис, у Лиры — свой вкус и мнения")
ok(all(P.TEMPLATES[t]["character"] == chars[c] for t, c in (("dewiar", "AGENT_CHAR"), ("agent", "CODER_CHAR"), ("jarvis", "JARVIS_CHAR"), ("eva", "EVA_CHAR"))),
   "готовые шаблоны — с новыми характерами")
ok(all(any(od.get("character") for od in (v if isinstance(v, list) else [v])) for k, v in P.OLD_DEFAULTS.items() if k in ("lira", "patrik", "dewiar", "agent", "jarvis", "eva")),
   "прежние тексты всех шестерых — в OLD_DEFAULTS (сохранённые копии обновятся)")
js = subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)], capture_output=True, text=True, check=True).stdout
ok("window.DEWIAR_TALK_RULES" in js and json.dumps(P.TALK_RULES, ensure_ascii=False) in js, "телефон: то же правило в personas.js")
ok("window.DEWIAR_TALK_RULES" in (ROOT / "android/assets/www/app.js").read_text(encoding="utf-8"), "телефон: правило — в systemPrompt")
ex = json.loads((ROOT / "data/exam/scenarios.json").read_text(encoding="utf-8"))["scenarios"]
ok({"talk_lively", "talk_critic"} <= {s["id"] for s in ex} and "talk_lively" in (ROOT / "android/assets/www/exam.js").read_text(encoding="utf-8"),
   "экзамен: «живая собеседница» и «честный разбор идеи» — на компьютере и телефоне")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
