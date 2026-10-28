#!/usr/bin/env bash
# Start the stack; explicit --restart replaces only verified project listeners.
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${KE2_PYTHON:-$PROJECT_ROOT/venv/bin/python}"
SIM_PORT="${MINUTE_HTTP_PORT:-8000}"
WITH_SIMULATOR=1
WITH_INDICATIVE=1
WITH_API=0
API_PYTHON="${KE2_API_PYTHON:-$PROJECT_ROOT/.venv-ke2-api/bin/python}"
CHECK_ONLY=0
REUSE_EXISTING=0
RESTART=0
REFRESH_INPUTS=0
REFRESH_MASTERS=0
KE2_BIND_HOST="${KE2_HOST:-127.0.0.1}"
SIM_BIND_HOST="${MINUTE_HTTP_HOST:-127.0.0.1}"
API_BIND_HOST="${KE2_API_HOST:-127.0.0.1}"
SIMULATOR_SCRIPT="simulation_server_2.py"
declare -A EXISTING_PORTS=()
PIDS=()
NAMES=()
LOG_FILES=()

usage() {
    cat <<'HELP'
Usage: bash start_system.sh [--check] [--reuse-existing] [--no-simulator] [--no-indicative] [--with-api] [--restart] [--gamma-weekly] [--refresh-inputs] [--refresh-masters] [--lan] [--lan-all]

Default: start decoder :9001, KE2 adapter :9002, NIFTY simulator :8000,
and indicative-close :8100 if that independent service is not already running.
Separate logs are printed on startup. Stay in this terminal; Ctrl+C stops only
processes launched here. Existing services on 9001/9002/simulator port cause
startup to stop, with no replacement or automatic restart.

--lan             Bind the KE2 WebSocket feed on all interfaces for LAN clients.
--lan-all         Also bind the simulator and FastAPI tester on all interfaces.
--refresh-masters Verify/copy/install both NSE and BSE masters before startup.
--refresh-inputs  Verify/copy current master, daily inputs, calendar and exact LUTs from the configured Windows share before startup.
--restart         Stop identified project listeners first, then start services.
                  Unknown listeners abort before any signal is sent.
--gamma-weekly    Use new all-expiry GammaShort simulator and its separate DB.
--with-api        Also start the FastAPI tester and Swagger UI on 9003.
--check           Validate files, dependencies and ports; start nothing.
--reuse-existing  Keep existing listeners and start only missing services.
                  Existing listeners must already be the intended services.
--no-simulator    Run the feed without starting the NIFTY simulator.
--no-indicative   Do not start the independent indicative-close service.
Environment: KE2_PYTHON, BSE_LOCAL_IP, MINUTE_SIM_DB, MINUTE_HTTP_HOST,
MINUTE_HTTP_PORT, KE2_HOST, KE2_API_HOST, KE2_PUBLIC_HOST, KE2_API_PYTHON, KE2_TEST_UPSTREAM. Existing simulator database configuration is preserved.
HELP
}

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
port_open() { ss -H -ltn "sport = :$1" | grep -q .; }

for arg in "$@"; do
    case "$arg" in
        --check) CHECK_ONLY=1 ;;
        --restart) RESTART=1 ;;
        --refresh-inputs) REFRESH_INPUTS=1 ;;
        --refresh-masters) REFRESH_MASTERS=1 ;;
        --lan) KE2_BIND_HOST="0.0.0.0" ;;
        --lan-all) KE2_BIND_HOST="0.0.0.0"; SIM_BIND_HOST="0.0.0.0"; API_BIND_HOST="0.0.0.0" ;;
        --gamma-weekly) SIMULATOR_SCRIPT="gamma_weekly_server.py" ;;
        --reuse-existing) REUSE_EXISTING=1 ;;
        --no-simulator) WITH_SIMULATOR=0 ;;
        --no-indicative) WITH_INDICATIVE=0 ;;
        --with-api) WITH_API=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "Unknown option: $arg" ;;
    esac
done

(( RESTART && REUSE_EXISTING )) && fail "Choose --restart or --reuse-existing, not both."

cd -- "$PROJECT_ROOT"
for tool in ss grep flock tee; do
    command -v "$tool" >/dev/null || fail "Required command missing: $tool"
done
[[ -x "$PYTHON_BIN" ]] || fail "Python not executable: $PYTHON_BIN"
[[ -x build-ke2/nse_decoder ]] || fail "Build first: cmake --build build-ke2 --target nse_decoder -j2"
[[ -r ke2_feed_server.py ]] || fail "Missing ke2_feed_server.py"
[[ -r scripts/system_endpoints.py ]] || fail "Missing scripts/system_endpoints.py"
# Serialize the complete refresh/stop/start sequence against other restarts.
if (( RESTART && ! CHECK_ONLY )); then
    mkdir -p logs
    exec 8>logs/system-restart.lock
    flock -n 8 || fail "Another project restart is active."
fi
if (( REFRESH_INPUTS || REFRESH_MASTERS )); then
    (( CHECK_ONLY )) && fail "--check does not refresh files; use run_all.sh --check."
    (( REUSE_EXISTING )) && fail "Master refresh requires a coordinated restart; do not reuse old services."
    command -v powershell.exe >/dev/null || fail "Windows PowerShell required for share refresh."
    REFRESH_SCRIPT=$(wslpath -w "$PROJECT_ROOT/scripts/refresh_market_inputs.ps1")
    REFRESH_ARGS=()
    if (( ! REFRESH_INPUTS )); then REFRESH_ARGS+=(-MastersOnly); fi
    powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$REFRESH_SCRIPT" "${REFRESH_ARGS[@]}" || fail "Input refresh failed; services left running."
    "$PYTHON_BIN" scripts/refresh_master.py --source data/reference_import/GreekTokens.csv --bse-source data/reference_import/BSEIndexTokens.csv || fail "Master validation failed; services left running."
fi
[[ -r shared/GreekTokens.csv && -r shared/BSEIndexTokens.csv ]] ||
    fail "Both local instrument masters are required."
"$PYTHON_BIN" -c 'import websockets; from websockets.asyncio.server import serve' ||
    fail "Install requirements-ke2.txt in the selected Python environment."
[[ "$SIM_PORT" =~ ^[0-9]+$ ]] && (( 10#$SIM_PORT >= 1 && 10#$SIM_PORT <= 65535 )) ||
    fail "MINUTE_HTTP_PORT must be an integer in 1..65535"
SIM_PORT=$((10#$SIM_PORT))
if (( WITH_SIMULATOR )); then
    [[ -r "$SIMULATOR_SCRIPT" ]] || fail "Missing $SIMULATOR_SCRIPT"
    if [[ "$SIMULATOR_SCRIPT" == gamma_weekly_server.py ]]; then
        "$PYTHON_BIN" -c 'import numpy,pandas; from gamma_weekly_engine import load_engine; load_engine()' || fail "Gamma dependencies or backtest source unavailable."
        if (( RESTART )); then
            "$PYTHON_BIN" scripts/check_gamma_inputs.py --strict || fail "Gamma event calendar or eligible contracts unavailable; existing services left running."
        fi
    fi
    [[ "$SIM_PORT" != 9001 && "$SIM_PORT" != 9002 && "$SIM_PORT" != 8100 ]] ||
        fail "Simulator port conflicts with another stack service."
fi

if (( WITH_API )); then
    [[ -x "$API_PYTHON" ]] || fail "Create .venv-ke2-api and install requirements-ke2-api.txt first."
    "$API_PYTHON" -c 'import fastapi,uvicorn; import ke2_test_api' ||
        fail "Install requirements-ke2-api.txt in the API Python environment."
    if (( WITH_SIMULATOR )) && [[ "$SIM_PORT" == 9003 ]]; then
        fail "Simulator port conflicts with FastAPI tester on 9003."
    fi
fi

# Preflight every required port BEFORE starting any child.
# Validate required restart artifacts before stopping an existing process.
if (( RESTART && WITH_INDICATIVE )); then
    [[ -x indicative_close_server/indicative_close_server && -s shared/cm_bhavcopy/current.csv ]] || fail "Indicative binary or local CM master missing."
fi
REQUIRED_PORTS=(9001 9002)
if (( WITH_API )); then REQUIRED_PORTS+=(9003); fi
if (( WITH_SIMULATOR )); then REQUIRED_PORTS+=("$SIM_PORT"); fi
if (( RESTART && WITH_INDICATIVE )); then REQUIRED_PORTS+=(8100); fi
if (( RESTART )); then
    RESTART_ARGS=(--root "$PROJECT_ROOT" --ports "${REQUIRED_PORTS[@]}")
    if (( CHECK_ONLY )); then RESTART_ARGS+=(--check); fi
    "$PYTHON_BIN" scripts/restart_project_services.py "${RESTART_ARGS[@]}" || fail "Project restart preflight/stop failed."
fi
for port in "${REQUIRED_PORTS[@]}"; do
    if port_open "$port"; then
        if (( RESTART && CHECK_ONLY )); then
            continue
        elif (( REUSE_EXISTING )); then
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
        if (( RESTART && CHECK_ONLY )); then
            printf 'Indicative listener on 8100 is included in the planned verified restart.\n'
        else
            printf 'Port 8100 already occupied; leaving that service untouched.\n'
        fi
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
if (( RESTART )); then
    flock -w 12 9 || fail "Previous project launcher did not release its lock."
else
    flock -n 9 || fail "Another start_system.sh instance is active."
fi
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
        8>&- 9>&- >"$logfile" 2>&1 &
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
start_service ke2-adapter 9002 "$PYTHON_BIN" -u "$PROJECT_ROOT/ke2_feed_server.py" --host "$KE2_BIND_HOST" --port 9002
if (( WITH_SIMULATOR )); then
    export NIFTY_WS_URI=ws://127.0.0.1:9001
    export MINUTE_HTTP_HOST="$SIM_BIND_HOST"
    export MINUTE_HTTP_PORT="$SIM_PORT"
    start_service simulator "$SIM_PORT" "$PYTHON_BIN" -u "$PROJECT_ROOT/$SIMULATOR_SCRIPT"
fi
if (( START_INDICATIVE )); then
    start_service indicative-close 8100 "$PROJECT_ROOT/indicative_close_server/indicative_close_server"
fi

if (( WITH_API )); then
    start_service api-tester 9003 "$API_PYTHON" -m uvicorn ke2_test_api:app --host "$API_BIND_HOST" --port 9003 --workers 1
fi

ENDPOINT_ARGS=(--sim-port "$SIM_PORT" --ke2-host "$KE2_BIND_HOST" --sim-host "$SIM_BIND_HOST" --api-host "$API_BIND_HOST" --simulator "$SIMULATOR_SCRIPT")
if (( ! WITH_SIMULATOR )); then ENDPOINT_ARGS+=(--no-simulator); fi
if (( ! WITH_INDICATIVE )); then ENDPOINT_ARGS+=(--no-indicative); fi
if (( ! WITH_API )); then ENDPOINT_ARGS+=(--no-api); fi
"$PYTHON_BIN" scripts/system_endpoints.py "${ENDPOINT_ARGS[@]}" | tee "$RUN_LOG_DIR/endpoints.txt"
# Restart serialization covers stop/start only. Keeping fd 8 locked throughout
# the monitor loop prevents a future authorized launcher from replacing us.
if (( RESTART )); then
    flock -u 8
    exec 8>&-
fi
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
