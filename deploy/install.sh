#!/usr/bin/env bash
# Run on the droplet as root, from inside the uploaded project folder:  bash deploy/install.sh
set -euo pipefail

APP_DIR=/opt/wa-assistant
DOMAIN="${DOMAIN:-bot.example.com}"
# Extra names that should show the website too, comma separated, e.g. SITE_DOMAINS="example.com,www.example.com"
SITE_DOMAINS="${SITE_DOMAINS:-}"

if [ "$(id -u)" -ne 0 ]; then echo "Run as root (you are root on a fresh droplet)."; exit 1; fi
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -f "$SRC/.env" ] && [ ! -f "$APP_DIR/.env" ]; then echo "Missing $SRC/.env. Copy .env.example to .env and fill it in first."; exit 1; fi

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip rsync ufw unattended-upgrades fail2ban
systemctl enable --now fail2ban >/dev/null 2>&1 || true  # blocks repeated bad SSH logins
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true  # automatic security updates

id wabot >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin wabot

echo "==> Copying the app to $APP_DIR"
mkdir -p "$APP_DIR"
rsync -a --delete --exclude venv --exclude data --exclude .env --exclude __pycache__ --exclude tests --exclude relay --exclude tools --exclude docs --exclude "*.md" --exclude requirements-dev.txt "$SRC/" "$APP_DIR/"
# Never overwrite the live .env: it holds the owner's real settings (bank details, keys). Copy only on a first install.
if [ ! -f "$APP_DIR/.env" ]; then cp "$SRC/.env" "$APP_DIR/.env"; else echo "==> Keeping the existing $APP_DIR/.env"; fi
mkdir -p "$APP_DIR/data/backups"
chown -R wabot:wabot "$APP_DIR"
chmod 700 "$APP_DIR/data"
chmod 600 "$APP_DIR/.env"

echo "==> Python environment"
[ -d "$APP_DIR/venv" ] || sudo -u wabot python3 -m venv "$APP_DIR/venv"
sudo -u wabot "$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
sudo -u wabot "$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "==> Service"
cp "$APP_DIR/deploy/wa-assistant.service" /etc/systemd/system/wa-assistant.service
systemctl daemon-reload
systemctl enable wa-assistant >/dev/null
systemctl restart wa-assistant

echo "==> Caddy (HTTPS) for $DOMAIN"
if ! command -v caddy >/dev/null 2>&1; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi
NAMES="$DOMAIN"
[ -n "$SITE_DOMAINS" ] && NAMES="$DOMAIN, ${SITE_DOMAINS//,/, }"
cat > /etc/caddy/Caddyfile <<CADDY
$NAMES {
    encode zstd gzip
    header -Server
    reverse_proxy 127.0.0.1:8000
}
CADDY
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

echo "==> Firewall"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

sleep 3
echo
systemctl --no-pager --lines=0 status wa-assistant || true
echo "Health check:"
curl -fsS http://127.0.0.1:8000/health || echo "NOT HEALTHY: run  journalctl -u wa-assistant -n 50 --no-pager"
echo
echo "Done. Open https://$DOMAIN/health in your browser."
