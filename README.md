# PsyhoTestBot 🧠

A Telegram-based psychodiagnostic **screening** platform for clinicians.

A doctor creates a one-time test link in a Telegram bot. The patient completes the test on a mobile web page (it also works as a Telegram Mini App). The scored result is delivered back to the doctor in Telegram, where it can be viewed in the chat or downloaded as a PDF report.

> **Clinical notice.** PsyhoTestBot is an aid for qualified professionals. It is not a medical device, it does not diagnose, and its output must be interpreted by a qualified clinician. The scoring rules in this repository have **not** been validated against the original publications — see [Instruments and their provenance](#instruments-and-their-provenance).
>
> **Personal data.** The system processes health-related personal data (patient names and test answers). Before real use, make sure you have a legal basis or consent, a retention policy and access control that satisfy the rules that apply to you (for example GDPR or national personal-data law). This is not legal advice.

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Repository contents](#repository-contents)
- [Instruments and their provenance](#instruments-and-their-provenance)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Security model](#security-model)
- [Known limitations](#known-limitations)
- [Third-party material and licensing](#third-party-material-and-licensing)

## Features

- **Three instruments:** PCL-5, Mini-Mult (short MMPI) and Leonhard–Schmieschek accentuation inventory.
- **One-time session links with a QR code.** A link is valid for 90 days and is consumed when the patient submits the test.
- **Patient interface in Ukrainian,** a single static HTML page that works inside Telegram and in an ordinary mobile browser.
- **Server-side scoring** in an n8n Code node. Any score or severity sent by the browser is ignored.
- **Results delivered to the doctor's Telegram chat,** with an in-chat result view and an on-demand PDF report.
- **Doctor-scoped access:** a doctor can open only the results of sessions that doctor created.

## How it works

```
Doctor ──Telegram──► Bot (aiogram, long polling)
                       │ creates a session (UUID token) and a QR code
                       ▼
                  PostgreSQL ◄──────────────────────────┐
                       ▲                                │
Patient ──HTTPS──► Nginx ──► n8n workflow ──────────────┘
   (index.html)      │   GET  /webhook/session     returns the session (patient name, test, sex)
                     │   POST /webhook/psychotest  validates, claims the token, scores, saves
                     ▼
                    n8n ──POST /result + X-Webhook-Secret──► Bot ──► Doctor (message + PDF button)
```

1. The doctor sends `/newtest`, picks a test, enters the patient's full name (and the sex for Mini-Mult, which needs it for T-scores) and confirms. The bot stores a session and returns a link and a QR code.
2. The patient opens the link. The page calls `GET /webhook/session?token=…` and renders the test.
3. On submit the page calls `POST /webhook/psychotest`. The workflow validates the payload, atomically claims the token (`pending` → `processing`), scores the answers, stores the result and marks the token `completed`. If scoring or saving fails, the token is released back to `pending` and the patient can retry.
4. The workflow calls the bot's `POST /result` with a shared secret. The bot sends the doctor a message with buttons to view the result and to download the PDF.

Token lifecycle: `pending` → `processing` → `completed`. A token stuck in `processing` for more than 10 minutes (for example after an n8n restart) can be claimed again.

## Repository contents

| File | Purpose |
| --- | --- |
| `bot.py` | Telegram bot (aiogram 3) and the internal `/result` receiver |
| `report.py` | PDF report generation used by `bot.py` (must be present next to `bot.py`) |
| `index.html` | Patient-facing test page (static, no build step) |
| `workflow.json` | n8n workflow: session lookup, submission handling, scoring, notification |
| `schema.sql` | PostgreSQL schema; safe to run on an empty or an existing database |
| `.env.example` | Environment variables for the bot |
| `DEPLOYMENT.md` | Step-by-step production deployment guide |

## Instruments and their provenance

| Instrument | Items | Answers | What the workflow computes |
| --- | --- | --- | --- |
| PCL-5 | 20 | 0–4 | Total (0–80), clusters B/C/D/E, a provisional DSM-5 rule and a cut-off at 33 |
| Mini-Mult | 71 | yes / no | Validity scales L, F, K; eight clinical scales with K-correction; T-scores by sex |
| Leonhard–Schmieschek | 88 | yes / no | Ten scales, 0–24 each; accentuation at 20 and above; tendency at 15–19 |

Verification status of the implemented rules (taken from the comments in the workflow code):

- **PCL-5.** The cluster structure is the standard one. The provisional DSM-5 rule and the cut-off range (31–33) were implemented from memory and are marked "not verified" in the code. Check the item wording and the administration instructions against the official instrument and an authorised translation before clinical use.
- **Mini-Mult.** The key was cross-checked against one secondary public source (psychic.at.ua, after Zaitsev and Kozyulya), not against the original publication. Norms (M and σ by sex) come from the same source. T = 50 + 10·(X − M)/σ is the standard MMPI formula; the source page shows its formula as an image.
- **Leonhard–Schmieschek.** Item text comes from kipt.sumdu.edu.ua. Where the sources disagree, the key follows the content of the items (Pedantry items 36/39/83; Excitability multiplier ×3). Published thresholds differ between sources and are configurable at the top of the scoring code (`SCH_ACCENT_MIN`, `SCH_TENDENCY_MIN`).

## Quick start

A full production setup (Docker stack, TLS, firewall, systemd service, n8n configuration, verification and backups) is described in **[DEPLOYMENT.md](DEPLOYMENT.md)**. In short:

1. Point a DNS name at a Linux server and install Docker, Nginx, Certbot and Python 3.
2. Start PostgreSQL and n8n with the Docker Compose file from the guide, create the database role and apply `schema.sql`.
3. Create a bot with @BotFather, fill in `.env` (see below) and run `bot.py` as a systemd service.
4. Publish `index.html` and proxy only the two webhook paths through Nginx with TLS.
5. In n8n: import `workflow.json`, create the PostgreSQL and Header Auth credentials, and publish the workflow.
6. Send `/start` to your bot, create a test for yourself and complete it.

## Configuration

The bot reads its settings from a `.env` file in its working directory (copy `.env.example`).

| Variable | Required | Description |
| --- | --- | --- |
| `BOT_TOKEN` | yes | Bot token from @BotFather |
| `DATABASE_URL` | yes | PostgreSQL connection string used by the bot |
| `MINI_APP_URL` | yes | Public HTTPS URL of the patient page; session links are built from it |
| `WEBHOOK_HOST` | no (default `127.0.0.1`) | Address the `/result` receiver binds to. It must be reachable from the n8n container, for example the Docker bridge gateway |
| `WEBHOOK_PORT` | no (default `8080`) | Port of the `/result` receiver. The reference deployment uses `8082` |
| `WEBHOOK_SECRET` | yes | Shared secret expected in the `X-Webhook-Secret` header. The bot refuses to start without it |
| `ADMIN_TG_ID` | no | Telegram ID that receives error notifications from the bot |

The `/result` URL and the secret are also configured inside n8n (the **HTTP: Notify Doctor** node and its Header Auth credential). The workflow file expects the bot at `http://172.19.0.1:8082/result`; change the URL in that node if your Docker network differs.

## Security model

- Only two paths are exposed to the internet: `GET /webhook/session` (with a strictly formatted `token` parameter) and `POST /webhook/psychotest`. The n8n editor, PostgreSQL and the bot's `/result` receiver stay on loopback or the internal Docker network.
- The one-time token (a random UUID) is the patient's only credential. It is single-use and expires after 90 days.
- All SQL in the workflow and in the bot uses bound parameters.
- Scoring happens on the server. Answers are validated strictly (count and allowed values); invalid input is rejected and the token is released.
- The bot's `/result` receiver authenticates every request with a constant-time comparison of `X-Webhook-Secret`.
- Everything shown to the doctor is HTML-escaped, and the patient name shown to the doctor comes from the server-side session, not from the request.
- Results and PDF reports are served only to the doctor who owns the session.

## Known limitations

- **Open doctor registration.** Any Telegram user who writes to the bot is registered as a doctor and can create sessions. Doctors cannot see each other's data, but access should be restricted to an allow-list before the bot is made public.
- **QR codes are generated by a third-party service** (`api.qrserver.com`), so the one-time link leaves your server. Generate QR codes locally if that matters to you.
- **No automatic resend.** If the bot is unreachable when a result arrives, the result stays in the database with status `scored` and the doctor is not notified.
- **Telegram `initData` is not validated.** The page is usable outside Telegram by design; the token is the only credential.
- **n8n keeps execution history** that can contain patient names and answers. Configure pruning (see the deployment guide).
- **Instrument rules are unverified** — see [Instruments and their provenance](#instruments-and-their-provenance).
- **Single-instance design:** the bot uses in-memory FSM storage and long polling.

## Third-party material and licensing

The instruments implemented here (items, keys and norms) were taken from the sources named above and remain the property of their respective authors. Their licences have not been verified; check the terms before redistributing this material.

This repository is public for transparency and review. **All rights reserved:** no licence to use, copy, modify or distribute the code is granted unless the maintainer adds a `LICENSE` file.
