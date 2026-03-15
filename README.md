# PsyhoTestBot 🧠

A Telegram-based psychodiagnostic platform for healthcare professionals. Doctors create test sessions via a Telegram bot, patients complete tests through a Telegram Mini App, and results are automatically delivered back to the doctor with PDF reports.

## Features

- **3 psychological tests** — PCL-5 (PTSD), Mini-Mult (shortened MMPI), Schmishek (character accentuations)
- **QR code generation** for sharing test links with patients
- **Telegram Mini App** — clean, mobile-optimized test UI
- **n8n workflow** — processes submissions, stores results, notifies doctors
- **PDF reports** — generated on-demand with interpretation and severity levels
- **Session management** — one-time tokens with 90-day expiry, doctor-scoped results

## Architecture

```
Telegram Bot (aiogram)
    │ creates session + token
    ▼
PostgreSQL
    │
    ▼
Mini App (HTML + Telegram WebApp SDK)
    │ POST /webhook/psychotest
    ▼
n8n Workflow
    │ POST /result
    ▼
Bot Webhook (port 8080)
    │ sends result + PDF button
    ▼
Telegram Message → Doctor
```

## Repository Structure

```
PsyhoTestBot/
├── bot/
│   └── bot.py                  # Telegram bot (aiogram 3.x)
├── miniapp/
│   └── index.html              # Telegram Mini App
├── n8n/
│   └── workflow.json           # n8n workflow (import this)
├── database/
│   └── schema.sql              # PostgreSQL schema
├── docs/
│   ├── DEPLOYMENT.md           # Full deployment guide
│   └── QUICKSTART.md           # 30-minute setup cheatsheet
├── .env.example                # Environment variables template
└── README.md
```

## Quick Start

### Requirements

- Ubuntu 20.04+ / Debian 11+
- Python 3.8+
- PostgreSQL 12+
- Nginx
- Docker (for n8n)
- A domain with HTTPS (recommended) or public IP

### 1. Clone & configure

```bash
git clone https://github.com/sergeykeba-cell/PsyhoTestBot.git
cd PsyhoTestBot
cp .env.example .env
# Edit .env with your values
```

### 2. Database

```bash
sudo -u postgres psql < database/schema.sql
```

### 3. Install bot dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install aiogram==3.7.0 asyncpg python-dotenv aiohttp reportlab
```

### 4. Deploy Mini App

```bash
sudo mkdir -p /var/www/psycho-miniapp
sudo cp miniapp/index.html /var/www/psycho-miniapp/index.html
# Configure Nginx — see docs/DEPLOYMENT.md
```

### 5. Import n8n workflow

```
Open n8n → Import → select n8n/workflow.json
Update PostgreSQL credentials in all DB nodes
Set "HTTP: Notify Doctor" URL to: http://localhost:8080/result
Activate the workflow
```

### 6. Start the bot

```bash
python bot/bot.py
# Or as a systemd service — see docs/DEPLOYMENT.md
```

## Environment Variables

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Telegram bot token from @BotFather |
| `DATABASE_URL` | PostgreSQL connection string |
| `MINI_APP_URL` | Public URL where miniapp is hosted |
| `WEBHOOK_PORT` | Port for n8n→bot result webhook (default: 8080) |
| `ADMIN_TG_ID` | Telegram ID for error notifications |

## Tests Included

| Test | Questions | Time |
|---|---|---|
| PCL-5 (PTSD Checklist) | 20 | 5–8 min |
| Mini-Mult (short MMPI) | 71 | 10–15 min |
| Schmishek (character accentuations) | 88 | 8–12 min |

## Tech Stack

- **Bot:** Python 3, aiogram 3.7, asyncpg, aiohttp, ReportLab
- **Frontend:** HTML5, Vanilla JS, Telegram WebApp SDK
- **Database:** PostgreSQL
- **Workflow:** n8n
- **Web server:** Nginx
- **PDF:** ReportLab

## License

Private project. All rights reserved.
