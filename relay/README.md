# Mail relay

DigitalOcean blocks outgoing mail ports (465/587), so the bot cannot send through a client's Gmail or Outlook from there.
The relay is a small program on a second server (Vultr, where those ports are open). The bot hands it the finished message
over HTTPS; the relay logs in with the client's app password, sends that one message, and keeps nothing.

## What it does and does not do
- Stores nothing. No passwords, message text or recipient addresses are written to disk or logs (only "send ok/failed" and the mail provider's domain).
- Needs the shared key on every request, and Caddy only lets the bot server's IP in. The Vultr firewall should also allow 443 only from the bot IP.
- Only connects to ports 465 and 587 on public internet hosts. The "From" must equal the login, so it cannot forge senders.
- At most 30 sends per account per hour and 150 per hour overall (`RELAY_PER_ACCOUNT_HOUR`, `RELAY_PER_HOUR`).
- A message with the same Message-ID is sent once; the bot gives each draft a fixed ID so a retry cannot duplicate it.
- If the relay is down, the bot tells the client the send failed and keeps the draft; nothing is lost or sent twice.

## Install (on the Vultr server, as root)
1. DNS: an A record `send` (for example `send.example.com`) pointing to the Vultr IP.
2. Firewall: 22 and 80 open to anywhere, 443 only from the bot server's IP/32.
3. Copy the project there and run:
   ```
   cd wa-assistant
   BOT_IP=203.0.113.10 DOMAIN=send.example.com bash relay/install.sh
   ```
4. The script prints two lines. Add them to `/opt/wa-assistant/.env` on the bot server and run `systemctl restart wa-assistant`.
5. In /admin > Settings, "Sending route" should say `relay`. Then connect a test mailbox (`mail bağla`) and send a draft to yourself.

Checks: `systemctl status mail-relay`, `journalctl -u mail-relay -f`, `curl http://127.0.0.1:8081/health` on the relay server.
Tests: `tests/test_relay.py`. Update the program by re-running the install command (the key is kept).
