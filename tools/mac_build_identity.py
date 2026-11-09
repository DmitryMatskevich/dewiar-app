"""Identity and freshness checks for the macOS application bundle.

The installer must never silently use an old ``dist/Dewiar.app``.  The
fingerprint is calculated from the source tree at build time and embedded in
the bundle.  Installation compares it with the current tree, so a build made
before the latest source edit is rejected instead of being shown as current.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


MANIFEST_NAME = "mac-build-manifest.json"
_ROOT_DIRS = ("core", "ui", "data", "icons", "tools", "packaging")
_ROOT_FILES = ("pyproject.toml", "requirements.txt", "requirements-content-mcp.txt")
_SKIP_DIRS = {".git", ".venv", "__pycache__", "build", "dist", "work", "node_modules"}


def _source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in _ROOT_FILES:
        path = root / name
        if path.is_file():
            files.append(path)
    for dirname in _ROOT_DIRS:
        base = root / dirname
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or any(part in _SKIP_DIRS for part in path.parts):
                continue
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def source_fingerprint(root: Path) -> str:
    """Return a deterministic digest of files that define the app build."""

    root = Path(root).resolve()
    digest = hashlib.sha256()
    for path in _source_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def git_commit(root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def write_manifest(root: Path, destination: Path, *, build_id: str) -> dict[str, str]:
    """Write the build manifest and return its contents."""

    manifest = {
        "schema": "1",
        "build_id": build_id,
        "source_fingerprint": source_fingerprint(root),
        "git_commit": git_commit(root),
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def validate_manifest(bundle: Path, root: Path, expected_fingerprint: str | None = None) -> dict[str, str]:
    """Validate an embedded manifest against the current source tree."""

    manifest_path = Path(bundle) / "Contents/Resources" / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError(
            "Сборка отклонена: в ней нет манифеста свежести. "
            "Сначала пересоберите проект через tools/rebuild_mac_app.command"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Сборка отклонена: повреждён манифест свежести") from exc
    if manifest.get("schema") != "1" or not manifest.get("build_id"):
        raise ValueError("Сборка отклонена: неизвестная версия манифеста")
    expected = expected_fingerprint or source_fingerprint(Path(root))
    if manifest.get("source_fingerprint") != expected:
        raise ValueError(
            "Сборка отклонена: dist/Dewiar.app устарел относительно исходников. "
            "Сначала выполните tools/rebuild_mac_app.command"
        )
    return manifest
