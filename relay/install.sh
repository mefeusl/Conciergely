#!/usr/bin/env bash
# Run on the SECOND server (Vultr) as root, from inside the project folder:
#   BOT_IP=203.0.113.10 DOMAIN=send.example.com bash relay/install.sh
# BOT_IP  = the public IP of the server that runs the bot (only it may use the relay)
# DOMAIN  = the name whose DNS A record points to THIS server
set -euo pipefail

DOMAIN="${DOMAIN:-send.example.com}"
BOT_IP="${BOT_IP:-}"
APP_DIR=/opt/mail-relay
ENV_FILE=/etc/mail-relay.env

if [ "$(id -u)" -ne 0 ]; then echo "Run as root."; exit 1; fi
if ! [[ "$BOT_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
  echo "Set BOT_IP to the bot server's IP, e.g.  BOT_IP=203.0.113.10 bash relay/install.sh"; exit 1
fi
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SRC/relay.py" ] || { echo "relay.py not found next to this script."; exit 1; }

echo "==> Checking that $DOMAIN points here"
MYIP="$(curl -4 -s https://ifconfig.me || true)"
DNSIP="$(getent ahostsv4 "$DOMAIN" | awk '{print $1; exit}' || true)"
if [ -n "$MYIP" ] && [ "$MYIP" != "$DNSIP" ]; then
  echo "WARNING: $DOMAIN resolves to '${DNSIP:-nothing}' but this server is $MYIP."
  echo "         Fix the DNS A record first, or HTTPS will not start. Continuing in 10 seconds (Ctrl+C to stop)."
  sleep 10
fi

echo "==> Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 curl unattended-upgrades fail2ban
systemctl enable --now fail2ban >/dev/null 2>&1 || true

echo "==> The relay program"
id mailrelay >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin mailrelay
mkdir -p "$APP_DIR"
install -m 644 "$SRC/relay.py" "$APP_DIR/relay.py"

if [ ! -f "$ENV_FILE" ] || ! grep -q '^RELAY_KEY=' "$ENV_FILE"; then
  echo "RELAY_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(36))')" > "$ENV_FILE"
fi
chown root:mailrelay "$ENV_FILE"; chmod 640 "$ENV_FILE"

cat > /etc/systemd/system/mail-relay.service <<UNIT
[Unit]
Description=Mail relay
After=network-online.target
Wants=network-online.target

[Service]
User=mailrelay
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $APP_DIR/relay.py
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable mail-relay >/dev/null
systemctl restart mail-relay

echo "==> Caddy (HTTPS, only $BOT_IP is let in)"
if ! command -v caddy >/dev/null 2>&1; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi
cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN {
    @allowed remote_ip $BOT_IP
    handle @allowed {
        reverse_proxy 127.0.0.1:8081
    }
    handle {
        respond "Forbidden" 403
    }
}
CADDY
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

sleep 2
echo "==> Self-test"
systemctl is-active --quiet mail-relay && echo "relay program: running" || { echo "relay program: NOT running"; journalctl -u mail-relay -n 20 --no-pager; exit 1; }
curl -s http://127.0.0.1:8081/health && echo

KEY="$(grep '^RELAY_KEY=' "$ENV_FILE" | cut -d= -f2-)"
cat <<DONE

Done. Add these two lines to /opt/wa-assistant/.env on the BOT server, then: systemctl restart wa-assistant

MAIL_RELAY_URL=https://$DOMAIN
MAIL_RELAY_KEY=$KEY

Keep the key private. To rotate it: delete $ENV_FILE, run this script again, and update the bot's .env.
DONE
