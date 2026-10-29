"""Граф памяти: [[ссылки]], ссылки на ещё не написанные заметки, #теги, общие имена в разных падежах — одним узлом,
задачи, факты, статистика роста."""
import os, sys, tempfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
from graph import build  # noqa: E402
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
ws = Path(tempfile.mkdtemp())
for d in ("Заметки", "Дневник", "Память", "Задачи/2026-09-28 Отчёт"):
    (ws / d).mkdir(parents=True)
(ws / "Память/Обо мне.md").write_text("# Обо мне\n\nтекст\n\n- Дочь Маша _(22.09.2026)_\n- Любит кофе\n", encoding="utf-8")
(ws / "Заметки/А.md").write_text("# План\n\nВстреча с Сергеем. См. [[Б]] и [[Будущая заметка]]. #работа", encoding="utf-8")
(ws / "Заметки/Б.md").write_text("# Б\n\nНаписать письмо Сергею. #работа #срочно", encoding="utf-8")
(ws / "Дневник/2026-09-30.md").write_text("# Дневник\n\nОбсуждали, что сказал Сергей. [[Отчёт]]", encoding="utf-8")
(ws / "Задачи/2026-09-28 Отчёт/итог.md").write_text("# Итог: Отчёт\n\nГотово.", encoding="utf-8")
g = build(ws, skills=[{"name": "letters", "title": "Письма", "description": "Написать письмо"}], user_name="Алексей",
          knowledge_files=["Регламент.pdf"])
N = {n["id"]: n for n in g["nodes"]}
E = {(e["from"], e["to"], e["kind"]) for e in g["edges"]}
ok(N["me"]["label"] == "Алексей", "в центре — пользователь")
ok(("note:Заметки/А.md", "note:Заметки/Б.md", "wikilink") in E, "[[ссылка]] между заметками")
ghost = [n for n in g["nodes"] if n.get("ghost")]
ok(len(ghost) == 1 and ghost[0]["label"] == "Будущая заметка", "ссылка на ненаписанную заметку — пунктирный узел")
ok("tag:работа" in N and ("note:Заметки/Б.md", "tag:работа", "tag") in E, "#теги — узлы")
ents = [n for n in g["nodes"] if n["kind"] == "entity"]
ok(len([n for n in ents if n["id"].startswith("ent:серг")]) == 1 and any(n["label"] == "Сергей" for n in ents), f"«Сергеем», «Сергею», «Сергей» — один узел «Сергей»: {[n['label'] for n in ents]}")
ok(not any(n["label"].lower().startswith("написать") for n in ents), "глаголы («Написать») не становятся «людьми»")
ok(("diary:Дневник/2026-09-30.md", "task:2026-09-28 Отчёт", "wikilink") in E, "[[ссылка]] на задачу находит задачу")
s = g["stats"]
ok(s["facts"] == 2 and s["notes"] == 2 and s["diary"] == 1 and s["tasks"] == 1 and s["docs"] == 1 and s["skills"] == 1, f"статистика: {s}")
ok(s["week"] >= 4 and s["links"] > 0, "рост за неделю и число связей")
