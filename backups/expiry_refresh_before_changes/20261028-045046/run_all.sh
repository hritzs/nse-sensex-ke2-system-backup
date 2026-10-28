#!/usr/bin/env bash
# Complete stack: verified restart, Gamma simulator, indicative, API and LAN.
set -Eeuo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    printf 'Usage: bash run_all.sh [--check] [--refresh-inputs]
'
    printf 'Starts decoder, KE2, Gamma simulator, indicative and FastAPI with LAN access.
'
    printf 'Replaces only verified project services; Ctrl+C stops services started here.
'
    exit 0
fi
exec bash "$PROJECT_ROOT/start_system.sh" --restart --gamma-weekly --with-api --lan-all "$@"
