#!/usr/bin/env bash
# Start the feed stack from one WSL terminal. Never replaces existing services.
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${KE2_PYTHON:-$PROJECT_ROOT/venv/bin/python}"
SIM_PORT="${MINUTE_HTTP_PORT:-8000}"
WITH_SIMULATOR=1
WITH_INDICATIVE=1
CHECK_ONLY=0
REUSE_EXISTING=0
declare -A EXISTING_PORTS=()
PIDS=()
NAMES=()
LOG_FILES=()

usage() {
    cat <<'HELP'
Usage: bash start_system.sh [--check] [--reuse-existing] [--no-simulator] [--no-indicative]

Default: start decoder :9001, KE2 adapter :9002, NIFTY simulator :8000,
and indicative-close :8100 if that independent service is not already running.
Separate logs are printed on startup. Stay in this terminal; Ctrl+C stops only
processes launched here. Existing services on 9001/9002/simulator port cause
startup to stop, with no replacement or automatic restart.

--check           Validate files, dependencies and ports; start nothing.
--reuse-existing  Keep existing listeners and start only missing services.
                  Existing listeners must already be the intended services.
--no-simulator    Run the feed without starting the NIFTY simulator.
--no-indicative   Do not start the independent indicative-close service.
Environment: KE2_PYTHON, BSE_LOCAL_IP, MINUTE_SIM_DB, MINUTE_HTTP_HOST,
MINUTE_HTTP_PORT. Existing simulator database configuration is preserved.
HELP
}

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
port_open() { ss -H -ltn "sport = :$1" | grep -q .; }

for arg in "$@"; do
    case "$arg" in
        --check) CHECK_ONLY=1 ;;
        --reuse-existing) REUSE_EXISTING=1 ;;
        --no-simulator) WITH_SIMULATOR=0 ;;
        --no-indicative) WITH_INDICATIVE=0 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "Unknown option: $arg" ;;
    esac
done

cd -- "$PROJECT_ROOT"
for tool in ss grep flock; do
    command -v "$tool" >/dev/null || fail "Required command missing: $tool"
done
[[ -x "$PYTHON_BIN" ]] || fail "Python not executable: $PYTHON_BIN"
[[ -x build-ke2/nse_decoder ]] || fail "Build first: cmake --build build-ke2 --target nse_decoder -j2"
[[ -r ke2_feed_server.py ]] || fail "Missing ke2_feed_server.py"
[[ -r shared/GreekTokens.csv && -r shared/BSEIndexTokens.csv ]] ||
    fail "Both local instrument masters are required."
"$PYTHON_BIN" -c 'import websockets; from websockets.asyncio.server import serve' ||
    fail "Install requirements-ke2.txt in the selected Python environment."
[[ "$SIM_PORT" =~ ^[0-9]+$ ]] && (( 10#$SIM_PORT >= 1 && 10#$SIM_PORT <= 65535 )) ||
    fail "MINUTE_HTTP_PORT must be an integer in 1..65535"
SIM_PORT=$((10#$SIM_PORT))
if (( WITH_SIMULATOR )); then
    [[ -r simulation_server_2.py ]] || fail "Missing simulation_server_2.py"
    [[ "$SIM_PORT" != 9001 && "$SIM_PORT" != 9002 && "$SIM_PORT" != 8100 ]] ||
        fail "Simulator port conflicts with another stack service."
fi

# Preflight every required port BEFORE starting any child.
REQUIRED_PORTS=(9001 9002)
if (( WITH_SIMULATOR )); then REQUIRED_PORTS+=("$SIM_PORT"); fi
for port in "${REQUIRED_PORTS[@]}"; do
    if port_open "$port"; then
        if (( REUSE_EXISTING )); then
            EXISTING_PORTS[$port]=1
            printf 'Reusing existing listener on port %s; verify it is the intended service.\n' "$port"
        else
            ss -ltnp "sport = :$port" >&2
            fail "Port $port is occupied. Use --reuse-existing to keep it, or stop it separately only if authorized."
        fi
    fi
done

START_INDICATIVE=$WITH_INDICATIVE
if (( WITH_INDICATIVE )); then
    if port_open 8100; then
        START_INDICATIVE=0
        printf 'Port 8100 already occupied; leaving that service untouched.\n'
    else
        [[ -x indicative_close_server/indicative_close_server ]] ||
            fail "Missing indicative-close binary; build it or use --no-indicative."
        [[ -s shared/cm_bhavcopy/current.csv ]] ||
            fail "Missing local CM data; supply it or use --no-indicative."
    fi
fi

if (( CHECK_ONLY )); then
    printf 'Preflight passed. No services started.\n'
    printf 'Instrument freshness and live multicast delivery still require verification.\n'
    exit 0
fi

mkdir -p logs
exec 9>logs/system-launcher.lock
flock -n 9 || fail "Another start_system.sh instance is active."
RUN_LOG_DIR="$PROJECT_ROOT/logs/system-$(TZ=Asia/Kolkata date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$RUN_LOG_DIR"

cleanup() {
    local result=$?
    trap - EXIT INT TERM
    if (( ${#PIDS[@]} )); then
        printf '\nStopping only this launcher\047s child processes...\n'
        # Reverse order: optional dashboard, simulator, adapter, then decoder.
        for ((i=${#PIDS[@]}-1; i>=0; i--)); do
            kill -INT "${PIDS[i]}" 2>/dev/null || true
        done
        for ((attempt=0; attempt<50; attempt++)); do
            local alive=0
            for pid in "${PIDS[@]}"; do
                if kill -0 "$pid" 2>/dev/null; then alive=1; fi
            done
            (( alive )) || break
            sleep 0.1
        done
        for pid in "${PIDS[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then kill -TERM "$pid" 2>/dev/null || true; fi
        done
        sleep 0.2
        for pid in "${PIDS[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then kill -KILL "$pid" 2>/dev/null || true; fi
            wait "$pid" 2>/dev/null || true
        done
    fi
    printf 'Logs retained: %s\n' "$RUN_LOG_DIR"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

start_service() {
    local name="$1" port="$2"
    shift 2
    if [[ -n "${EXISTING_PORTS[$port]:-}" ]]; then
        printf '%-18s port=%-5s existing process left running (not managed here).\n' "$name" "$port"
        return 0
    fi
    local logfile="$RUN_LOG_DIR/$name.log"
    # A separate session prevents terminal Ctrl+C plus cleanup delivering duplicate
    # signals. Reset inherited background SIGINT so Python also shuts down cleanly.
    "$PYTHON_BIN" -c 'import os,signal,sys; os.setsid(); signal.signal(signal.SIGINT,signal.SIG_DFL); os.execv(sys.argv[1],sys.argv[1:])' "$@" \
        9>&- >"$logfile" 2>&1 &
    local pid=$!
    PIDS+=("$pid"); NAMES+=("$name"); LOG_FILES+=("$logfile")
    for ((attempt=0; attempt<300; attempt++)); do
        if ! kill -0 "$pid" 2>/dev/null; then
            tail -n 30 "$logfile" >&2 || true
            fail "$name exited during startup; see $logfile"
        fi
        if port_open "$port"; then
            printf '%-18s PID=%-8s port=%-5s log=%s\n' "$name" "$pid" "$port" "$logfile"
            return 0
        fi
        sleep 0.1
    done
    tail -n 30 "$logfile" >&2 || true
    fail "$name did not listen on port $port within 30 seconds."
}

start_service decoder 9001 "$PROJECT_ROOT/build-ke2/nse_decoder"
start_service ke2-adapter 9002 "$PYTHON_BIN" -u "$PROJECT_ROOT/ke2_feed_server.py" --host 127.0.0.1 --port 9002
if (( WITH_SIMULATOR )); then
    export NIFTY_WS_URI=ws://127.0.0.1:9001
    export MINUTE_HTTP_HOST="${MINUTE_HTTP_HOST:-127.0.0.1}"
    export MINUTE_HTTP_PORT="$SIM_PORT"
    start_service simulator "$SIM_PORT" "$PYTHON_BIN" -u "$PROJECT_ROOT/simulation_server_2.py"
fi
if (( START_INDICATIVE )); then
    start_service indicative-close 8100 "$PROJECT_ROOT/indicative_close_server/indicative_close_server"
fi

printf '\nKE2 API: ws://127.0.0.1:9002/\n'
if (( WITH_SIMULATOR )); then printf 'Simulator: http://127.0.0.1:%s/\n' "$SIM_PORT"; fi
printf 'TCP readiness confirmed; verify actual prices with scripts/ke2_probe.py.\n'
printf 'Logs: %s\nCtrl+C stops only services started here.\n' "$RUN_LOG_DIR"

if (( ${#PIDS[@]} == 0 )); then
    printf 'All requested services were already listening; nothing started.\n'
    exit 0
fi

while true; do
    for ((i=0; i<${#PIDS[@]}; i++)); do
        if ! kill -0 "${PIDS[i]}" 2>/dev/null; then
            tail -n 30 "${LOG_FILES[i]}" >&2 || true
            fail "${NAMES[i]} exited; shutting down the remaining child services."
        fi
    done
    sleep 1
done
