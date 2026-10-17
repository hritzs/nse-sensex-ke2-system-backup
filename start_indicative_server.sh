#!/usr/bin/env bash

set -euo pipefail

ROOT="$HOME/projects/nse-fo-low-latency-decoder"
SERVER_DIR="$ROOT/indicative_close_server"
LOCAL_BHAV_DIR="$ROOT/shared/cm_bhavcopy"
LOCAL_BHAV="$LOCAL_BHAV_DIR/current.csv"

mkdir -p "$LOCAL_BHAV_DIR"

# ============================================================
# NSE CM BHAVCOPY LOCATION
# ============================================================
WINDOWS_DIR='\\172.16.1.85\Shared\Hardik\Bhavcopy\NSEcmbhav'

echo "============================================================"
echo " INDICATIVE CLOSE SERVER"
echo "============================================================"
echo "Current date : $(date '+%d-%b-%Y')"
echo "Bhavcopy dir : ${WINDOWS_DIR}"
echo "Local CM file: ${LOCAL_BHAV}"
echo

# ============================================================
# FIND LATEST AVAILABLE BHAVCOPY
#
# Filename format:
# NSEcm09SEP2026bhav.csv
#
# PowerShell parses the date from the filename and returns
# the newest trading-day file available.
# ============================================================
echo "[1/4] Finding latest available CM bhavcopy..."

LATEST_BHAV="$(
powershell.exe -NoProfile -Command '
$dir = "\\172.16.1.85\Shared\Hardik\Bhavcopy\NSEcmbhav"

$files = Get-ChildItem -LiteralPath $dir -File |
    Where-Object {
        $_.Name -match "^NSEcm\d{2}[A-Za-z]{3}\d{4}bhav\.csv$"
    } |
    ForEach-Object {
        if ($_.Name -match "^NSEcm(\d{2})([A-Za-z]{3})(\d{4})bhav\.csv$") {
            $dateText = "$($Matches[1])$($Matches[2])$($Matches[3])"

            try {
                [PSCustomObject]@{
                    Name = $_.Name
                    Date = [datetime]::ParseExact(
                        $dateText,
                        "ddMMMyyyy",
                        [Globalization.CultureInfo]::InvariantCulture
                    )
                }
            }
            catch {
            }
        }
    } |
    Sort-Object Date -Descending

if ($files.Count -gt 0) {
    $files[0].Name
}
'
)"

LATEST_BHAV="$(printf '%s' "$LATEST_BHAV" | tr -d '\r\n')"

if [[ -z "$LATEST_BHAV" ]]; then
    echo
    echo "ERROR: No NSE CM bhavcopy CSV found."
    echo "Directory:"
    echo "  ${WINDOWS_DIR}"
    exit 1
fi

WINDOWS_BHAV="${WINDOWS_DIR}\\${LATEST_BHAV}"

echo "[OK] Latest available bhavcopy:"
echo "     ${LATEST_BHAV}"
echo "     ${WINDOWS_BHAV}"
echo

# ============================================================
# COPY BHAVCOPY INTO WSL
# ============================================================
echo "[1/4] Copying bhavcopy into local CM data..."

TMP_BHAV="$(mktemp)"

powershell.exe -NoProfile -Command "
\$src = '${WINDOWS_BHAV}'

if (!(Test-Path -LiteralPath \$src)) {
    Write-Error ('Bhavcopy not found: ' + \$src)
    exit 1
}

Get-Content -LiteralPath \$src -Raw
" > "$TMP_BHAV"

if [[ ! -s "$TMP_BHAV" ]]; then
    rm -f "$TMP_BHAV"
    echo "ERROR: Bhavcopy was copied as an empty file."
    exit 1
fi

mv "$TMP_BHAV" "$LOCAL_BHAV"

echo "[OK] Local bhavcopy ready."
echo

# ============================================================
# COMPILE ONLY INDICATIVE CLOSE SERVER
# ============================================================
echo "[2/4] Compiling standalone server..."

cd "$ROOT"

g++ -std=c++17 -O2 -pthread \
    indicative_close_server/server.cpp \
    -o indicative_close_server/indicative_close_server \
    -llzo2

echo "[OK] Build successful."
echo

# ============================================================
# STOP ONLY INDICATIVE SERVER USING PORT 8100
# NEVER use pkill -f
# ============================================================
echo "[3/4] Checking port 8100..."

OLD_PID="$(
    ss -ltnp 2>/dev/null |
    awk '
        /:8100 / {
            if (match($0, /pid=[0-9]+/)) {
                print substr($0, RSTART + 4, RLENGTH - 4)
                exit
            }
        }
    '
)"

if [[ -n "${OLD_PID}" ]]; then

    OLD_COMM="$(ps -p "${OLD_PID}" -o comm= 2>/dev/null || true)"

    echo "Port 8100 PID : ${OLD_PID}"
    echo "Process       : ${OLD_COMM}"

    if [[ "${OLD_COMM}" == "indicative_clos" ||
          "${OLD_COMM}" == "indicative_close_server" ]]; then

        echo "Stopping existing indicative server..."

        kill "${OLD_PID}" 2>/dev/null || true
        sleep 1

        if kill -0 "${OLD_PID}" 2>/dev/null; then
            echo "Forcing termination..."
            kill -9 "${OLD_PID}" 2>/dev/null || true
            sleep 1
        fi

    else

        echo "ERROR: Port 8100 belongs to an unexpected process."
        echo "Refusing to terminate PID ${OLD_PID}."
        exit 1

    fi

else

    echo "No existing server on port 8100."

fi

echo

# ============================================================
# START SERVER
# ============================================================
echo "[4/4] Starting standalone server..."

cd "$ROOT"

nohup "$SERVER_DIR/indicative_close_server" \
    > /tmp/indicative_close_server.log 2>&1 &

PID=$!

sleep 3

echo
echo "PID=$PID"
echo

echo "===== PORT ====="
ss -ltnp | grep ':8100' || true

echo
echo "===== SERVER LOG ====="
sed -n '1,40p' /tmp/indicative_close_server.log

echo
echo "============================================================"
echo " SERVER STARTED"
echo "============================================================"
echo "Using bhavcopy : ${LATEST_BHAV}"
echo "HTTP            : http://localhost:8100"
echo "Log             : /tmp/indicative_close_server.log"
echo "============================================================"
