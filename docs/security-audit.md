# Launch audit: security, privacy and readiness (StefBuilds checklist applied to Conciergely)

Done 28 Oct 2026 on the code in this build. It is a **code review plus automated tests plus a real browser pass on the
landing page** (desktop and phone widths, keyboard). Nothing was tested on the live server, with a real WhatsApp number,
in the Instagram app, or with a real purchase. Where that decides the answer, the status says so.
Nothing here is legal or tax advice; the lawyer/accountant items are listed at the end.

**What Conciergely is:** a WhatsApp assistant. There is no sign-up form, no login, no checkout and no database reachable from a
browser. A client signs up by sending one WhatsApp message; you add them in `/admin` and bill them yourself. So several
website-journey checks (2.3, 2.4, 3.x, 5.4) do not apply in the form the checklist expects. They are marked that way,
not passed.

## Short assessment

A new visitor can understand the offer and reach you (WhatsApp, email, phone) from the page, on a phone and on a desktop. No
secret, key or client data is in anything a browser can download, and one client cannot reach another's records.
The main gaps are not code: **no published privacy page, prices not shown, tax and promotional-message rules unconfirmed,
and nothing has been checked on the live server or in Instagram's browser.**

## All 30 checks

Status words: PASS, FAIL, NOT TESTED, NEEDS CONFIRMATION. "In this build" means fixed here and not yet on your server.

### Part 1: the five clicks (website journey)

| ID | Status | Evidence | Impact | Next action |
|---|---|---|---|---|
| 1.1 What it does | PASS | Heading "Your executive assistant lives in WhatsApp." plus a one-line list of jobs (rendered and read) | Clear | A friend test (5 s) is still worth doing |
| 1.2 Who it is for | FAIL | The page never says who the assistant is for (executives? founders? families?) | Visitors cannot tell if it is for them | Add one audience line under the heading; you choose the audience |
| 1.3 Example of the result | PASS | Animated chat in the hero shows a real flight and reminder exchange, in both languages (observed in browser) | Shows the product | None |
| 1.4 Offer matches the link | NOT TESTED | No ad or post link was supplied | n/a | Point each ad/post at `/` or an anchor such as `/#plans` |
| 1.5 Main button easy to tap | PASS | "Start your free trial" is the one filled button; 44 px high; checked at 320, 375, 768, 1280 px, no overflow | Clear | None |
| 2.1 Button says what happens next | NEEDS CONFIRMATION | The button scrolls to the contact list; WhatsApp then opens with a prefilled message and a status line says so (observed) | Slight extra tap | Confirm on a phone that WhatsApp opens |
| 2.2 Trial explained | FAIL | Page says 7 days free, no card, and now shows the prices and a how-to-pay box (bank transfer) | Visitor cannot see what it costs afterwards | Decide whether to publish prices (see 5.1) |
| 2.3 Typed input kept on Back | PASS (n/a) | The site has no form to lose | none | none |
| 2.4 Tell people about payment first | PASS (n/a) | Nothing is paid for inside the site | none | none |
| 2.5 Works inside Instagram | NOT TESTED | Needs a phone with the Instagram app. The project deliberately has no Instagram link | Unknown | Open your real bio link in Instagram once; `wa.me` links usually open WhatsApp from there |
| 3.1 Short signup | PASS | Signup is one WhatsApp message; the setup questions (5 on Basic, 7 on Plus) are all optional ("geç" skips one, "kurulumu atla" skips all) (code + tests) | Low friction | Live test with a friend |
| 3.2 What to do after signup | NEEDS CONFIRMATION | The bot sends a "done" message listing what to try (code). Not seen on a live phone | Possible blank start | Live test |
| 3.3 Examples of what to type | NEEDS CONFIRMATION | The welcome lists example jobs; no sample prompts per feature | Some clients may not know what to ask | Add 3 sample sentences to the "done" message if the live test shows confusion |
| 3.4 Errors explained | NEEDS CONFIRMATION | Canned replies exist for voice, file and limit failures (tests). Live behaviour of the AI/WhatsApp failures not seen | Unknown | Live test with a bad file and a voice note |
| 4.1 App does what the page says | NOT TESTED | Needs the live Anthropic, Meta, OpenAI, SerpApi and Yahoo connections | Unknown | Run the live friend test listed in the README |
| 4.2 Check what it creates | NOT TESTED | Same; tests use fakes | Unknown | Read real answers for flights, scenarios, brief |
| 4.3 Downloads work | PASS (n/a) | No downloads; the voice briefing is a WhatsApp audio message (not tested live) | none | Play one briefing on a phone |
| 4.4 Waiting and failures | NEEDS CONFIRMATION | "Typing" indicator, queue retries, and a fallback message exist (tests). No cost is charged per failed try | Low | Live test |
| 5.1 Price and schedule shown | FAIL | The page shows "Message us for pricing" because no price is set in the page data | Visitors cannot compare | Owner decision: publish Basic and Plus prices (setup + monthly, in TL, KDV stated) |
| 5.2 Limits explained | NEEDS CONFIRMATION | Monthly caps exist per plan, shown to the client when reached; not listed on the page | Surprise caps | Decide whether to state them |
| 5.3 What paying adds | PASS | Plan cards and the comparison table list exactly what Plus adds | Clear | None |
| 5.4 One real purchase | NOT TESTED | There is no online checkout (billing is manual). No payment was made or attempted | n/a | If you later add checkout, you must do the first real purchase yourself |

### Part 2: security, privacy, accessibility, sales

| ID | Status | Evidence | Impact | Next action |
|---|---|---|---|---|
| C1 Secret keys out of public code | PASS | Keys live only in the server `.env` (mode 600, excluded from the zip and from git). The pages receive only the public contact details. Test scans every file for key patterns and for key names in `site/`; the admin page shows no secrets | None found | Replace any key you ever pasted in chat or email; set spending limits on the Anthropic, OpenAI, SerpApi and Meta dashboards (NEEDS CONFIRMATION: only you can see them) |
| C2 Users cannot see each other's data | NEEDS CONFIRMATION | The database is a private SQLite file, not reachable from a browser; there is no public data endpoint. Every client query is filtered by that client's phone; `tests/test_isolation.py` proves memory, history, reminders, trades, journal and watchlist cannot be read or changed across two clients. SQLite has no row-level security, so this is enforced in code | Strong in code | Live two-number test: the second number must not see the first's reminders |
| C3 Recording chat or activity | PASS (this build) | The site has no analytics, chat widget or session recorder and loads nothing from other sites (test + CSP). WhatsApp chats are stored (last 200 messages) and sent to Anthropic/OpenAI/Meta; the first message now says the chat is stored and how to turn off automatic messages | Disclosure was missing from the first message | Deploy; publish the privacy page (C7) |
| C4 Promotional texts only with consent | NEEDS CONFIRMATION | The bot sends no promotions. Proactive messages are reminders the client set, the briefing and news alerts (both off until the client turns them on), and trial notices. New: "dur" / "stop" turns off briefing and news alerts (tests). The trial notices end with a "stay on Plus" line | Low | Ask a lawyer whether the trial-ending notice counts as a commercial message under Turkish rules (İYS registration) and Meta's template rules |
| C5 Unsubscribe from marketing email | PASS (n/a) | The product sends no marketing email. Mail features only draft and send what the client says "send" to | None | If you ever email offers, add unsubscribe and sender details first |
| C6 Usable without a mouse | PASS | Browser pass: Tab reaches every link, button and the language switch in order; visible focus ring on all; skip link works and moves focus; mobile menu opens with Enter, Escape closes and returns focus; text contrast table meets 4.5:1 (tested); status messages use `aria-live`; no images; reduced-motion respected | Good | A human screen-reader pass (VoiceOver/NVDA) is still needed; this is WCAG 2.1 AA as a technical reference, not a compliance claim |
| C7 Explain data collection | PASS (this build), lawyer review pending | `/privacy` (EN/TR) now states what is stored, processors, retention and the user's choices, linked from the footer; contact comes from `.env`, no legal name or address is invented. Older note:  `docs/gizlilik-metni.md` is a draft with placeholders and no lawyer review, and the site has **no privacy page link**. The site itself sets no cookies and no tracking (only the language choice is kept in the browser) so there is no cookie banner to build | Clients' messages are processed with no published notice | Deploy, set `CONTACT_EMAIL` in `.env`, have a lawyer review `/privacy`, add owner's legal name and address if required |
| C8 Tax on sales | NEEDS CONFIRMATION | Billing is outside the app (you invoice clients). No tax logic exists | Unknown | Accountant questions below |

## Confirmed issues, ranked, with the smallest fix and how to verify

1. **No published privacy notice (C7, C3).** Fix: fill the placeholders in `docs/gizlilik-metni.md`, lawyer review, publish as a page and link it in the footer. Verify: the footer link opens the page; the controller name and email are yours.
2. **Twilio messages were accepted unsigned when `PUBLIC_BASE_URL` was empty.** Fixed in this build: they are now refused. Only matters if you switch from Meta to Twilio. Verify: `config.problems()` warns; unit test `test_unsigned_twilio_messages_are_refused`.
3. **Full phone numbers could appear in the server log** through the upgrade-request event. Fixed: logs show `...1234`. Verify: `journalctl -u wa-assistant | grep "asked for Plus"` shows no full number.
4. **No opt-out word for automatic messages (C4).** Fixed: "dur" / "stop" / "bildirimleri kapat" turn off briefing and news alerts; reminders the client set stay. Verify: send "dur" from a test number, then ask for the briefing setting.
5. **Public pages had no CSP, HSTS, Permissions-Policy.** Fixed: the landing page and 404 page get a CSP that allows only the page's own script and style by hash, plus HSTS (6 months, no subdomains, no preload) over HTTPS, Permissions-Policy, frame blocking. `/admin` keeps its stricter set and is marked noindex. Verify after deploy: `curl -sI https://bot.example.com/ | grep -i -E "content-security|strict-transport|permissions"`.
6. **Dev/support folders were copied to the server** (tests, relay, tools, docs, markdown, dev requirements). Fixed in `deploy/install.sh`. Verify: `ls /opt/wa-assistant` has no `tests`, `relay`, `tools`, `docs`.
7. **Service sandbox**: added private devices, kernel and cgroup protection, no capabilities, restricted address families. Verify: `systemctl status wa-assistant` shows active after the install; `systemd-analyze security wa-assistant` score.
8. Page content: no audience line (1.2), no price (2.2, 5.1). Owner decisions; not changed.

## Database and storage: what protects the data

- One SQLite file and a memory folder under `/opt/wa-assistant/data` (directory mode 700, files mode 600 by the service `UMask=0077`), owned by the service user. Not under the web root; the only folder served as files is `site/static/` (the favicon). A test fails if the page files become downloadable raw.
- Mail logins are sealed with AES-256-GCM per client; the master key lives only in `.env`, not in the database or backups.
- Admin: HTTP Basic over HTTPS, 15-minute lockout after 10 failed tries per address, CSRF token and origin check on every POST (tests make sure no admin route skips the login or the token).
- SQL: every query is parameterised. Six places build SQL text from fixed names or a whitelist; a test fails if a new one appears without review.
- Not done / residual: the database file is not encrypted at rest (rely on the host disk); backups are on the same server for 14 days (**add an off-server copy**); dependencies are not pinned to exact versions; SSH hardening (keys only, no root password) was not checked, confirm it on the droplet.

## Not checked, and questions that need you or a professional

- Not checked: the live server, real WhatsApp/Meta, the Instagram in-app browser, a screen reader, real purchases (none exist), provider spending limits.
- For a lawyer: the privacy text (KVKK notice, data sent abroad to Anthropic, OpenAI, Meta, DigitalOcean); whether trial notices and any future offers are "commercial electronic messages" needing consent and İYS registration; WhatsApp message-template rules.
- For an accountant: who invoices (you or a company), VAT (KDV) on setup and monthly fees, e-invoice or e-receipt duty, treatment for foreign clients, and whether anything here changes if you sell outside Turkey. Do not assume the payment method handles tax.

## Next three actions

1. Fill and publish the privacy page, and link it from the footer (C7).
2. Decide the audience line and whether to show prices, then deploy this build and run the live friend test (4.1 to 4.4, C2).
3. Set spending limits on every provider dashboard and add an off-server backup.
