#!/usr/bin/env bash
# One-shot setup for a GCP VM (Debian/Ubuntu): frontend + backend behind
# Google login (oauth2-proxy) and HTTPS (Caddy + Let's Encrypt) on
# https://<ip-with-dashes>.sslip.io
#
# Usage (on the VM):
#   curl -fsSL https://raw.githubusercontent.com/kyrill99/Orchestrators/main/deploy/gcp/setup.sh -o setup.sh
#   sudo OAUTH_CLIENT_ID=... OAUTH_CLIENT_SECRET=... ALLOWED_EMAILS="a@x.com,b@y.com" bash setup.sh
#
# Re-run any time to pull the latest main and rebuild.
set -euo pipefail

: "${OAUTH_CLIENT_ID:?set OAUTH_CLIENT_ID}"
: "${OAUTH_CLIENT_SECRET:?set OAUTH_CLIENT_SECRET}"
ALLOWED_EMAILS="${ALLOWED_EMAILS:-}"          # comma-separated; empty = any Google account
REPO_URL="${REPO_URL:-https://github.com/kyrill99/Orchestrators.git}"
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/orchestrators
APP_USER=orchestrators

if [[ $EUID -ne 0 ]]; then echo "run with sudo" >&2; exit 1; fi

IP="${PUBLIC_IP:-$(curl -fsS -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip)}"
HOST="${PUBLIC_HOST:-${IP//./-}.sslip.io}"
echo "==> Public URL will be https://${HOST}"

echo "==> Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates gnupg python3 python3-venv debian-keyring debian-archive-keyring apt-transport-https >/dev/null

# Swap: `next build` needs more than 1 GB RAM on small VMs.
if ! swapon --show | grep -q . ; then
  echo "==> Adding 2G swap"
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

if ! command -v node >/dev/null || [[ "$(node -v | cut -d. -f1 | tr -d v)" -lt 20 ]]; then
  echo "==> Node.js 22"
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null
  apt-get install -y -qq nodejs >/dev/null
fi

if ! command -v caddy >/dev/null; then
  echo "==> Caddy"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
fi

if ! command -v oauth2-proxy >/dev/null; then
  echo "==> oauth2-proxy"
  TAG=$(curl -fsS https://api.github.com/repos/oauth2-proxy/oauth2-proxy/releases/latest | python3 -c 'import sys,json;print(json.load(sys.stdin)["tag_name"])')
  ARCH=$(dpkg --print-architecture)   # amd64 / arm64
  curl -fsSL "https://github.com/oauth2-proxy/oauth2-proxy/releases/download/${TAG}/oauth2-proxy-${TAG}.linux-${ARCH}.tar.gz" | tar xz -C /tmp
  install -m 755 /tmp/oauth2-proxy-${TAG}.linux-${ARCH}/oauth2-proxy /usr/local/bin/oauth2-proxy
fi

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"

echo "==> Code ($BRANCH)"
if [[ -d "$APP_DIR/.git" ]]; then
  sudo -u "$APP_USER" git -C "$APP_DIR" fetch -q origin "$BRANCH"
  sudo -u "$APP_USER" git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
else
  install -d -o "$APP_USER" "$APP_DIR"
  sudo -u "$APP_USER" git clone -q --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

echo "==> Frontend build"
cd "$APP_DIR/orchestrators-frontend"
sudo -u "$APP_USER" npm ci --no-audit --no-fund --loglevel=error
sudo -u "$APP_USER" NEXT_TELEMETRY_DISABLED=1 npm run build

echo "==> Backend venv"
cd "$APP_DIR/orchestrators-backend"
[[ -d .venv ]] || sudo -u "$APP_USER" python3 -m venv .venv
sudo -u "$APP_USER" .venv/bin/pip install -q --upgrade pip
sudo -u "$APP_USER" .venv/bin/pip install -q -r requirements.txt
[[ -f .env ]] || sudo -u "$APP_USER" cp .env.example .env
sudo -u "$APP_USER" mkdir -p data

echo "==> oauth2-proxy config"
install -d -m 750 -o root -g "$APP_USER" /etc/oauth2-proxy
COOKIE_FILE=/etc/oauth2-proxy/cookie_secret
[[ -f $COOKIE_FILE ]] || python3 -c 'import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())' > "$COOKIE_FILE"
EMAIL_LINES='email_domains = ["*"]'
if [[ -n "$ALLOWED_EMAILS" ]]; then
  tr ',' '\n' <<<"$ALLOWED_EMAILS" | sed 's/^ *//;s/ *$//' | grep . > /etc/oauth2-proxy/emails.txt
  EMAIL_LINES='authenticated_emails_file = "/etc/oauth2-proxy/emails.txt"'
fi
cat > /etc/oauth2-proxy/oauth2-proxy.cfg <<EOF
provider = "google"
client_id = "${OAUTH_CLIENT_ID}"
client_secret = "${OAUTH_CLIENT_SECRET}"
cookie_secret = "$(cat "$COOKIE_FILE")"
cookie_secure = true
redirect_url = "https://${HOST}/oauth2/callback"
http_address = "127.0.0.1:4180"
reverse_proxy = true
${EMAIL_LINES}
upstreams = ["http://127.0.0.1:3000/", "http://127.0.0.1:8000/api/"]
pass_user_headers = true
set_xauthrequest = true
skip_provider_button = true
EOF
chown root:"$APP_USER" /etc/oauth2-proxy/*; chmod 640 /etc/oauth2-proxy/*

echo "==> systemd services"
cat > /etc/systemd/system/orchestrators-frontend.service <<EOF
[Unit]
Description=Orchestrators frontend (Next.js)
After=network.target
[Service]
User=${APP_USER}
WorkingDirectory=${APP_DIR}/orchestrators-frontend
Environment=NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
ExecStart=/usr/bin/npm run start -- -H 127.0.0.1 -p 3000
Restart=always
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/orchestrators-backend.service <<EOF
[Unit]
Description=Orchestrators backend (FastAPI)
After=network.target
[Service]
User=${APP_USER}
WorkingDirectory=${APP_DIR}/orchestrators-backend
ExecStart=${APP_DIR}/orchestrators-backend/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/oauth2-proxy.service <<EOF
[Unit]
Description=oauth2-proxy (Google login)
After=network.target
[Service]
User=${APP_USER}
ExecStart=/usr/local/bin/oauth2-proxy --config=/etc/oauth2-proxy/oauth2-proxy.cfg
Restart=always
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/caddy/Caddyfile <<EOF
${HOST} {
    encode gzip
    reverse_proxy 127.0.0.1:4180
}
EOF

systemctl daemon-reload
systemctl enable -q orchestrators-frontend orchestrators-backend oauth2-proxy caddy
systemctl restart orchestrators-frontend orchestrators-backend oauth2-proxy caddy

sleep 3
for s in orchestrators-frontend orchestrators-backend oauth2-proxy caddy; do
  printf '%-24s %s\n' "$s" "$(systemctl is-active $s)"
done
echo
echo "Done. Open https://${HOST}  (first HTTPS request may take ~30s while the certificate is issued)"
echo "OAuth redirect URI to register: https://${HOST}/oauth2/callback"
echo "Logs: journalctl -u oauth2-proxy -u caddy -u orchestrators-frontend -f"
