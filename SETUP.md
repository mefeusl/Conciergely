# Setup guide

This takes you from nothing to a working WhatsApp assistant. Budget a couple of hours the first time, mostly for the Meta account steps.

You will end up with: the bot running on your own server at `https://bot.your-domain.com`, connected to a WhatsApp number, with an admin page at `/admin`.

## 0. What to have ready

| Thing | Where | Notes |
|---|---|---|
| Anthropic API key | console.anthropic.com | Set a monthly spend limit |
| OpenAI API key (optional, for voice) | platform.openai.com | Set a spend limit too |
| Meta developer account with the WhatsApp product | developers.facebook.com | Needs a phone number that is not already registered on WhatsApp |
| A VPS running Ubuntu 22.04 or 24.04, 1 GB RAM | any provider | You need root SSH access |
| A domain name | any registrar | You will add one DNS record |

Never paste your keys into chat, tickets or screenshots, and never commit them to git.

## 1. Try it on your own computer (no WhatsApp needed)

You need Python 3.10 or newer (3.13 is what it is developed on).

```bash
git clone <this repo> conciergely && cd conciergely
python3 -m venv venv
. venv/bin/activate          # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env         # Windows: Copy-Item .env.example .env
```

Open `.env` and fill in at least `ANTHROPIC_API_KEY` and `ADMIN_PASSWORD` (invent a long password). Then:

```bash
DRY_RUN=1 uvicorn app.main:app --port 8000     # Windows PowerShell: $env:DRY_RUN="1"; uvicorn app.main:app --port 8000
```

- `http://127.0.0.1:8000/health` should answer `{"ok":true,...}`.
- `http://127.0.0.1:8000/admin` is the admin page (user `admin`).
- With `DRY_RUN=1`, replies are written to the log instead of being sent.

Run the tests to confirm your install: `pip install -r requirements-dev.txt && python -m pytest -q`.

## 2. Set up WhatsApp (Meta Cloud API)

1. In the Meta developer dashboard create an app of type **Business** and add the **WhatsApp** product.
2. Add a phone number (or use the free test number to start). Note its **Phone number ID**.
3. Create a **System user** with a **permanent access token** that has the `whatsapp_business_messaging` permission. Note the token.
4. Copy the **App secret** (App settings, Basic).
5. Invent a **verify token**: any long random string. You will enter it in two places.
6. Put these in `.env`:

```
WHATSAPP_PROVIDER=meta
META_ACCESS_TOKEN=...
META_PHONE_NUMBER_ID=...
META_APP_SECRET=...
META_VERIFY_TOKEN=...
PUBLIC_BASE_URL=https://bot.your-domain.com
```

### The message template (needed for proactive messages)

WhatsApp only lets a business send free-form messages within 24 hours after the person's last message. Reminders, the morning brief and billing notices that go out later must use an **approved template**.

1. In WhatsApp Manager create a **Utility** template with one variable, for example the body `{{1}}`.
2. Wait for Meta to approve it.
3. Set `META_TEMPLATE_NAME` to its name and `META_TEMPLATE_LANG` to its language code (for example `en` or `tr`).

Without a template, proactive messages only reach people who wrote to the bot in the last 24 hours, and the morning brief is skipped for everyone else.

## 3. Prepare the server

1. Create an Ubuntu VPS and point a DNS **A record** for `bot.your-domain.com` at its IP address (if you use Cloudflare, set it to "DNS only").
2. Copy the project to the server. From your computer:

```bash
scp -r conciergely root@YOUR_SERVER_IP:/root/conciergely
```

3. Copy your finished `.env` file to the same place (`/root/conciergely/.env`). It is never part of the repository.

## 4. Install

```bash
ssh root@YOUR_SERVER_IP
cd /root/conciergely
DOMAIN=bot.your-domain.com bash deploy/install.sh
```

The script installs Python and Caddy (automatic HTTPS), creates an unprivileged service user, installs the packages into a virtualenv, starts the `wa-assistant` systemd service, and opens only ports 22, 80 and 443.

It keeps an existing `/opt/wa-assistant/.env` untouched, so updating later does not overwrite your settings.

Check that it works:

```bash
systemctl status wa-assistant
curl https://bot.your-domain.com/health
journalctl -u wa-assistant -n 50 --no-pager
```

## 5. Connect the webhook

In the Meta dashboard, WhatsApp, Configuration:

- **Callback URL:** `https://bot.your-domain.com/webhook`
- **Verify token:** the same string as `META_VERIFY_TOKEN`
- Click **Verify and save**, then subscribe to the **messages** field.

If verification fails: the service is not running, the token differs from `.env`, or the domain does not point at the server yet.

## 6. Add yourself as the first client

1. Open `https://bot.your-domain.com/admin` (user `admin`, password from `ADMIN_PASSWORD`).
2. Add your own phone number as a client and choose a plan.
3. Send a message to the WhatsApp number. The bot runs a short setup (name, language, preferences) and is then ready.

Try: a voice note, "remind me tomorrow at 9 to call the bank", "every month on the 15th remind me to pay the electricity bill", and `my data` to see what it remembers.

## 7. Configure what you sell (optional)

All of this lives in `.env`, see the comments in `.env.example`.

- Prices: `PRICE_LITE_MONTH`, `PRICE_BASIC_MONTH`, `PRICE_PLUS_MONTH`, `SETUP_*`, and the first-months discount (`HALF_PRICE_*`)
- Trial length: `TRIAL_DAYS`
- Which features are Plus only: `PLUS_ONLY`
- Bank transfer billing: fill in `BANK_IBAN`, `BANK_HOLDER`, `BANK_NAME` first, then set `BILLING_ON=1`. Leave it off until the bank details are filled in, so no one is billed or frozen by surprise.
- Contact details used in messages: `CONTACT_EMAIL`, `CONTACT_WHATSAPP`, `CONTACT_PHONE`, `CONTACT_WEBSITE`
- Referral program: `REFERRALS_ON` and `REF_*`
- Optional integrations: `SERPAPI_KEY` (flights), `OPENAI_API_KEY` (voice), mail (`MAIL_MASTER_KEY`, set a long random value and never lose it)

## Updating

1. Upload the new code to the server (the `.env` is not part of it).
2. Run `bash deploy/install.sh` again from the project folder. Your live `.env` and the database in `/opt/wa-assistant/data` are kept.
3. Check `/health` and the logs.

## Backups

The app copies the database every night to `/opt/wa-assistant/data/backups` (14 kept). Copy that folder off the server too, because a backup on the same disk is not a backup:

```bash
scp -r root@YOUR_SERVER_IP:/opt/wa-assistant/data ./backup
```

## Troubleshooting

| Problem | Check |
|---|---|
| Meta says "Verify and save" failed | Does `curl https://bot.your-domain.com/health` work? Is the verify token identical in `.env` and in Meta? |
| The bot does not answer | `journalctl -u wa-assistant -n 100`, then the Overview page in `/admin` for queue problems |
| Reminders or the morning brief do not arrive | The 24-hour rule: set up the template (step 2). Also check the client's quiet hours |
| Voice notes fail | Is `OPENAI_API_KEY` set and funded? |
| "Billing" never starts | `BILLING_ON=1` and the three `BANK_*` values must be set |
| Settings vanished after an update | You overwrote `.env`; keep a private copy outside the repository |

## Before real customers

- Get your privacy text and terms reviewed by a lawyer (`docs/gizlilik-metni.md` is only a draft).
- Finish Meta business verification and use a dedicated WhatsApp number.
- Set spend limits on every provider dashboard.
- Add an uptime monitor on `/health` and keep an off-server backup.
- Test everything yourself, then with a few friends, before you invite anyone else.
