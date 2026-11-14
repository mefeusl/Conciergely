# Conciergely: project notes for Claude Code

A Claude-powered Turkish/English executive assistant that clients talk to on WhatsApp. The owner sells it: Basic 1,000 TL setup + 1,000 TL a month, Plus 2,000 TL setup + 2,000 TL a month,
the first 2 paid months at half price (all in `.env`, see Billing). Basic is the everyday assistant; Plus adds the extras. Every new client gets a free 7-day Plus trial.
Read README.md for setup and the full feature list. This file is the context a new session needs.

## Stack and layout
- Python 3.13, FastAPI, SQLite (WAL), a durable job queue (`app/jobs.py`, `app/worker.py`), APScheduler (`app/scheduler.py`),
  Anthropic tool-use loop (`app/assistant.py`, tools in `app/tools.py` and `app/trading_tools.py`), OpenAI Whisper and TTS,
  Open-Meteo weather, SerpApi flights, Yahoo prices (unofficial), Meta WhatsApp Cloud API (`app/wa_meta.py`).
- `app/` code, `deploy/install.sh` server install,
  `tests/` pytest, `docs/` privacy text draft and `security-audit.md` (the launch audit), `tools/build_guide.py` builds the Turkish customer guide PDF.
- Runs on one DigitalOcean droplet: `/opt/wa-assistant`, systemd unit `wa-assistant`, Caddy HTTPS at
  https://bot.example.com. The bot, `/admin` and the landing page are all served by the one service.

## Commands
- Tests: `pip install -r requirements.txt -r requirements-dev.txt && python -m pytest -q` (about 800 tests, about 40 s).
  FastAPI may be missing locally; routes were only smoke-imported with stubs, so test routes on a real server.
- Deploy: upload the zip, unzip, copy the old `.env` in, run `bash deploy/install.sh` (optional `SITE_DOMAINS=a.com,www.a.com`
  for extra site names). `.env` is never in the zip.
- Guide PDF: `python tools/build_guide.py`.

## Plans and gating (`app/plans.py`)
- `PLUS_ONLY` in `.env` (default `trading,briefing,mail`) lists the feature groups reserved for Plus. Flights are in Basic.
- `plan_of(client)` treats an active trial as Plus. Trial starts at the client's first message (`app/trial.py`); a heads-up goes
  out with 1 day left and an ended notice after. `plans.visible_tools` hides gated tools from the model.
- The Plus extras are named for customers: spoken morning brief, **Stock scenarios** (Turkish: Hisse senaryoları; the code and
  playbook still call it `scenario` / time machine, never show that name to customers), and mail.
- Scenario output (`app/timemachine.py`, `trading_tools.scenario`): ranges at 1/3/12 months, chances of rises, falls and dips,
  a past-year profile (worst fall, biggest days, moves vs the index) and headlines shown BESIDE the numbers (never an input).
  `watchlist_scan` ranks the client's followed stocks (year high/low distance, unusual week, headlines for movers).
  Headlines come from Google News RSS (last 2 days only), so big past moves are not matched to their news. Plain wording rule:
  no entry/exit/stop/target unless the client uses them (playbooks `time-machine`, `watchlist-scan`).

- Price-history quality (`app/quality.py`): Yahoo often misses Turkish bonus issues (bedelsiz), faking a crash. A one-day move over
  12% on `.IS` (45% elsewhere) marks the history unreliable; tools then use only the bars after the last jump and return
  `history_unreliable` (or refuse if under 60 clean bars). Applies to price_levels, scenario, backtest_setup, watchlist_scan and the
  nightly scan. If Yahoo's own split record (`bars.corporate_actions`) explains a jump (date within 4 days, size within 15% of the ratio),
  `quality.repair` rescales the old prices and the result says `repaired`. The KAP probe showed kap.org.tr answers from the
  droplet, but reading KAP directly is NOT built. The system prompt says how to word it and to take
  today's move from get_quote only.

## Mail (`app/mail.py`, `app/mailchat.py`, `app/vault.py`)
- Plain IMAP/SMTP with the client's own app password, no OAuth. Passwords are sealed with AES-256-GCM, a per-secret key wrapped
  by `MAIL_MASTER_KEY` (rotation with `MAIL_MASTER_KEY_OLD`). Reading is read-only. Sending needs the client to say "gönder"/"send"
  after a draft; a send claim prevents double sends. No delete. Audit log stores no contents.
- Chat commands: `mail bağla`, `mail geçmişi`, `mail bağlantısını kes`.
- DigitalOcean blocks outbound ports 465/587, so sending goes through the **mail relay** (`relay/relay.py`, stdlib only, runs on a
  Vultr server behind Caddy; install with `relay/install.sh`, docs in `relay/README.md`). The bot uses it when `MAIL_RELAY_URL`
  (https) and `MAIL_RELAY_KEY` are set (`mail.smtp_verify` / `mail.smtp_send`, errors are `mail.RelayError`); otherwise it sends
  directly. Each draft has a fixed Message-ID so retries cannot duplicate. Reading (IMAP) is direct. Admin Settings shows the route.
  Tests: `tests/test_relay.py` and the relay section of `tests/test_vault_mail.py`.

## Billing by bank transfer (`app/billing.py`, `app/receipt.py`)
- OFF until `BILLING_ON=1` and `BANK_IBAN`/`BANK_HOLDER` are set (a deploy must never freeze anyone by surprise).
- A client has `paid_until`; before the first payment the due date is the end of the free trial. Prices: `month_price`/`amount_due`
  (half price for the first `HALF_PRICE_MONTHS` paid months, setup once, `setup_paid_tl` remembers it so Basic -> Plus pays the difference).
- `billing.tick` (every 30 min): reminders at -5 days (only after a first payment; trial clients get the trial notices), on the due day, +2 days;
  freeze at +4 days (`frozen_reason='unpaid'`, never during `grace_until`). A frozen-unpaid client gets "pay first" with the details
  (`intake.py`, once per hour) and can still send a receipt. Abuse freezes keep their own notice.
- Receipts: a photo/PDF from a client who owes (or says dekont/ödeme/upgrade) is read by Claude (`receipt._claude_reader`, replaceable in tests)
  and AUTO-APPLIED only if IBAN matches (masked digits allowed if they agree), amount equals what they owe for their plan or the Plus amount
  (that upgrades them), the description has their code `CG-12345`, the date is within 5 days, and the receipt is new (field fingerprint and image hash).
  Anything doubtful or unreadable -> review list in /admin + `GRACE_DAYS` extra time + owner alert. Wrong account -> refused.
- /admin: Mark as paid (plan, months, optional amount), Family and friends (`free`: never billed), receipts to check, Payments box in Settings.
- Reminders after 24 h of silence use the one approved template (`META_TEMPLATE_NAME`, text in {{1}}). Without it they only reach clients who wrote in the last 23 h.
- The site shows the prices and a how-to-pay box; the IBAN is NOT on the site, the bot sends it with the client's personal code.
- Tests: `tests/test_billing.py`, `tests/test_receipt.py`, `tests/test_billing_flow.py`.

## Model routing (`app/routing.py`) and voice setup (`app/voicechat.py`, `app/talk.py`)
- Haiku (`MODEL_FAST`) answers almost everything. The main model (`CLAUDE_MODEL`) handles charts, scans (incl. 30-day), backtests, scenarios,
  risk, long (>400 chars) or analytical requests and PDFs. If Haiku calls a heavy tool (`routing.HEAVY_TOOLS`), `run_loop` moves the rest of that reply
  to the main model. Each reply logs "reply timing: started on <model>, N s".
- Voice setup is OFF until `VOICE_SETUP=1` (+ `OPENAI_API_KEY`, https `PUBLIC_BASE_URL`). The setup greeting then includes a personal link `/talk/<token>`
  (hash stored in `voice_links`, expires after `VOICE_LINK_HOURS`, `VOICE_MAX_TURNS` per link). The page records audio, Whisper transcribes,
  the SAME `onboarding.handle` runs, the reply is spoken with OpenAI TTS (mp3). Typed fallback. Not a regular feature; may become an update.

## Speech to text and a new day
- `voice.transcribe` uses `TRANSCRIBE_MODEL` (gpt-4o-transcribe) with a vocabulary hint (client name, followed stocks); a refused model falls back to whisper-1 until restart.
  `TRANSCRIBE_LANG_HINT=1` also sends the client's language (off by default: clients mix Turkish and English).
- `assistant.reply` tells the model how long ago the client's previous message was (`db.hours_since_last_message`, note added at 6 h+) so a greeting the next day is a fresh start,
  and the style rule forbids opening with a correction of an earlier message.

## Reminders privacy
- Reminder texts are stored sealed (`enc:` + `vault.seal`, owner `reminder:<phone>`) whenever `MAIL_MASTER_KEY` is set; `db.add_reminder` seals, `db.list_pending_reminders`/`due_reminders`
  open them, and `db.init_db` seals old plain ones on every start. A text that cannot be opened becomes "(reminder text unavailable)" and never stops the scheduler.
  The admin never shows reminders or chat messages, only the profile (memory), stocks and settings. Tests: `tests/test_reminder_crypto.py`.

## Calls (`app/calls.py`)
- OFF by default (it needs an account with a telephony provider). Needs the master switch in admin Settings, the per-client
  switch and Plus. Providers: `test` (never dials) and `webhook`. Rides/taxi booking was removed on purpose; the bot never books or buys.

## Admin, site, ops
- Admin: `app/admin.py` routes, `app/adminlogic.py` logic, `app/pages.py` Jinja templates (flat colours, dark rail, no JS, strict CSP).
  Settings page holds contact details, calling switch and alert number. Shows estimated cost per client (`app/costs.py`, `COST_*` rates in `.env`).
- Landing page: `app/sitepage.py` fills `__SITE_JSON__`, `__YEAR__` and `__CANONICAL__` from `.env` (`CONTACT_EMAIL`, `CONTACT_WHATSAPP`, `CONTACT_PHONE`, `CONTACT_WEBSITE`). No Instagram on purpose.
  Pages are `site/index.html`, `site/privacy.html` and `site/terms.html` (routes `/privacy` `/gizlilik`, `/terms` `/sartlar`; `__CONTACT__` filled from `.env` by `sitepage.privacy`) and `site/404.html` (no inline `style=` or `on*=` handlers: the CSP allows only the page's own script/style by hash, computed in `sitepage.csp_for`). Only `site/static/` is served as plain files (`/static-site/`). Routes: `/`, `/robots.txt`, `/sitemap.xml`, `/favicon.ico`, custom 404 for browsers. Headers: `sitepage.security_headers`.
  Mobile menu, skip link, footer links, tap-to-call/mail, status/empty messages are in `index.html`; `tests/test_site.py` guards them.
  Design follows the Apple DESIGN.md style: white, parchment and dark tiles, one blue (#0066cc), no gradients, animated chat phone in the hero.
- Ops: `/health` returns 503 if the queue is stuck; `app/ops.py` records problems; nightly SQLite backup (`app/backup.py`, 14 kept).
- Upgrade requests: a client writing `PLUS` creates a pending request in the admin.
- Opt-out: a client writing `dur` / `stop` turns off the briefing and news alerts (`i18n.STOP`, `conversation._decide`). The bot sends no promotions.
- Security guards: `tests/test_security_audit.py` (no secrets in files, admin routes guarded, SQL parameterised, service sandboxed) and `tests/test_isolation.py` (clients cannot reach each other's records). Twilio webhooks are refused unless `PUBLIC_BASE_URL` is set.
- The server install does not copy `tests`, `relay`, `tools`, `docs` or markdown (`deploy/install.sh`). The relay is installed on the Vultr server from the zip.

## Lite plan and referrals (`app/plans.py`, `app/referrals.py`)
- Plans are `lite` < `basic` < `plus` (`plans.PLANS`, `plans.upward(plan)` = that plan and dearer ones). Lite: 500 TL/month (`PRICE_LITE_MONTH`), setup `SETUP_LITE` (default 0), plain chat only:
  no tools (`visible_tools` returns []; `assistant._create_once` sends no tools or web search), a short system prompt, onboarding asks 3 questions, voice notes/photos/files get a canned message, no Plus trial,
  no recap/heads-up. Receipts accept the client's own plan price or any dearer plan (`plans.upward`). Admin plan selects include Lite. Lite clients have no due date until first payment: mark them paid in /admin.
- Strangers (`intake`): a number that is not a client gets ONE canned TR+EN reply per 24 h (`referrals.stranger_reply`, `unknown_contacts` keyed by an HMAC of the number, global brake 120/h; payload `direct` skips the 24 h-window check).
  A message with `REF-1234` from a number that was never a client (`ever_clients` hash survives erasure) signs it up as Basic with the Plus trial, linked to the inviter; no self-referral; `REF_SIGNUPS_PER_DAY` cap.
- Rewards: when a referred friend's FIRST real payment (amount > 0) is applied (`billing.apply_payment` -> `referrals.on_payment`) the inviter gets a credit row (`referral_credits`, percent from `REF_LADDER` by how many friends paid).
  Credits stack up to 100 % on the next month's price only (setup excluded), carry over, and are consumed when the discounted amount is paid. The friend gets `REF_FRIEND_PCT` % off the first month. `billing.amount_due` includes
  the discount, so quotes and receipt matching are discount-aware. `davet` / `invite` shows code, wa.me link, level, pending discount. Mentions of the programme: end of setup, payment confirmed, trial ending (once each, never after `dur`). Admin: /admin/referrals.

## Quality, daily life and Turkey extras
- `app/speech.py` turns briefing scripts into spoken words (EUR/TRY, BIST 100, %, decimals) in ONE language; `tts.speak(text, lang=)` adds `TTS_INSTRUCTIONS_TR/EN` for gpt-4o voices; briefing voice reads `BRIEF_NEWS_VOICE` headlines, the text carries `BRIEF_NEWS_TEXT`.
- `app/info_tools.py`: `news_headlines` (6-8 with sources, `offset` for 'more'), `football` (Süper Lig via TheSportsDB, derby lookup, falls back to web search with 'can't confirm'), `track_parcel` (carrier link only), `prayer_times` (Aladhan, Diyanet method 13).
  System-prompt rules: football by default, derbi = Galatasaray-Fenerbahçe, no US sports, bills/subscriptions as monthly repeating reminders, appointments, currency/gold alerts (`markets.ALIASES`).
- Repeating reminders: `set_reminder(repeat=monthly|yearly)`; stored as `monthly:DAY`; `scheduler._schedule_next` creates the next one (and heads-up/follow-up) when one fires.
- `app/mydata.py`: `verilerim` / `my data` (numbered memory), `sil 3`, `değiştir 3: ...`. `app/lifecycle.py`: first-day message (job kind `wow`, `WOW_ON`), Sunday 19:00 recap and 20:00 heads-up when 2+ reminders tomorrow (`recap_on`, 'özet kapat/aç', `dur` turns off).
- Forwarded messages (`Incoming.forwarded`) and photos/PDFs offer a reminder but only set it after a yes. `conversation._working_notice` sends 'on it' after `WORKING_AFTER_SECONDS`.
- `app/sharing.py`: `set_reminder(share_with=phone)`: only for existing clients, they must write `kabul`/`accept`; copies are sent when the reminder fires; `paylaşımı bitir` ends it.
- Untested against live services (tests use fakes): TheSportsDB, Aladhan, the new TTS instructions, Meta `context.forwarded`.

## Conventions to keep
- Turkish and English strings live in `app/i18n.py` (setup questions are `QUESTIONS`, Plus adds two more). Keep both languages.
- No gradients in any UI. No second accent colour on the site.
- The bot gives information and stress tests, never "buy/sell/hold" advice and never promises a price. Keep the "not a forecast" wording; never call stock scenarios a "predictor".
- Add a test for every change; the suite must pass before a zip is built.

## Not done / decided later
- Real calling provider (two-way voice), Apple Calendar (CalDAV), calendar by private link, meeting notes from voice notes,
  Meta Ads, location triggers, payment links, Postgres/Redis for scale.
- Before real clients: Meta business verification and message templates, a dedicated WhatsApp number, spending limits on provider
  dashboards, UptimeRobot on `/health`, off-server backups, a live test with friends, a lawyer review of `docs/gizlilik-metni.md`.
- Untested against real services: Anthropic, Meta, OpenAI voice, SerpApi, Yahoo, real mailboxes. Tests use fakes.
