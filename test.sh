#!/bin/bash
set -eo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0
FAIL=0
SKIP=0

pass() { echo "  PASS  $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL  $1 — $2"; FAIL=$((FAIL + 1)); }
skip() { echo "  SKIP  $1 — $2"; SKIP=$((SKIP + 1)); }

# ── Discover config ─────────────────────────────────────────────
if [ ! -f "${DIR}/config/config.yaml" ]; then
    echo "ERROR: config/config.yaml not found. Need at least one terminal configured."
    exit 1
fi

CFG="${DIR}/scripts/config_helper.py"

# Pick the first terminal's broker/account as test target. nginx (on
# API_HOST_PORT, default 8888) routes /<broker>/<account>/... to the
# right terminal — per-terminal ports are container-internal now.
FIRST_PREFIX=$(python3 "$CFG" terminals 2>/dev/null | head -n1 | awk '{print $1"/"$2}')

if [ -z "$FIRST_PREFIX" ] || [ "$FIRST_PREFIX" = "/" ]; then
    echo "ERROR: no terminals found in config.yaml"
    exit 1
fi

API_HOST_PORT="${API_HOST_PORT:-8888}"
BASE="http://localhost:${API_HOST_PORT}/${FIRST_PREFIX}"

# Detect auth
AUTH=""
TOKEN=$(python3 "$CFG" api_token 2>/dev/null | tr -d '[:space:]')
if [ -n "$TOKEN" ]; then
    AUTH="-H Authorization:\ Bearer\ ${TOKEN}"
fi

api() {
    local method="$1" path="$2"
    if [ -n "$AUTH" ]; then
        curl -s --max-time 15 -X "$method" -H "Authorization: Bearer ${TOKEN}" "${BASE}${path}"
    else
        curl -s --max-time 15 -X "$method" "${BASE}${path}"
    fi
}

api_status() {
    local method="$1" path="$2"
    if [ -n "$AUTH" ]; then
        curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X "$method" -H "Authorization: Bearer ${TOKEN}" "${BASE}${path}"
    else
        curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X "$method" "${BASE}${path}"
    fi
}

echo "MT5 HTTP API Tests"
echo "==================="
echo "Target: ${BASE}"
echo "Auth:   $([ -n "$AUTH" ] && echo 'yes' || echo 'no')"
echo ""

# ── Connectivity ────────────────────────────────────────────────
echo "--- Connectivity ---"

RESP=$(api GET /ping 2>/dev/null || true)
if [ -z "$RESP" ]; then
    echo "FATAL: API not reachable at ${BASE}. Is the VM running?"
    exit 1
fi
echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='ok'" 2>/dev/null \
    && pass "/ping returns {status:ok}" \
    || fail "/ping" "unexpected response: $RESP"

# Auth test (only if token is configured)
if [ -n "$TOKEN" ]; then
    CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${BASE}/ping")
    [ "$CODE" = "401" ] \
        && pass "/ping without token returns 401" \
        || fail "/ping no-auth" "expected 401 got $CODE"
fi

# All terminals reachable through nginx
echo ""
echo "--- All Terminals ---"
TERM_PREFIXES=$(python3 "$CFG" terminals 2>/dev/null | awk '{print $1"/"$2}')
for PREFIX in $TERM_PREFIXES; do
    URL="http://localhost:${API_HOST_PORT}/${PREFIX}/ping"
    if [ -n "$TOKEN" ]; then
        CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
            -H "Authorization: Bearer ${TOKEN}" \
            "$URL" 2>/dev/null || echo "000")
    else
        CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
            "$URL" 2>/dev/null || echo "000")
    fi
    [ "$CODE" = "200" ] \
        && pass "${PREFIX} up" \
        || fail "${PREFIX}" "got $CODE"
done

# ── Terminal / Account ──────────────────────────────────────────
echo ""
echo "--- Terminal & Account ---"

TERM=$(api GET /terminal)
echo "$TERM" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('connected') is not None" 2>/dev/null \
    && pass "GET /terminal returns terminal info" \
    || fail "GET /terminal" "missing fields"

ACC=$(api GET /account)
echo "$ACC" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('login',0) > 0" 2>/dev/null \
    && pass "GET /account has login" \
    || fail "GET /account" "bad response"

ERR=$(api GET /error)
echo "$ERR" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'code' in d" 2>/dev/null \
    && pass "GET /error returns error info" \
    || fail "GET /error" "bad response"

# ── Symbols ─────────────────────────────────────────────────────
echo ""
echo "--- Symbols ---"

SYMS=$(api GET /symbols)
SYM_COUNT=$(echo "$SYMS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$SYM_COUNT" -gt 0 ] \
    && pass "GET /symbols returns ${SYM_COUNT} symbols" \
    || fail "GET /symbols" "empty list"

# Pick EURUSD or first available symbol
TEST_SYM=$(echo "$SYMS" | python3 -c "
import sys, json
syms = json.load(sys.stdin)
print('EURUSD' if 'EURUSD' in syms else syms[0])
" 2>/dev/null || echo "EURUSD")

INFO=$(api GET "/symbols/${TEST_SYM}")
echo "$INFO" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('name')=='${TEST_SYM}'" 2>/dev/null \
    && pass "GET /symbols/${TEST_SYM} returns symbol info" \
    || fail "GET /symbols/${TEST_SYM}" "bad response"

TICK=$(api GET "/symbols/${TEST_SYM}/tick")
echo "$TICK" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('bid',0) > 0" 2>/dev/null \
    && pass "GET /symbols/${TEST_SYM}/tick has bid" \
    || fail "GET /symbols/${TEST_SYM}/tick" "bad response"

CODE=$(api_status GET "/symbols/ZZZZNOTEXIST123")
[ "$CODE" = "404" ] \
    && pass "GET /symbols/invalid returns 404" \
    || fail "GET /symbols/invalid" "expected 404 got $CODE"

# ── Rates ───────────────────────────────────────────────────────
echo ""
echo "--- Rates ---"

# Default: last 100 M1 bars
RATES=$(api GET "/symbols/${TEST_SYM}/rates")
R_COUNT=$(echo "$RATES" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$R_COUNT" -gt 0 ] \
    && pass "GET rates default returns ${R_COUNT} bars" \
    || fail "GET rates default" "empty"

# Positive count
RATES=$(api GET "/symbols/${TEST_SYM}/rates?timeframe=H1&count=10")
R_COUNT=$(echo "$RATES" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$R_COUNT" -le 10 ] && [ "$R_COUNT" -gt 0 ] \
    && pass "GET rates count=10 returns ${R_COUNT} bars" \
    || fail "GET rates count=10" "got $R_COUNT"

# Forward from timestamp
NOW=$(date -u +%s)
WEEK_AGO=$((NOW - 604800))
RATES=$(api GET "/symbols/${TEST_SYM}/rates?timeframe=H1&from=${WEEK_AGO}&count=5")
R_COUNT=$(echo "$RATES" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$R_COUNT" -gt 0 ] \
    && pass "GET rates from=week_ago count=5 returns ${R_COUNT} bars" \
    || fail "GET rates from+count forward" "empty"

# Negative count (backward from timestamp)
RATES_JSON=$(api GET "/symbols/${TEST_SYM}/rates?timeframe=H1&from=${NOW}&count=-10")
R_COUNT=$(echo "$RATES_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$R_COUNT" -gt 0 ] && [ "$R_COUNT" -le 10 ] \
    && pass "GET rates count=-10 returns ${R_COUNT} bars backward" \
    || fail "GET rates count=-10" "got $R_COUNT"

# Negative count: all bars should be <= from
if [ "$R_COUNT" -gt 0 ]; then
    echo "$RATES_JSON" | python3 -c "
import sys, json
bars = json.load(sys.stdin)
anchor = ${NOW}
bad = [b for b in bars if b['time'] > anchor]
assert len(bad) == 0, f'{len(bad)} bars after anchor'
" 2>/dev/null \
        && pass "GET rates count=-10 all bars <= anchor" \
        || fail "GET rates count=-10 filter" "bars after anchor found"
fi

# Count=0 returns empty
RATES=$(api GET "/symbols/${TEST_SYM}/rates?timeframe=H1&count=0")
R_COUNT=$(echo "$RATES" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$R_COUNT" -eq 0 ] \
    && pass "GET rates count=0 returns empty" \
    || fail "GET rates count=0" "got $R_COUNT"

# Negative count with ancient from (pre-data) should return empty, not crash
CODE=$(api_status GET "/symbols/${TEST_SYM}/rates?timeframe=W1&from=86400&count=-10")
[ "$CODE" = "200" ] \
    && pass "GET rates backward from epoch doesn't crash (HTTP $CODE)" \
    || fail "GET rates backward from epoch" "got HTTP $CODE"

# Range mode: from+to
DAY_AGO=$((NOW - 86400))
RATES=$(api GET "/symbols/${TEST_SYM}/rates?timeframe=H1&from=${DAY_AGO}&to=${NOW}")
R_COUNT=$(echo "$RATES" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$R_COUNT" -gt 0 ] \
    && pass "GET rates from+to range returns ${R_COUNT} bars" \
    || fail "GET rates from+to" "empty"

# Range mode: count+to is mutually exclusive
CODE=$(api_status GET "/symbols/${TEST_SYM}/rates?timeframe=H1&from=${DAY_AGO}&to=${NOW}&count=10")
[ "$CODE" = "400" ] \
    && pass "GET rates count+to returns 400" \
    || fail "GET rates count+to" "expected 400 got $CODE"

# Range mode: to without from is invalid
CODE=$(api_status GET "/symbols/${TEST_SYM}/rates?timeframe=H1&to=${NOW}")
[ "$CODE" = "400" ] \
    && pass "GET rates to-without-from returns 400" \
    || fail "GET rates to-without-from" "expected 400 got $CODE"

# Datetime string: YYYY_MM_DD_HH_MM_SS
DT_FROM=$(date -u -d "@${DAY_AGO}" +%Y_%m_%d_%H_%M_%S)
DT_TO=$(date -u -d "@${NOW}" +%Y_%m_%d_%H_%M_%S)
RATES=$(api GET "/symbols/${TEST_SYM}/rates?timeframe=H1&from=${DT_FROM}&to=${DT_TO}")
R_COUNT=$(echo "$RATES" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$R_COUNT" -gt 0 ] \
    && pass "GET rates Y_M_D_H_M_S range returns ${R_COUNT} bars" \
    || fail "GET rates Y_M_D_H_M_S range" "empty"

# Date-only: YYYY_MM_DD (midnight UTC)
D_FROM=$(date -u -d "@${DAY_AGO}" +%Y_%m_%d)
RATES=$(api GET "/symbols/${TEST_SYM}/rates?timeframe=H1&from=${D_FROM}&count=10")
R_COUNT=$(echo "$RATES" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$R_COUNT" -gt 0 ] \
    && pass "GET rates Y_M_D date-only anchor returns ${R_COUNT} bars" \
    || fail "GET rates Y_M_D anchor" "empty"

# Bogus date string returns 400
CODE=$(api_status GET "/symbols/${TEST_SYM}/rates?timeframe=H1&from=not_a_date&count=5")
[ "$CODE" = "400" ] \
    && pass "GET rates bogus date returns 400" \
    || fail "GET rates bogus date" "expected 400 got $CODE"

# Invalid timeframe
CODE=$(api_status GET "/symbols/${TEST_SYM}/rates?timeframe=BOGUS")
[ "$CODE" = "400" ] \
    && pass "GET rates invalid timeframe returns 400" \
    || fail "GET rates invalid timeframe" "expected 400 got $CODE"

# ── Ticks ───────────────────────────────────────────────────────
echo ""
echo "--- Ticks ---"

TICKS=$(api GET "/symbols/${TEST_SYM}/ticks?count=5")
T_COUNT=$(echo "$TICKS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$T_COUNT" -gt 0 ] \
    && pass "GET ticks count=5 returns ${T_COUNT} ticks" \
    || fail "GET ticks count=5" "empty"

# Forward from timestamp
TICKS=$(api GET "/symbols/${TEST_SYM}/ticks?from=${WEEK_AGO}&count=5")
T_COUNT=$(echo "$TICKS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$T_COUNT" -gt 0 ] \
    && pass "GET ticks from+count forward returns ${T_COUNT}" \
    || fail "GET ticks from+count forward" "empty"

# Negative count backward (anchor from the latest tick we just fetched)
TICKS_BK=$(api GET "/symbols/${TEST_SYM}/ticks?from=${WEEK_AGO}&count=-5")
T_BK_COUNT=$(echo "$TICKS_BK" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$T_BK_COUNT" -gt 0 ] && [ "$T_BK_COUNT" -le 5 ] \
    && pass "GET ticks count=-5 returns ${T_BK_COUNT} ticks backward" \
    || fail "GET ticks count=-5" "got $T_BK_COUNT"

# Range mode: from+to (small window so we don't drown in ticks)
HOUR_AGO=$((NOW - 3600))
TICKS=$(api GET "/symbols/${TEST_SYM}/ticks?from=${HOUR_AGO}&to=${NOW}")
T_COUNT=$(echo "$TICKS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$T_COUNT" -ge 0 ] \
    && pass "GET ticks from+to range returns ${T_COUNT} ticks" \
    || fail "GET ticks from+to" "got $T_COUNT"

# Range mode: count+to is mutually exclusive
CODE=$(api_status GET "/symbols/${TEST_SYM}/ticks?from=${HOUR_AGO}&to=${NOW}&count=5")
[ "$CODE" = "400" ] \
    && pass "GET ticks count+to returns 400" \
    || fail "GET ticks count+to" "expected 400 got $CODE"

# Range mode: to without from is invalid
CODE=$(api_status GET "/symbols/${TEST_SYM}/ticks?to=${NOW}")
[ "$CODE" = "400" ] \
    && pass "GET ticks to-without-from returns 400" \
    || fail "GET ticks to-without-from" "expected 400 got $CODE"

# ── Positions & Orders (read-only) ──────────────────────────────
echo ""
echo "--- Positions & Orders ---"

CODE=$(api_status GET /positions)
[ "$CODE" = "200" ] \
    && pass "GET /positions returns 200" \
    || fail "GET /positions" "got $CODE"

CODE=$(api_status GET /orders)
[ "$CODE" = "200" ] \
    && pass "GET /orders returns 200" \
    || fail "GET /orders" "got $CODE"

# ── History ─────────────────────────────────────────────────────
echo ""
echo "--- History ---"

DAY_AGO=$((NOW - 86400))
CODE=$(api_status GET "/history/orders?from=${DAY_AGO}&to=${NOW}")
[ "$CODE" = "200" ] \
    && pass "GET /history/orders returns 200" \
    || fail "GET /history/orders" "got $CODE"

CODE=$(api_status GET "/history/deals?from=${DAY_AGO}&to=${NOW}")
[ "$CODE" = "200" ] \
    && pass "GET /history/deals returns 200" \
    || fail "GET /history/deals" "got $CODE"

# History without params should 400
CODE=$(api_status GET "/history/orders")
[ "$CODE" = "400" ] \
    && pass "GET /history/orders no params returns 400" \
    || fail "GET /history/orders no params" "expected 400 got $CODE"

# ── Summary ─────────────────────────────────────────────────────
echo ""
echo "==================="
echo "PASS: ${PASS}  FAIL: ${FAIL}  SKIP: ${SKIP}"
[ "$FAIL" -eq 0 ] && echo "ALL TESTS PASSED" || echo "SOME TESTS FAILED"
exit $FAIL
