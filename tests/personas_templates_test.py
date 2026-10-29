"""Персонажи с 0.36: встроенных двое — Лира и Патрик (оба с живым лицом); Агент, Джарвис, Эва и Кодер — готовые шаблоны.
Кто пользовался шаблонным (выбран, учил имя) — тот остаётся своим под тем же id, с живым лицом и ролью из шаблона;
неиспользованные — только в «Готовых»; добавить из «Готовых» — одной кнопкой; неизвестный выбранный — Лира."""
import json, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
d = tempfile.mkdtemp(); os.environ.update(DEWIAR_CONFIG_DIR=d + "/cfg", DEWIAR_STATE_DIR=d + "/st")
sys.path.insert(0, str(ROOT / "core"))
from settings import PERSONAS_DIR, SETTINGS_PATH  # noqa: E402
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


PERSONAS_DIR.mkdir(parents=True)
SETTINGS_PATH.write_text(json.dumps({"persona": "eva"}), encoding="utf-8")
(PERSONAS_DIR / "jarvis.heard.json").write_text(json.dumps({"gigaam": ["джарвиз"]}), encoding="utf-8")
import personas as P  # noqa: E402
ps = P.Personas()
ids = [p["id"] for p in ps.all()]
ok(ids[:2] == ["lira", "patrik"] and set(ids) == {"lira", "patrik", "eva", "jarvis"}, f"Лира и Патрик + те, кем пользовались (Эва выбрана, Джарвис учил имя): {ids}")
ok(ps.get("patrik").get("live") == "patrik" and ps.get("lira").get("live") == "lira", "у Лиры и Патрика — живые лица")
e = ps.get("eva")
ok(e["name"] == "Эва" and e.get("live") == "eva" and e.get("route") and not e.get("builtin"), "Эва осталась своей: имя, живое лицо, роль — из шаблона")
ok("джарвиз" in ps.get("jarvis")["aliases"], "у Джарвиса — выученное произношение")
ok(ps.get("dewiar")["id"] == "lira", "неизвестный (не перенесённый) — Лира")
g = {x["id"]: x for x in ps.gallery()}
ok(g["dewiar"]["template"] and not g["dewiar"]["installed"] and g["eva"]["installed"], "в «Готовых»: Агент — добавить, Эва — уже в команде")
ok(ps.gallery_add("agent") == "agent" and ps.get("agent")["name"] == "Кодер" and ps.get("agent").get("route"), "Кодер добавлен из «Готовых» — с ролью")
pid = ps.save(dict(ps.get("eva"), color="#00ff00"))
ok(ps.get(pid).get("live") == "eva" and ps.get(pid)["color"] == "#00ff00", "свой цвет Эве — живое лицо не потерялось")
ps.delete("eva")
ok("eva" not in {p["id"] for p in ps.all()}, "своего из шаблона можно убрать насовсем")
P.Personas()                                                     # второй запуск — перенос не повторяется
ok("eva" not in {p["id"] for p in P.Personas().all()}, "перенос — один раз: убранная Эва не возвращается")
js = (ROOT / "ui/assets/entities.js").read_text(encoding="utf-8")
ok("patrik:" in js and all((ROOT / f"ui/assets/live/{f}").exists() for f in
   ["patrik.webp", "patrik-eyes.png", "patrik-smile.webp", "patrik-iris-l.png", "patrik-iris-r.png"] + [f"patrik-mouth-{i}.webp" for i in range(4)]), "слои живого лица Патрика на месте")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
