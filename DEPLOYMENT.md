# Deployment Guide — PsyhoTestBot

## Table of Contents
1. [Server Preparation](#1-server-preparation)
2. [Install Dependencies](#2-install-dependencies)
3. [Database Setup](#3-database-setup)
4. [n8n Setup](#4-n8n-setup)
5. [Deploy Mini App](#5-deploy-mini-app)
6. [Start Telegram Bot](#6-start-telegram-bot)
7. [Testing](#7-testing)

---

## 1. Server Preparation

### Requirements
- Ubuntu 20.04+ or Debian 11+
- Minimum 2GB RAM
- 20GB free disk space
- Public IP address
- Domain (recommended) or IP

### Install base packages
```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv nginx postgresql postgresql-contrib certbot python3-certbot-nginx git curl
```

---

## 2. Install Dependencies

```bash
python3 -m venv /opt/psycho-bot/venv
source /opt/psycho-bot/venv/bin/activate
pip install aiogram==3.7.0 asyncpg python-dotenv aiohttp reportlab
```

---

## 3. Database Setup

```bash
sudo -u postgres psql

-- In psql console:
CREATE USER psycho_user WITH PASSWORD 'your_strong_password';
CREATE DATABASE psycho_db OWNER psycho_user;
GRANT ALL PRIVILEGES ON DATABASE psycho_db TO psycho_user;
\q
```

Then apply the schema:
```bash
psql -U psycho_user -d psycho_db < database/schema.sql
```

---

## 4. n8n Setup

### Install via Docker
```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

mkdir -p /opt/n8n-data

docker run -d \
  --name n8n \
  --restart always \
  -p 5678:5678 \
  -v /opt/n8n-data:/home/node/.n8n \
  -e N8N_BASIC_AUTH_ACTIVE=true \
  -e N8N_BASIC_AUTH_USER=admin \
  -e N8N_BASIC_AUTH_PASSWORD=your_password \
  -e WEBHOOK_URL=https://your-domain.com \
  -e GENERIC_TIMEZONE=Europe/Kiev \
  n8nio/n8n
```

### Import workflow
1. Open n8n: `http://your-server:5678`
2. Login with credentials above
3. Import `n8n/workflow.json`
4. Update PostgreSQL credentials in all DB nodes:
   - Host: `localhost`
   - Database: `psycho_db`
   - User: `psycho_user`
   - Password: your password
5. In the "HTTP: Notify Doctor" node, set URL: `http://localhost:8080/result`
6. Activate the workflow

---

## 5. Deploy Mini App

### Nginx config
```bash
sudo mkdir -p /var/www/psycho-miniapp
sudo cp miniapp/index.html /var/www/psycho-miniapp/index.html
sudo chown -R www-data:www-data /var/www/psycho-miniapp

sudo tee /etc/nginx/sites-available/psycho-miniapp > /dev/null << 'EOF'
server {
    listen 80;
    server_name your-domain.com;

    root /var/www/psycho-miniapp;
    index index.html;

    location / {
        try_files $uri $uri/ =404;
    }

    location /webhook/ {
        proxy_pass http://localhost:5678/webhook/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/psycho-miniapp /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### Enable HTTPS (required for Telegram Mini App)
```bash
sudo certbot --nginx -d your-domain.com
```

---

## 6. Start Telegram Bot

### Configure environment
```bash
sudo mkdir -p /opt/psycho-bot
sudo cp bot/bot.py /opt/psycho-bot/bot.py
sudo cp .env.example /opt/psycho-bot/.env
sudo nano /opt/psycho-bot/.env   # fill in your values
```

### systemd service
```bash
sudo tee /etc/systemd/system/psycho-bot.service > /dev/null << 'EOF'
[Unit]
Description=PsyhoTestBot Telegram Bot
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/psycho-bot
Environment="PATH=/opt/psycho-bot/venv/bin"
ExecStart=/opt/psycho-bot/venv/bin/python /opt/psycho-bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable psycho-bot
sudo systemctl start psycho-bot
sudo systemctl status psycho-bot
```

---

## 7. Testing

1. Send `/start` to your bot in Telegram — main menu should appear
2. Create a new test: "➕ New Test" → pick PCL-5 → enter patient name → confirm
3. Open the generated link in a browser — intro screen should appear
4. Complete the test and click "Send to doctor"
5. Bot should send a result message with "View Result" and "Download PDF" buttons
6. Tap "Download PDF" — PDF report should be delivered to the chat

---

## Monitoring

```bash
# Bot logs
sudo journalctl -u psycho-bot -f

# Nginx access/error
sudo tail -f /var/log/nginx/error.log

# n8n
docker logs -f n8n

# All service status
sudo systemctl status psycho-bot nginx postgresql
docker ps
```

---

## Security Checklist

- [ ] HTTPS enabled (Let's Encrypt)
- [ ] Firewall configured (ufw allow 22, 80, 443)
- [ ] PostgreSQL accessible only from localhost
- [ ] Strong passwords in `.env`
- [ ] Automated backups (cron + pg_dump)
- [ ] fail2ban installed

---

## BotFather Menu Button

1. Open @BotFather
2. `/mybots` → select your bot
3. Bot Settings → Menu Button → Configure
4. Name: `Tests`
5. URL: `https://your-domain.com`
