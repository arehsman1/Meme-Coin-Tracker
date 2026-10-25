#!/usr/bin/env bash
# Post-deploy verification — honest PASS / FAIL / NOT_CONFIGURED
# Usage: ./scripts/verify_deploy.sh [API_BASE_URL]
set -euo pipefail

API="${1:-http://127.0.0.1:8000}"
API="${API%/}"

pass=0
fail=0
skip=0

check() {
  local name="$1"
  local status="$2"  # PASS FAIL NOT_CONFIGURED
  local detail="${3:-}"
  printf "%-40s %-16s %s\n" "$name" "$status" "$detail"
  case "$status" in
    PASS) pass=$((pass + 1)) ;;
    FAIL) fail=$((fail + 1)) ;;
    *) skip=$((skip + 1)) ;;
  esac
}

echo "=== Meme Tracker deploy verification ==="
echo "API base: $API"
echo

# Health
if code=$(curl -s -o /tmp/mt_health.json -w "%{http_code}" --max-time 10 "$API/health" 2>/dev/null); then
  if [ "$code" = "200" ]; then
    check "GET /health" "PASS" "HTTP $code"
  else
    check "GET /health" "FAIL" "HTTP $code"
  fi
else
  check "GET /health" "FAIL" "unreachable"
fi

# Ready
if code=$(curl -s -o /tmp/mt_ready.json -w "%{http_code}" --max-time 10 "$API/ready" 2>/dev/null); then
  if [ "$code" = "200" ]; then
    check "GET /ready" "PASS" "HTTP $code"
  else
    check "GET /ready" "FAIL" "HTTP $code (may be degraded without DB)"
  fi
else
  check "GET /ready" "FAIL" "unreachable"
fi

# Public settings
if code=$(curl -s -o /tmp/mt_settings.json -w "%{http_code}" --max-time 10 "$API/api/v1/settings/public" 2>/dev/null); then
  if [ "$code" = "200" ]; then
    live=$(python3 -c "import json; d=json.load(open('/tmp/mt_settings.json')); print(d.get('live_trading'))" 2>/dev/null || echo "?")
    if [ "$live" = "False" ] || [ "$live" = "false" ]; then
      check "live_trading flag" "PASS" "false (expected)"
    else
      check "live_trading flag" "FAIL" "value=$live (must be false)"
    fi
    check "GET /api/v1/settings/public" "PASS" ""
  else
    check "GET /api/v1/settings/public" "FAIL" "HTTP $code"
  fi
else
  check "GET /api/v1/settings/public" "FAIL" "unreachable"
fi

# Wallets list (memory or supabase)
if code=$(curl -s -o /tmp/mt_wallets.json -w "%{http_code}" --max-time 10 "$API/api/v1/wallets?limit=5" 2>/dev/null); then
  if [ "$code" = "200" ]; then
    storage=$(python3 -c "import json; d=json.load(open('/tmp/mt_wallets.json')); print(d.get('storage','?'))" 2>/dev/null || echo "?")
    check "GET /api/v1/wallets" "PASS" "storage=$storage"
  else
    check "GET /api/v1/wallets" "FAIL" "HTTP $code"
  fi
else
  check "GET /api/v1/wallets" "FAIL" "unreachable"
fi

# Tokens list
if code=$(curl -s -o /tmp/mt_tokens.json -w "%{http_code}" --max-time 10 "$API/api/v1/tokens?limit=5" 2>/dev/null); then
  if [ "$code" = "200" ]; then
    check "GET /api/v1/tokens" "PASS" ""
  else
    check "GET /api/v1/tokens" "FAIL" "HTTP $code"
  fi
else
  check "GET /api/v1/tokens" "FAIL" "unreachable"
fi

echo
echo "Summary: PASS=$pass FAIL=$fail NOT_CONFIGURED/other=$skip"
if [ "$fail" -gt 0 ]; then
  echo "Result: INCOMPLETE — fix failures before treating deploy as verified."
  exit 1
fi
echo "Result: API surface reachable. Confirm migrations + Telegram + HTTPS Mini App separately."
exit 0
