"""Редактор раздела «Папка»: форматы, безопасная запись и UX-кнопки."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))

from workspace import Workspace, WorkspaceError  # noqa: E402


def ok(condition, message):
    print(("✅ " if condition else "❌ ") + message)
    if not condition:
        raise SystemExit(1)


with tempfile.TemporaryDirectory(prefix="dewiar-folder-editor-") as tmp:
    os.environ["DEWIAR_WORKSPACE"] = str(Path(tmp) / "workspace")
    ws = Workspace(None)
    root = ws.ensure()
    files = [
        ("Заметки/черновик.md", "# Заголовок\nТекст"),
        ("Знания/страница.html", "<h1>Заголовок</h1>"),
        ("Заметки/обычный.txt", "Простой текст"),
        ("Заметки/старый.markdown", "*markdown*"),
    ]
    for rel, text in files:
        ws.write(rel, text)
        ok((root / rel).read_text(encoding="utf-8") == text, f"запись разрешена: {rel}")
    for rel in ("Лаборатория/скрипт.py", "Знания/data.json"):
        try:
            ws.write(rel, "опасная правка")
        except WorkspaceError:
            pass
        else:
            raise SystemExit(f"❌ запрещённый формат принят: {rel}")
    try:
        ws.write("../вне.md", "выход")
    except WorkspaceError:
        pass
    else:
        raise SystemExit("❌ путь за пределами рабочей папки принят")

settings = (ROOT / "ui/settings.html").read_text(encoding="utf-8")
folder = (ROOT / "ui/studio/folder.js").read_text(encoding="utf-8")
help_text = (ROOT / "ui/assets/help.js").read_text(encoding="utf-8")
ok('id="f-move"' not in settings, "кнопка «Другая папка» убрана из раздела")
ok('id="f-save"' in folder and 'id="f-save-as"' in folder, "кнопки «Сохранить» и «Сохранить как» создаются редактором")
ok("EDITABLE_FILES" in folder and "fMountEditor" in folder, "добавлены режимы «Вид» и «Код»")
ok("Смена рабочей папки не удаляет прежнюю" in help_text, "справка объясняет смену папки и индексирование")
print("✅ редактор «Папка» проверен")
