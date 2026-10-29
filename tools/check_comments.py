#!/usr/bin/env python3
"""Проверка «поменялись только комментарии»: сравнивает файлы с последней записью git (HEAD) без комментариев.

Нужна, когда переписываются комментарии и описания: код при этом не должен измениться ни на символ.
    python3 tools/check_comments.py              # все изменённые файлы
    python3 tools/check_comments.py core/llm.py  # только эти

Как сравнивает:
  .py   — дерево разбора (ast) без строк-описаний (docstring): код, имена, строки в коде — те же;
  .php  — `php -w` (PHP сам убирает комментарии и пробелы);
  .js   — поток лексем acorn без комментариев (нужен node и пакет acorn: `npm i -g acorn` или NODE_PATH);
  .html — код в <script> — как .js, <style> — как .css, остальная разметка — без <!-- --> и лишних пробелов;
  .css  — без /* */ и лишних пробелов;  .java — без // и /* */ вне строк и лишних пробелов.
Другие файлы (sh, nsi, md…) не проверяет — о них скажет «не проверяется».
Выход: 0 — код тот же во всех файлах, 1 — где-то изменился код (файл и первое расхождение — в выводе)."""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

JS_TOKENS = r"""
const acorn = require("acorn");
const src = require("fs").readFileSync(0, "utf8");
const out = [];
for (const t of acorn.tokenizer(src, { ecmaVersion: "latest", sourceType: "script", allowHashBang: true, allowReturnOutsideFunction: true })) {
  out.push(t.type.label + "\u0001" + (t.value === undefined ? "" : (typeof t.value === "object" ? String(t.value.pattern || t.value) : String(t.value))));
}
process.stdout.write(JSON.stringify(out));
"""


def head(path):
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{path}"], capture_output=True)
    return r.stdout.decode("utf-8") if r.returncode == 0 else None


def py_norm(src):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            b = node.body[0]
            if isinstance(b, ast.Expr) and isinstance(getattr(b, "value", None), ast.Constant) and isinstance(b.value.value, str):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.dump(tree, include_attributes=False)


def php_norm(src):
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as f:
        f.write(src)
    try:
        r = subprocess.run(["php", "-w", f.name], capture_output=True, text=True)
        if r.returncode:
            raise ValueError("PHP не разобрал файл: " + (r.stderr or r.stdout)[:300])
        return re.sub(r"\s+", " ", r.stdout)
    finally:
        os.unlink(f.name)


def js_norm(src):
    r = subprocess.run(["node", "-e", JS_TOKENS], input=src, capture_output=True, text=True)
    if r.returncode:
        raise ValueError("JS не разобрался: " + r.stderr.strip().splitlines()[-1][:300] if r.stderr.strip() else "node упал")
    return "\n".join(json.loads(r.stdout))


def _strip_c(src, line_comments=True):
    """Убрать /* */ (и // для Java) вне строк '…' и "…"."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1]); i = j + 1
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        elif line_comments and src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        else:
            out.append(c); i += 1
    return re.sub(r"\s+", " ", "".join(out)).strip()


def css_norm(src):
    return _strip_c(src, line_comments=False)


def java_norm(src):
    return _strip_c(src, line_comments=True)


def html_norm(src):
    parts = []

    def script(m):
        attrs, body = m.group(1), m.group(2)
        if "src=" in attrs or not body.strip():
            return f"<script{attrs}></script>"
        parts.append("JS:" + js_norm(body))
        return f"<script{attrs}>§{len(parts)}§</script>"

    def style(m):
        parts.append("CSS:" + css_norm(m.group(2)))
        return f"<style{m.group(1)}>§{len(parts)}§</style>"
    s = re.sub(r"<script([^>]*)>(.*?)</script>", script, src, flags=re.S | re.I)
    s = re.sub(r"<style([^>]*)>(.*?)</style>", style, s, flags=re.S | re.I)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    s = re.sub(r"\s+", " ", s).strip()
    return s + "\n" + "\n".join(parts)


NORM = {".py": py_norm, ".php": php_norm, ".js": js_norm, ".css": css_norm, ".java": java_norm, ".html": html_norm}


def first_diff(a, b):
    i = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return f"…{a[max(0, i - 60):i + 60]!r}\n    стало: …{b[max(0, i - 60):i + 60]!r}"


def main(argv):
    files = argv or subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", "HEAD"], capture_output=True, text=True).stdout.split()
    bad = 0
    for f in files:
        ext = Path(f).suffix.lower()
        if ext not in NORM:
            print(f"–  {f}: не проверяется ({ext or 'без расширения'})")
            continue
        old = head(f)
        p = ROOT / f
        if old is None or not p.exists():
            print(f"–  {f}: нет в HEAD или удалён — сравнивать не с чем")
            continue
        try:
            a, b = NORM[ext](old), NORM[ext](p.read_text(encoding="utf-8"))
        except (SyntaxError, ValueError) as e:
            print(f"❌ {f}: не разбирается — {e}")
            bad += 1
            continue
        if a == b:
            print(f"✅ {f}: код тот же")
        else:
            print(f"❌ {f}: изменился код\n    было:  {first_diff(a, b)}")
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
