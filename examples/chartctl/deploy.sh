#!/usr/bin/env bash
set -euo pipefail

# Chart Deployments — example workflow
# Stage an expert + set, deploy to a chart, verify, then tear down.
#
# Usage:
#   export MT5_API_URL=http://localhost:8888/yourbroker/yourlogin
#   export MT5_API_TOKEN=your-api-token
#   ./deploy.sh <path/to/expert.ex5> <path/to/set.set> <symbol> <timeframe>
#
# Example:
#   ./deploy.sh ~/EAs/MyScalp.ex5 ~/EAs/eurusd-m5.set EURUSD M5

MT5_API_URL="${MT5_API_URL:?Set MT5_API_URL}"
MT5_API_TOKEN="${MT5_API_TOKEN:-}"
AUTH=()
if [ -n "$MT5_API_TOKEN" ]; then
  AUTH=(-H "Authorization: Bearer $MT5_API_TOKEN")
fi

EXPERT_PATH="${1:?Usage: deploy.sh <expert.ex5> <set.set> <symbol> <timeframe>}"
SET_PATH="${2:?}"
SYMBOL="${3:?}"
TIMEFRAME="${4:?}"
EXPERT_NAME=$(basename "$EXPERT_PATH")
SET_NAME=$(basename "$SET_PATH")

echo "=== Step 1: Stage expert ==="
curl -sS "${AUTH[@]}" -F "expert=@$EXPERT_PATH" "$MT5_API_URL/experts" | head -c 200
echo

echo "=== Step 2: Stage set file ==="
curl -sS "${AUTH[@]}" -F "set=@$SET_PATH" "$MT5_API_URL/sets" | head -c 200
echo

echo "=== Step 3: Create deployment ==="
DEPLOY_JSON=$(curl -sS -X POST "${AUTH[@]}" \
  -H "Content-Type: application/json" \
  "$MT5_API_URL/deployments" \
  -d "{\"expert\":\"$EXPERT_NAME\",\"set\":\"$SET_NAME\",\"symbol\":\"$SYMBOL\",\"timeframe\":\"$TIMEFRAME\"}")
echo "$DEPLOY_JSON" | head -c 300
echo
DEPLOY_ID=$(echo "$DEPLOY_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "")

if [ -z "$DEPLOY_ID" ]; then
  echo "Failed to extract deployment id from response."
  exit 1
fi

echo "=== Step 4: Poll until running (up to 60s) ==="
for i in $(seq 1 12); do
  sleep 5
  STATUS=$(curl -sS "${AUTH[@]}" "$MT5_API_URL/deployments/$DEPLOY_ID" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")
  echo "  Attempt $i: status=$STATUS"
  if [ "$STATUS" = "running" ]; then
    echo "=== Deployment running! ==="
    break
  fi
done

echo "=== Step 5: Verify via /charts ==="
curl -sS "${AUTH[@]}" "$MT5_API_URL/charts" | python3 -m json.tool | head -30

echo "=== Step 6: Take a screenshot ==="
CHART_ID=$(curl -sS "${AUTH[@]}" "$MT5_API_URL/charts" \
  | python3 -c "import sys,json; charts=json.load(sys.stdin).get('charts',[]); print([c['id'] for c in charts if c.get('deployment_id')=='$DEPLOY_ID'][0])" 2>/dev/null || echo "")
if [ -n "$CHART_ID" ]; then
  curl -sS "${AUTH[@]}" "$MT5_API_URL/charts/$CHART_ID/screenshot" -o "chartctl-${SYMBOL}-${TIMEFRAME}.png"
  echo "Screenshot saved to chartctl-${SYMBOL}-${TIMEFRAME}.png"
fi

echo "=== Done ==="
echo "Deployment $DEPLOY_ID is running. Clean up with:"
echo "  curl -X DELETE ${AUTH[@]} \"$MT5_API_URL/deployments/$DEPLOY_ID\""
