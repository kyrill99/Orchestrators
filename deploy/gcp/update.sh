#!/usr/bin/env bash
# Self-update: pull the latest branch, rebuild, restart frontend + backend.
#
# Installed by setup.sh as /usr/local/sbin/orchestrators-update (root-owned,
# so the app user cannot change what root runs) and started by
# orchestrators-update.path when the backend drops data/update.request
# (POST /api/admin/update). Changes to this file take effect after the
# next setup.sh run.
#
# Manual run: sudo systemctl start orchestrators-update
set -euo pipefail

APP_DIR=/opt/orchestrators
APP_USER=orchestrators
BRANCH="${BRANCH:-main}"
DATA="$APP_DIR/orchestrators-backend/data"
STATUS="$DATA/update-status.json"

as_app() { runuser -u "$APP_USER" -- "$@"; }

rm -f "$DATA/update.request"

STARTED=$(date -Is)
FROM=$(as_app git -C "$APP_DIR" rev-parse --short HEAD)
TO=""

status() {
  as_app python3 - "$STATUS" "$1" "$STARTED" "$FROM" "$TO" "${2:-}" <<'EOF'
import json, sys, datetime
path, state, started, frm, to, detail = sys.argv[1:]
done = state != "running"
json.dump({
    "state": state, "from": frm, "to": to or None, "detail": detail or None,
    "started_at": started,
    "finished_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds") if done else None,
}, open(path, "w"))
EOF
}

STEP="start"
trap 'status failed "$STEP (line $LINENO)"' ERR

status running
echo "==> Updating from $FROM"

STEP="git fetch"
as_app git -C "$APP_DIR" fetch -q origin "$BRANCH"
TO=$(as_app git -C "$APP_DIR" rev-parse --short "origin/$BRANCH")
if [[ "$FROM" == "$TO" ]]; then
  echo "==> Already at $TO"
  status up_to_date
  exit 0
fi
as_app git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
status running "pulled $TO"

STEP="frontend build"
cd "$APP_DIR/orchestrators-frontend"
as_app npm ci --no-audit --no-fund --loglevel=error
# Empty API URL = same-origin /api/... calls, routed to the backend by oauth2-proxy.
as_app env NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_API_URL= npm run build

STEP="backend deps"
cd "$APP_DIR/orchestrators-backend"
as_app .venv/bin/pip install -q -r requirements.txt

STEP="restart"
status ok
systemctl restart orchestrators-frontend orchestrators-backend
echo "==> Updated $FROM -> $TO"
