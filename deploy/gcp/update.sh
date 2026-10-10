#!/usr/bin/env bash
# Self-update: pull the latest branch, rebuild what changed, restart, verify.
#
# Installed by setup.sh as /usr/local/sbin/orchestrators-update (root-owned,
# so the app user cannot change what root runs) and started by
# orchestrators-update.path when the backend drops data/update.request
# (POST /api/admin/update). Before anything else it refreshes itself from
# GitHub (pinned repo URL, not the app checkout), so changes to this file
# apply from the next update on.
#
# Progress goes to data/update-status.json (one entry per step) and command
# output to data/update.log; GET /api/admin/update serves both.
#
# Manual run: sudo systemctl start orchestrators-update
set -Eeuo pipefail

APP_DIR=/opt/orchestrators
APP_USER=orchestrators
BRANCH="${BRANCH:-main}"
REPO_SLUG="${REPO_SLUG:-kyrill99/Orchestrators}"
DATA="$APP_DIR/orchestrators-backend/data"
STATUS="$DATA/update-status.json"
LOG="$DATA/update.log"
SELF=/usr/local/sbin/orchestrators-update

as_app() { runuser -u "$APP_USER" -- "$@"; }

if [[ -z "${ORCH_UPDATER_FRESH:-}" ]]; then
  SHA=$(git ls-remote "https://github.com/${REPO_SLUG}.git" "refs/heads/$BRANCH" 2>/dev/null | cut -f1 || true)
  TMP=$(mktemp)
  if [[ -n "$SHA" ]] \
    && curl -fsSL "https://raw.githubusercontent.com/${REPO_SLUG}/${SHA}/deploy/gcp/update.sh" -o "$TMP" \
    && bash -n "$TMP" && ! cmp -s "$TMP" "$SELF"; then
    install -m 755 -o root -g root "$TMP" "$SELF"
    rm -f "$TMP"
    echo "==> Updater refreshed to ${SHA:0:7}"
    ORCH_UPDATER_FRESH=1 exec "$SELF"
  fi
  rm -f "$TMP"
fi

rm -f "$DATA/update.request"
: > "$LOG"
chmod 644 "$LOG"

# status init <from> <step ids> | to <sha> | step <id> <state> [detail] | finish <state> [detail]
status() {
  as_app python3 - "$STATUS" "$@" <<'EOF'
import datetime, json, os, sys
LABELS = {
    "fetch": "Fetch code",
    "frontend_deps": "Frontend packages",
    "frontend_build": "Build frontend",
    "backend_deps": "Backend packages",
    "restart": "Restart services",
    "verify": "Health check",
}
now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
path, op, *args = sys.argv[1:]
if op == "init":
    s = {"state": "running", "from": args[0], "to": None, "detail": None,
         "started_at": now, "finished_at": None,
         "steps": [{"id": i, "label": LABELS[i], "state": "pending", "detail": None,
                    "started_at": None, "finished_at": None} for i in args[1].split()]}
else:
    s = json.load(open(path))
    if op == "to":
        s["to"] = args[0]
    elif op == "step":
        for st in s["steps"]:
            if st["id"] == args[0]:
                st["state"] = args[1]
                st["started_at" if args[1] == "running" else "finished_at"] = now
                if len(args) > 2 and args[2]:
                    st["detail"] = args[2]
    elif op == "finish":
        s["state"], s["finished_at"] = args[0], now
        s["detail"] = args[1] if len(args) > 1 and args[1] else None
        for st in s["steps"]:
            if st["state"] == "pending":
                st["state"] = "skipped"
with open(path + ".tmp", "w") as f:
    json.dump(s, f)
os.replace(path + ".tmp", path)
EOF
}

CURRENT=""
step() { CURRENT=$1; status step "$1" running; echo "==> $1" | tee -a "$LOG"; }
done_step() { status step "$CURRENT" done "${1:-}"; CURRENT=""; }
skip() { status step "$1" skipped "$2"; }
run() { "$@" >>"$LOG" 2>&1; }

failed() {
  local rc=$1 line=$2
  [[ -n "$CURRENT" ]] && status step "$CURRENT" failed "exit $rc (line $line)"
  status finish failed "${CURRENT:-start} failed, exit $rc"
}
trap 'failed $? $LINENO' ERR

FROM=$(as_app git -C "$APP_DIR" rev-parse --short HEAD)
status init "$FROM" "fetch frontend_deps frontend_build backend_deps restart verify"

step fetch
run as_app git -C "$APP_DIR" fetch origin "$BRANCH"
TO=$(as_app git -C "$APP_DIR" rev-parse --short "origin/$BRANCH")
status to "$TO"
if [[ "$FROM" == "$TO" ]]; then
  done_step "already at $TO"
  status finish up_to_date
  exit 0
fi
CHANGED=$(as_app git -C "$APP_DIR" diff --name-only "$FROM" "$TO")
run as_app git -C "$APP_DIR" reset --hard "origin/$BRANCH"
done_step "$FROM → $TO, $(wc -l <<<"$CHANGED") files"

changed() { grep -q "^$1" <<<"$CHANGED"; }
FRONT=0; BACK=0
changed orchestrators-frontend/ && FRONT=1
[[ -d "$APP_DIR/orchestrators-frontend/.next" ]] || FRONT=1
changed orchestrators-backend/ && BACK=1

cd "$APP_DIR/orchestrators-frontend"
if [[ $FRONT == 1 ]] && { changed orchestrators-frontend/package-lock.json || [[ ! -d node_modules ]]; }; then
  step frontend_deps
  run as_app npm ci --no-audit --no-fund --loglevel=error
  done_step
else
  skip frontend_deps "unchanged"
fi
if [[ $FRONT == 1 ]]; then
  step frontend_build
  # Empty API URL = same-origin /api/... calls, routed to the backend by oauth2-proxy.
  run as_app env NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_API_URL= npm run build
  done_step
else
  skip frontend_build "no frontend changes"
fi

cd "$APP_DIR/orchestrators-backend"
if changed orchestrators-backend/requirements.txt; then
  step backend_deps
  run as_app .venv/bin/pip install -r requirements.txt
  done_step
else
  skip backend_deps "unchanged"
fi

SERVICES=()
[[ $FRONT == 1 ]] && SERVICES+=(orchestrators-frontend)
[[ $BACK == 1 ]] && SERVICES+=(orchestrators-backend)
if [[ ${#SERVICES[@]} -eq 0 ]]; then
  skip restart "nothing to restart"
  skip verify "nothing restarted"
  status finish ok
  exit 0
fi

step restart
systemctl restart "${SERVICES[@]}"
done_step "${SERVICES[*]#orchestrators-}"

wait_http() {
  local code
  for _ in $(seq 1 60); do
    code=$(curl -s -o /dev/null -m 3 -w '%{http_code}' "$1" || true)
    [[ "$code" =~ ^[23] ]] && return 0
    sleep 2
  done
  echo "no healthy answer from $1 (last: $code)" >>"$LOG"
  return 1
}
step verify
wait_http http://127.0.0.1:8000/health
wait_http http://127.0.0.1:3000/
done_step "backend and frontend answering"

status finish ok
echo "==> Updated $FROM -> $TO"
