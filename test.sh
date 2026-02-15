#!/bin/bash
set -eo pipefail

API_URL="http://localhost:${API_PORT:-6542}"

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

# Check API
echo -n "API ping:  "
RESP=$(curl -s --connect-timeout 5 "${API_URL}/ping" 2>/dev/null || true)
if [ -n "${RESP}" ]; then
    echo "${RESP}"
else
    echo "NOT REACHABLE (VM may still be starting)"
    echo ""
    echo "Check noVNC: http://localhost:${NOVNC_PORT:-8006}"
    echo "Check logs:  cat metatrader5/logs/api.log"
    exit 1
fi

# Terminal info
echo -n "Terminal:  "
curl -s "${API_URL}/terminal_info" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'error' in d:
    print(d['error'])
else:
    print(f\"build {d.get('build', '?')} | connected: {d.get('connected', '?')}\")
" 2>/dev/null || echo "N/A"

# Account info
echo -n "Account:   "
curl -s "${API_URL}/account_info" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'error' in d:
    print(d['error'])
else:
    print(f\"login {d.get('login', '?')} | balance: {d.get('balance', '?')} {d.get('currency', '')}\")
" 2>/dev/null || echo "N/A"

echo ""
echo "API endpoints: ${API_URL}"
