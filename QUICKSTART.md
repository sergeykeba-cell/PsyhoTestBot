# Quick Start — 30-Minute Setup

## Step 1: System Packages (5 min)
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv nginx postgresql postgresql-contrib git
```

## Step 2: Database (5 min)
```bash
sudo -u postgres psql << 'EOF'
CREATE USER psycho_user WITH PASSWORD 'YOUR_PASSWORD';
CREATE DATABASE psycho_db OWNER psycho_user;
GRANT ALL PRIVILEGES ON DATABASE psycho_db TO psycho_user;
\q
EOF

psql -U psycho_user -d psycho_db < database/schema.sql
```

## Step 3: Mini App (5 min)
```bash
sudo mkdir -p /var/www/psycho-miniapp
sudo cp miniapp/index.html /var/www/psycho-miniapp/index.html
sudo chown -R www-data:www-data /var/www/psycho-miniapp

sudo tee /etc/nginx/sites-available/psycho << 'EOF'
server {
    listen 80;
    server_name YOUR_DOMAIN_OR_IP;
    root /var/www/psycho-miniapp;
    index index.html;
    location / { try_files $uri $uri/ =404; }
    location /webhook/ {
        proxy_pass http://localhost:5678/webhook/;
        proxy_set_header Host $host;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/psycho /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl restart nginx
```

## Step 4: n8n (5 min)
```bash
curl -fsSL https://get.docker.com | sudo sh

docker run -d --name n8n --restart always -p 5678:5678 \
  -v /opt/n8n-data:/home/node/.n8n \
  -e N8N_BASIC_AUTH_ACTIVE=true \
  -e N8N_BASIC_AUTH_USER=admin \
  -e N8N_BASIC_AUTH_PASSWORD=YOUR_PASSWORD \
  -e WEBHOOK_URL=http://YOUR_IP_OR_DOMAIN \
  -e GENERIC_TIMEZONE=Europe/Kiev \
  n8nio/n8n

# Then open http://YOUR_IP:5678 and import n8n/workflow.json
# Update PostgreSQL credentials in all DB nodes
# Set "HTTP: Notify Doctor" URL to http://localhost:8080/result
# Activate the workflow
```

## Step 5: Bot (10 min)
```bash
sudo mkdir -p /opt/psycho-bot
python3 -m venv /opt/psycho-bot/venv
source /opt/psycho-bot/venv/bin/activate
pip install aiogram==3.7.0 asyncpg python-dotenv aiohttp reportlab

sudo cp bot/bot.py /opt/psycho-bot/bot.py
sudo cp .env.example /opt/psycho-bot/.env
# Edit /opt/psycho-bot/.env with your real values

sudo tee /etc/systemd/system/psycho-bot.service > /dev/null << 'EOF'
[Unit]
Description=PsyhoTestBot
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
sudo systemctl enable --now psycho-bot
sudo systemctl status psycho-bot
```

## Verify

```bash
# Database
psql -U psycho_user -d psycho_db -c "SELECT COUNT(*) FROM doctors;"

# Mini App
curl http://localhost/

# Bot: send /start in Telegram

# Webhook endpoint
curl -X POST http://localhost:8080/result \
  -H "Content-Type: application/json" \
  -d '{"session_token":"test"}'
# Should return 404 with JSON error (endpoint is alive)
```

## HTTPS (required for Telegram Mini App)
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d YOUR_DOMAIN
# Update MINI_APP_URL in .env → restart bot
sudo systemctl restart psycho-bot
```
