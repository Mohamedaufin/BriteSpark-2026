#!/usr/bin/env bash
# Starts both mock services. Ctrl-C stops them.
set -euo pipefail
cd "$(dirname "$0")"
: "${BENEFITS_FAILURE_RATE:=0.15}"
export BENEFITS_FAILURE_RATE
python3 rest_service.py --port 8081 &
REST=$!
python3 xml_service.py  --port 8082 &
XML=$!
trap 'kill $REST $XML 2>/dev/null || true' EXIT INT TERM
echo
echo "  REST  http://127.0.0.1:8081/residents?page=1"
echo "  XML   http://127.0.0.1:8082/records"
echo
wait
