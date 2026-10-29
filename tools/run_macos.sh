#!/bin/bash
# Локальный прототип: отдельные данные, без аккаунта, синхронизации и доступа к микрофону.
set -eu
DEWIAR_PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export DEWIAR_CONFIG_DIR="$DEWIAR_PROJECT_ROOT/work/mac-preview/config"
export DEWIAR_STATE_DIR="$DEWIAR_PROJECT_ROOT/work/mac-preview/state"
export DEWIAR_WORKSPACE_DIR="$DEWIAR_PROJECT_ROOT/work/mac-preview/workspace"
export DEWIAR_NO_SCOPE=1
mkdir -p "$DEWIAR_CONFIG_DIR"
if [ ! -f "$DEWIAR_CONFIG_DIR/settings.json" ]; then
  "$DEWIAR_PROJECT_ROOT/.venv/bin/python" - <<'PY'
import json, os
from pathlib import Path
Path(os.environ["DEWIAR_CONFIG_DIR"], "settings.json").write_text(json.dumps({
    "sync": {"enabled": False}, "privacy": {"local_only": True, "mic": "button"},
    "obsidian": {"vault": ""}, "ui": {"welcome": "done"}
}), encoding="utf-8")
PY
fi
exec "$DEWIAR_PROJECT_ROOT/.venv/bin/python" "$DEWIAR_PROJECT_ROOT/ui/app.py" --text-only --settings "$@"
