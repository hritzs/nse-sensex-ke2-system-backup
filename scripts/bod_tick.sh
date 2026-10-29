#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/logs/bod"
exec "$ROOT/venv/bin/python" -u "$ROOT/scripts/bod_manager.py" --tick >>"$ROOT/logs/bod/$(TZ=Asia/Kolkata date +%Y%m%d).log" 2>&1
