# Conciergely

**A Claude-powered personal assistant that lives in WhatsApp.** It speaks Turkish and English, understands voice notes, sets reminders that repeat, sends a spoken morning brief, and follows up on its own, so people don't have to open another app.

It is a complete, self-hosted product: the bot, a customer admin page, billing by bank transfer, a referral program, backups and tests. (The public marketing website is not part of this repository.) You run it on one small server.

> Built for the Turkish market (lira prices, bank transfer, BIST stocks, prayer times, Turkish parcel carriers), but everything works in English too and the Turkish parts are easy to swap.

## What it does

**Everyday assistant**
- Turkish and English, switching mid-conversation; text and voice notes (OpenAI Whisper in, optional spoken replies out)
- Reminders that follow up, including repeating ones ("every month on the 15th: pay the electricity bill") and reminders shared with another person only after they accept
- Notes, lists and a private per-client memory; the client can write `verilerim` ("my data") to see, edit or delete everything it remembers
- Photos and PDFs explained in plain words; forwarded messages turned into reminders
- Flight search with prices, weather, directions and nearby places from a shared location pin, parcel tracking links, prayer times, football results, headlines

**Proactive (the part chat apps don't do)**
- Spoken morning brief: day plan, weather, markets, headlines
- Sunday recap and a heads-up the evening before a busy day
- Price alerts and news alerts on a watchlist

**Plans (all prices are settings, not code)**
- `Lite`: plain chat only, no tools. `Basic`: everyday assistant. `Plus`: adds the morning brief, stock scenarios and email.
- A free trial of Plus for each new client (length is a setting)

**Plus extras**
- *Stock scenarios*: replays a stock's own history thousands of ways to show the range of outcomes and the chance a stop or target is hit first. A stress test, not a prediction.
- *Email*: read, summarize, and draft replies. Nothing is sent until the client says "send".

**Running a business on it**
- Billing by bank transfer: personal payment code, the bot reads the receipt photo/PDF and checks amount and IBAN, you confirm in the admin page
- Referral program with a discount ladder
- Admin page: clients, plans, costs per client, abuse protection, queue health, settings
- Nightly backups, a health endpoint, one-way hashed phone fingerprints, encrypted reminders and mail credentials

**Safe by design**
- It never books, buys or pays for anyone; it finds, plans, reminds and drafts
- Webhook signatures are verified, jobs are queued and retried, duplicate messages are ignored
- Per-client and global spend limits, abuse throttling, a kill switch

## How it fits together

```
Client's WhatsApp
      | message
Meta WhatsApp Cloud API --> https://your-domain/webhook   (Caddy provides HTTPS)
                              | verifies the signature, queues the message, answers 200 at once
                              v
                 SQLite queue (data/assistant.db)  <-- retries with backoff on failure
                              |
                      worker threads
                       |-- voice note?  --> Whisper --> text
                       |-- new client?  --> short setup --> memory
                       '-- Claude + tools (reminders, flights, weather, stocks, mail ...)
                              |
                       "send" job --> Meta Cloud API --> client's WhatsApp
```

Timers in the same process run reminders, alerts, the morning brief, billing reminders and backups. If the server restarts nothing is lost: unfinished jobs go back to the queue.

Stack: Python 3.13, FastAPI, SQLite (WAL), APScheduler, the Anthropic API (tool use), OpenAI Whisper/TTS, Caddy, systemd. The admin page is server-rendered, with no frontend framework.

## What you need to buy or sign up for

| Need | Required? | Roughly what it costs |
|---|---|---|
| **Anthropic API** account (Claude) | Yes | Pay as you go. A light user costs cents per month; set a spend limit in their console |
| **Meta developer account + WhatsApp Business Cloud API** | Yes | Free to set up; Meta charges per conversation/template message. A phone number that is not already on WhatsApp is needed |
| **A small server (VPS)**, 1 GB RAM is enough | Yes | About $5-12 per month (DigitalOcean, Hetzner, Vultr...) |
| **A domain name** | Yes | About $10-15 per year |
| **OpenAI API** (voice notes and spoken replies) | Optional, but voice is a main feature | Pay as you go, small |
| **SerpApi** (flight search) | Optional | Has a free tier |
| **A bank account** to receive transfers | Only if you sell it | Your own |
| Caddy (HTTPS) | Installed for you | Free |

Prices change, so check each provider's current pricing page before you commit. A realistic fixed cost to run it is the server plus the domain, and everything else scales with usage.

## Quick start

Full step-by-step instructions are in **[SETUP.md](SETUP.md)**. The short version:

```bash
git clone https://github.com/mefeusl/Conciergely && cd conciergely
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill in your own keys; never commit this file
DRY_RUN=1 uvicorn app.main:app --port 8000
# open http://127.0.0.1:8000/health and /admin
```

`DRY_RUN=1` logs replies instead of sending them, so you can try everything without a WhatsApp number.

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q        # about 940 tests, under a minute
```

Tests use fakes for every outside service, so they need no keys and no network. They do not exercise the live Meta, OpenAI or Anthropic APIs; test those on a real server with the dry-run switch off.

## Project layout

```
app/            the application (bot, tools, billing, admin, scheduler)
deploy/         install script and systemd unit for a fresh Ubuntu server
relay/          optional small mail relay, for servers that block outgoing mail ports
docs/           privacy text template (Turkish) and a security audit
tools/          builds the Turkish customer guide PDF
tests/          pytest suite
```

## Important notes before you use it with real people

- **Not legal advice.** `docs/gizlilik-metni.md` is a draft privacy text. Have a lawyer review them for your country (KVKK/GDPR) before you collect real customers' data.
- **Not financial advice.** The stock features show numbers and scenarios for study. They do not tell anyone what to buy, and the assistant is instructed to say so.
- **WhatsApp rules.** Free-form replies work only inside 24 hours of the person's last message; proactive messages outside that window need one approved Meta template (see SETUP.md). Respect opt-in and opt-out.
- **Secrets.** Keep `.env` out of git (it is already in `.gitignore`), give each provider key its own spend limit, and rotate anything that has ever been pasted anywhere public.
- **Prices and bank details** are all in `.env`. Nothing personal is hard-coded in this repository; the brand name "Conciergely" is only a default (`BOT_NAME`).

## License

GNU Affero General Public License v3.0 (AGPL-3.0), see [LICENSE](LICENSE). In short: you may use, change and run it, but if you run a modified version as a network service for other people you must make your changes available under the same license.
