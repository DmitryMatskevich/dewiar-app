#!/usr/bin/env python3
"""Освежить «Что нового» у недавних выпусков на GitHub по CHANGELOG.md (поправили текст прошлой версии — выпуск
тоже поправится). Таблица «Скачать» над разделом остаётся как есть. Запускает «Метка версии» (.github/workflows/release.yml).

    python3 tools/release_refresh.py [сколько последних версий, по умолчанию 6]
    python3 tools/release_refresh.py --section 0.36.5    — раздел версии для описания выпуска
Нужен gh с правом на выпуски (GH_TOKEN в GitHub Actions)."""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def unwrap(text):
    """Склеить строки, перенесённые для удобства чтения в редакторе: на GitHub каждый перенос — новая строка."""
    out = []
    for ln in text.split("\n"):
        s = ln.strip()
        if out and s and out[-1].strip() and not re.match(r"^(?:[-*+]\s|\d+[.)]\s|#|\||```)", s):
            out[-1] = out[-1].rstrip() + " " + s
        else:
            out.append(ln.rstrip())
    return "\n".join(out)


def section(text, v):
    """Раздел версии v из CHANGELOG.md («## 0.36.5 …» или «### …»), без переносов внутри абзацев. Нет — None."""
    m = re.search(r"^##+ " + re.escape(v) + r"(?:[ ·/][^\n]*)?\n(.*?)(?=^##+ |\Z)", text, re.M | re.S)
    return unwrap(m.group(1).strip()) if m else None


def main():
    ch = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if len(sys.argv) > 2 and sys.argv[1] == "--section":      # раздел одной версии — для release.yml
        print(section(ch, sys.argv[2]) or "")
        return
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    current = re.search(r'VERSION = "([^"]+)"', (ROOT / "core/version.py").read_text()).group(1)
    versions = re.findall(r"^## (\d+\.\d+\.\d+)", ch, re.M)[:n]
    for v in versions:
        if v == current:                       # текущий выпуск пишет само задание files — вместе с таблицей
            continue
        r = subprocess.run(["gh", "release", "view", f"v{v}", "--json", "body", "--jq", ".body"], capture_output=True, text=True)
        if r.returncode:
            print(f"v{v}: выпуска нет")
            continue
        body, notes = r.stdout, section(ch, v)
        i = body.find("## Что нового")
        if not notes or i < 0:
            print(f"v{v}: пропускаю (нет раздела «Что нового»)")
            continue
        new = body[:i] + "## Что нового\n\n" + notes + "\n"
        if new.strip() == body.strip():
            print(f"v{v}: без изменений")
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(new)
        r = subprocess.run(["gh", "release", "edit", f"v{v}", "--notes-file", f.name], capture_output=True, text=True)
        print(f"v{v}: {'текст обновлён' if r.returncode == 0 else 'не обновился: ' + r.stderr.strip()[:200]}")


if __name__ == "__main__":
    main()
