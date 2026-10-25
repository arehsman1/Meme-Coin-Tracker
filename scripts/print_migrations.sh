#!/usr/bin/env bash
# Print migration files in apply order. Does not connect to Supabase.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$ROOT/supabase/migrations"
echo "Apply these SQL files in order (Supabase SQL Editor or supabase db push):"
echo
i=1
for f in $(ls "$DIR"/*.sql 2>/dev/null | sort); do
  echo "  $i. $(basename "$f")"
  i=$((i + 1))
done
echo
echo "After apply, verify:"
echo "  SELECT table_name FROM information_schema.tables"
echo "  WHERE table_schema='public' AND table_name IN"
echo "    ('tokens','tracked_wallets','wallet_transactions','historical_outcomes',"
echo "     'notification_outbox','scanner_service_settings');"
