#!/bin/bash

echo ""
echo "============================================================"
echo "  Brite Spark 2026 - Problem 3: No Wrong Door"
echo "============================================================"
echo ""

# Check Python
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo "ERROR: Python is not installed."
    echo ""
    echo "Install it:"
    echo "  macOS:           brew install python3"
    echo "  Ubuntu/Debian:   sudo apt update && sudo apt install python3"
    echo "  Fedora/RHEL:     sudo dnf install python3"
    echo ""
    exit 1
fi

echo "Python found: $($PYTHON --version)"
echo ""

# Verify files
echo "Verifying project files..."
$PYTHON -c "
import os, sys
files = [
    'app/api.py',
    'app/assembly.py',
    'data pack/services/rest_service.py',
    'data pack/services/xml_service.py'
]
missing = [f for f in files if not os.path.exists(f)]
for f in missing:
    print('MISSING:', f)
if missing:
    sys.exit(1)
print('All files OK.')
"
if [ $? -ne 0 ]; then
    echo ""
    echo "ERROR: One or more required files are missing."
    echo "Make sure you are running this script from the repo root folder."
    echo "  cd BriteSpark"
    echo "  bash start.sh"
    echo ""
    exit 1
fi
echo ""

# Start REST mock in new terminal window
echo "[1/3] Starting REST Mock Service on port 8081..."
if command -v gnome-terminal &>/dev/null; then
    gnome-terminal --title="REST Mock - Port 8081" -- bash -c "$PYTHON 'data pack/services/rest_service.py' --port 8081; exec bash"
elif command -v xterm &>/dev/null; then
    xterm -title "REST Mock - Port 8081" -e "$PYTHON 'data pack/services/rest_service.py' --port 8081; bash" &
elif [[ "$OSTYPE" == "darwin"* ]]; then
    osascript -e "tell application \"Terminal\" to do script \"cd '$(pwd)' && $PYTHON 'data pack/services/rest_service.py' --port 8081\""
else
    # Fallback: run in background and log to file
    $PYTHON "data pack/services/rest_service.py" --port 8081 > /tmp/rest_mock.log 2>&1 &
    echo "  (Running in background. Logs: /tmp/rest_mock.log)"
fi

sleep 2

# Start XML mock in new terminal window
echo "[2/3] Starting XML Mock Service on port 8082 (failure-rate 0.40)..."
if command -v gnome-terminal &>/dev/null; then
    gnome-terminal --title="XML Mock - Port 8082" -- bash -c "$PYTHON 'data pack/services/xml_service.py' --port 8082 --failure-rate 0.40; exec bash"
elif command -v xterm &>/dev/null; then
    xterm -title "XML Mock - Port 8082" -e "$PYTHON 'data pack/services/xml_service.py' --port 8082 --failure-rate 0.40; bash" &
elif [[ "$OSTYPE" == "darwin"* ]]; then
    osascript -e "tell application \"Terminal\" to do script \"cd '$(pwd)' && $PYTHON 'data pack/services/xml_service.py' --port 8082 --failure-rate 0.40\""
else
    $PYTHON "data pack/services/xml_service.py" --port 8082 --failure-rate 0.40 > /tmp/xml_mock.log 2>&1 &
    echo "  (Running in background. Logs: /tmp/xml_mock.log)"
fi

sleep 3

# Start Unified API in new terminal window
echo "[3/3] Starting Unified API on port 8090..."
if command -v gnome-terminal &>/dev/null; then
    gnome-terminal --title="Unified API - Port 8090" -- bash -c "$PYTHON -m app.api --port 8090; exec bash"
elif command -v xterm &>/dev/null; then
    xterm -title "Unified API - Port 8090" -e "$PYTHON -m app.api --port 8090; bash" &
elif [[ "$OSTYPE" == "darwin"* ]]; then
    osascript -e "tell application \"Terminal\" to do script \"cd '$(pwd)' && $PYTHON -m app.api --port 8090\""
else
    $PYTHON -m app.api --port 8090 > /tmp/unified_api.log 2>&1 &
    echo "  (Running in background. Logs: /tmp/unified_api.log)"
fi

sleep 3

echo ""
echo "============================================================"
echo "  All 3 services started."
echo "============================================================"
echo ""
echo "  REST Mock      ->  http://127.0.0.1:8081"
echo "  XML Mock       ->  http://127.0.0.1:8082"
echo "  Unified API    ->  http://127.0.0.1:8090"
echo ""
echo "============================================================"
echo "  Health Check:"
echo "============================================================"
echo ""
$PYTHON -c "
import urllib.request, json, time
time.sleep(1)
try:
    r = urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=5).read()
    print(json.dumps(json.loads(r), indent=2))
except Exception as e:
    print('  Health check failed:', e)
    print('  Wait a few seconds and open: http://127.0.0.1:8090/health')
"
echo ""
echo "============================================================"
echo "  Quick Test URLs:"
echo "============================================================"
echo ""
echo "  All Residents:"
echo "  http://127.0.0.1:8090/unified/residents"
echo ""
echo "  Single Resident (by REST ID):"
echo "  http://127.0.0.1:8090/unified/residents/R-10697"
echo ""
echo "  Same Resident (by XML Reference - No Wrong Door):"
echo "  http://127.0.0.1:8090/unified/residents/NO/2019/4697"
echo ""
echo "============================================================"
