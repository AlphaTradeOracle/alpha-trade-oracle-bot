#!/usr/bin/env bash
# Stage Alpha Desk dashboard on VPS (Nginx + static build).
# SSL via certbot once DNS points here.
set -euo pipefail

DOMAIN_ROOT="alpha-trade-oracle.com"
DOMAIN_WWW="www.alpha-trade-oracle.com"
WEB_ROOT="/var/www/alpha-desk"
REPO="/opt/alpha-trade-oracle-bot"
BRANCH="origin/cursor/trading-dashboard-efe9"

export DEBIAN_FRONTEND=noninteractive

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) dashboard deploy start ====="

apt-get update -y
apt-get install -y nginx certbot python3-certbot-nginx curl

# Node 22 for Vite build (idempotent)
if ! command -v node >/dev/null 2>&1 || [[ "$(node -v | cut -d. -f1 | tr -d v)" -lt 20 ]]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi
node -v
npm -v

mkdir -p "$REPO" "$WEB_ROOT"
cd "$REPO"
if [[ ! -d .git ]]; then
  echo "ERROR: $REPO is not a git checkout" >&2
  exit 1
fi

git fetch origin cursor/trading-dashboard-efe9 main
# Extract dashboard from PR branch without switching live bot code
rm -rf /tmp/alpha-desk-src
mkdir -p /tmp/alpha-desk-src
git archive "$BRANCH" trading-dashboard | tar -x -C /tmp/alpha-desk-src
cd /tmp/alpha-desk-src/trading-dashboard

npm ci
npm run build

rm -rf "${WEB_ROOT:?}/"*
cp -a dist/. "$WEB_ROOT/"
chown -R www-data:www-data "$WEB_ROOT"

cat >/etc/nginx/sites-available/alpha-desk <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN_ROOT} ${DOMAIN_WWW};

    root ${WEB_ROOT};
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location ~* \.(js|css|png|jpg|jpeg|gif|svg|ico|woff2?)$ {
        expires 7d;
        add_header Cache-Control "public";
        try_files \$uri =404;
    }
}
EOF

ln -sfn /etc/nginx/sites-available/alpha-desk /etc/nginx/sites-enabled/alpha-desk
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl enable nginx
systemctl restart nginx

# Open HTTP/HTTPS if ufw is active
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp || true
  ufw allow 443/tcp || true
fi

echo "===== HTTP staging ready ====="
echo "WEB_ROOT=$WEB_ROOT"
curl -sI -H "Host: ${DOMAIN_WWW}" http://127.0.0.1/ | head -n 15 || true

# Try SSL only if public DNS already points here
RESOLVED=$(getent ahostsv4 "$DOMAIN_WWW" 2>/dev/null | awk '{print $1; exit}' || true)
MY_IP=$(curl -4 -s --max-time 10 ifconfig.me || true)
echo "DNS($DOMAIN_WWW)=$RESOLVED  VPS_IP=$MY_IP"

if [[ -n "$RESOLVED" && -n "$MY_IP" && "$RESOLVED" == "$MY_IP" ]]; then
  certbot --nginx -d "$DOMAIN_ROOT" -d "$DOMAIN_WWW" --non-interactive --agree-tos \
    --register-unsafely-without-email --redirect || {
      echo "WARN: certbot failed — retry after DNS propagates"
    }
else
  echo "SKIP SSL: DNS not pointing to this VPS yet."
  echo "Set A records for @ and www to $MY_IP then run:"
  echo "  certbot --nginx -d $DOMAIN_ROOT -d $DOMAIN_WWW --non-interactive --agree-tos --register-unsafely-without-email --redirect"
fi

echo "--- public desk API via nginx ---"
curl -fsS -o /tmp/desk_snap.json -w "HTTP %{http_code}\n" \
  "https://${DOMAIN_WWW}/api/v1/desk/snapshot" || \
curl -fsS -o /tmp/desk_snap.json -w "HTTP %{http_code}\n" \
  -H "Host: ${DOMAIN_WWW}" "http://127.0.0.1/api/v1/desk/snapshot" || true
if [[ -f /tmp/desk_snap.json ]]; then
  python3 -c 'import json;d=json.load(open("/tmp/desk_snap.json"));print("closed",d.get("portfolio",{}).get("closedTrades"),"trades",len(d.get("trades",[])))' || true
fi

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) dashboard deploy done ====="
