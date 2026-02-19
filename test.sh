#!/bin/bash
set -eo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "MT5 HTTP API Status Check"
echo "========================="
echo ""

# Check container
echo -n "Container: "
if docker ps --filter name=mt5-httpapi --format '{{.Status}}' | grep -q "Up"; then
    docker ps --filter name=mt5-httpapi --format '{{.Status}}'
else
    echo "NOT RUNNING"
    exit 1
fi

# Build list of ports to check
if [ -f "${DIR}/config/terminals.json" ]; then
    API_PORTS=$(python3 -c "
import json
ports = [t['port'] for t in json.load(open('${DIR}/config/terminals.json'))]
print(' '.join(str(p) for p in ports))
" 2>/dev/null)
else
    API_PORTS="${API_PORT:-6542}"
fi

for PORT in ${API_PORTS}; do
    API_URL="http://localhost:${PORT}"
    echo ""
    echo "--- Port ${PORT} ---"

    # Check API
    echo -n "API ping:  "
    RESP=$(curl -s --connect-timeout 5 "${API_URL}/ping" 2>/dev/null || true)
    if [ -z "${RESP}" ]; then
        echo "NOT REACHABLE (VM may still be starting)"
        echo "Check noVNC: http://localhost:${NOVNC_PORT:-8006}"
        continue
    fi
    echo "${RESP}"

    # Terminal info
    echo -n "Terminal:  "
    curl -s "${API_URL}/terminal" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'error' in d:
    print(d['error'])
else:
    print(f\"build {d.get('build', '?')} | connected: {d.get('connected', '?')}\")
" 2>/dev/null || echo "N/A"

    # Account info
    echo -n "Account:   "
    curl -s "${API_URL}/account" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'error' in d:
    print(d['error'])
else:
    print(f\"login {d.get('login', '?')} | balance: {d.get('balance', '?')} {d.get('currency', '')}\")
" 2>/dev/null || echo "N/A"
done

echo ""
