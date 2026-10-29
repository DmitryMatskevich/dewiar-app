#!/usr/bin/env python3
"""«Что нового» для кабинета: dist/dewiar-notes-X.Y.Z.txt — пункты RELEASE_NOTES.md этого выпуска (компьютер X.Y.Z и
телефон из BuildInfo.java) простым текстом. Файл кладётся в папку обновлений вместе со сборками — кабинет («Обновления»)
сам подставляет его в поле «Что нового», когда выбрана эта версия (cloud.php: upd_notes).
    python3 tools/release_notes.py [папка]   -> путь к файлу"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def versions():
    pc = re.search(r'^VERSION = "([^"]+)"', (ROOT / "core/version.py").read_text(), re.M).group(1)
    ph = re.search(r'VERSION = "([^"]+)"', (ROOT / "android/src/pro/dewiar/app/BuildInfo.java").read_text()).group(1)
    return pc, ph


def notes(pc, ph):
    """Пункты «- **Заголовок** (версии): текст», где среди версий есть pc или ph -> «- Заголовок: текст»."""
    out = []
    for ln in (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^- \*\*(.+?)\*\*\s*\(([^)]*)\)\s*:?\s*(.*)$", ln)
        if not m:
            continue
        tags = [t.strip() for t in re.split(r"[/,]", m.group(2))]
        if pc in tags or ph in tags:
            text = re.sub(r"`([^`]*)`", r"\1", m.group(3)).replace("**", "").strip()
            out.append(f"- {m.group(1)}: {text}" if text else f"- {m.group(1)}")
    return out


if __name__ == "__main__":
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "dist")
    out_dir.mkdir(parents=True, exist_ok=True)
    pc, ph = versions()
    items = notes(pc, ph)
    f = out_dir / f"dewiar-notes-{pc}.txt"
    f.write_text(f"Dewiar {pc} · телефон {ph}\n" + "\n".join(items) + "\n", encoding="utf-8")
    print(f)
