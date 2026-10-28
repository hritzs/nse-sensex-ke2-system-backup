#!/usr/bin/env bash
# Complete stack: verified restart, Gamma simulator, indicative, API and LAN.
set -Eeuo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    printf 'Usage: bash run_all.sh [--check] [--refresh-inputs] [--no-refresh-masters]
'
    printf 'Starts decoder, KE2, Gamma simulator, indicative and FastAPI with LAN access.
'
    printf 'Replaces only verified project services; Ctrl+C stops services started here.
'
    printf 'Current NSE and BSE token masters are refreshed from the share by default.\n'
    printf '--check is read-only; --no-refresh-masters explicitly uses local copies.\n'
    exit 0
fi
REFRESH_ARGS=(--refresh-masters)
FORWARDED_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --check|--refresh-inputs) REFRESH_ARGS=(); FORWARDED_ARGS+=("$arg") ;;
        --no-refresh-masters) REFRESH_ARGS=() ;;
        *) FORWARDED_ARGS+=("$arg") ;;
    esac
done
exec bash "$PROJECT_ROOT/start_system.sh" --restart --gamma-weekly --with-api --lan-all "${REFRESH_ARGS[@]}" "${FORWARDED_ARGS[@]}"
