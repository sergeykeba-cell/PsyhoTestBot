# Deployment guide

This guide sets up PsyhoTestBot on a single Linux server. The reference system is Ubuntu 24.04 with Python 3.12; other recent Debian-based systems should work the same way.

Read the [clinical and personal-data notices in the README](README.md) before putting real patient data into the system.

## Topology

```
Internet ─► Nginx :80/:443 ─┬─ static files        /var/www/psyho-miniapp/index.html
                            ├─ GET  /webhook/session      ─┐
                            └─ POST /webhook/psychotest   ─┤ proxied to 127.0.0.1:5678
                                                           ▼
   Docker network (172.19.0.0/24, bridge psyho0)          n8n ──► PostgreSQL (service "postgres")
                                                           │
   host process: bot.py ◄── POST /result (172.19.0.1:8082) ┘
   bot.py ──► PostgreSQL (127.0.0.1:5433)    bot.py ──► Telegram API
```

Rules that matter more than any single command:

- Only ports 22, 80 and 443 are reachable from the internet. Published Docker ports bypass UFW, so every published port is bound to `127.0.0.1`.
- Nginx proxies exactly two paths to n8n. The n8n editor is never exposed; you reach it through an SSH tunnel.
- n8n reaches the bot on the Docker bridge gateway, and one UFW rule on that bridge allows it.

## 0. Variables

Run the steps of this guide in one shell session, or export these again when you reconnect. `DOMAIN` must already point at the server (an A/AAAA record).

```bash
export DOMAIN=app.example.org
```

## 1. Packages and firewall

Install Docker Engine with the Compose plugin by following <https://docs.docker.com/engine/install/ubuntu/>. Docker commands below assume your admin user is in the `docker` group; otherwise prefix them with `sudo`.

```bash
sudo apt update
sudo apt install -y git nginx certbot python3-certbot-nginx python3-venv python3-pip ufw openssl

sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

Allow OpenSSH before enabling UFW, otherwise you lock yourself out.

## 2. Service user and code

```bash
sudo useradd --system --home-dir /opt/psyho-test-bot --no-create-home --shell /usr/sbin/nologin psyhobot
sudo install -d -o psyhobot -g psyhobot -m 750 /opt/psyho-test-bot
sudo -u psyhobot git clone https://github.com/sergeykeba-cell/PsyhoTestBot.git /opt/psyho-test-bot

sudo -u psyhobot python3 -m venv /opt/psyho-test-bot/venv
sudo -u psyhobot /opt/psyho-test-bot/venv/bin/pip install aiogram==3.7.0 asyncpg python-dotenv aiohttp reportlab
```

`report.py` generates the PDF reports and needs a font with Cyrillic glyphs; check the top of `report.py` for the font it expects and install the matching system package if it is not bundled.

## 3. Secrets and the bot's `.env`

Create the bot in Telegram with @BotFather (`/newbot`) and keep the token. Find your numeric Telegram ID with any "ID bot"; it will receive error notifications.

The commands below generate the random secrets and write them to files with restricted permissions. `read -s` keeps the bot token out of your shell history and the screen.

```bash
DB_PASSWORD=$(openssl rand -hex 24)
WEBHOOK_SECRET=$(openssl rand -hex 32)
read -rsp "Bot token from @BotFather: " BOT_TOKEN; echo
read -rp "Your numeric Telegram ID: " ADMIN_TG_ID

sudo -u psyhobot tee /opt/psyho-test-bot/.env >/dev/null <<EOF
BOT_TOKEN=$BOT_TOKEN
DATABASE_URL=postgresql://psyhotest_user:$DB_PASSWORD@127.0.0.1:5433/psyhotest
MINI_APP_URL=https://$DOMAIN
WEBHOOK_HOST=172.19.0.1
WEBHOOK_PORT=8082
WEBHOOK_SECRET=$WEBHOOK_SECRET
ADMIN_TG_ID=$ADMIN_TG_ID
EOF
sudo chmod 600 /opt/psyho-test-bot/.env

sudo install -d -m 755 /opt/psyho-stack
sudo install -d -m 700 /opt/psyho-stack/secrets
openssl rand -hex 24 | sudo tee /opt/psyho-stack/secrets/pg_admin_password >/dev/null
sudo chmod 600 /opt/psyho-stack/secrets/pg_admin_password
echo "DOMAIN=$DOMAIN" | sudo tee /opt/psyho-stack/.env >/dev/null
```

Never paste these values into chats, tickets or commits. If your shell session ends before step 5 is finished, read `DB_PASSWORD` back from `DATABASE_URL` in the bot's `.env`.

## 4. PostgreSQL and n8n (Docker)

Save this as `/opt/psyho-stack/docker-compose.yml`:

```yaml
name: psyho

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: postgres
      POSTGRES_DB: postgres
      POSTGRES_PASSWORD_FILE: /run/secrets/pg_admin_password
    secrets:
      - pg_admin_password
    ports:
      - "127.0.0.1:5433:5432"
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

  n8n:
    image: docker.n8n.io/n8nio/n8n:2.46.7
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    ports:
      - "127.0.0.1:5678:5678"
    environment:
      N8N_HOST: ${DOMAIN}
      N8N_PROTOCOL: https
      WEBHOOK_URL: https://${DOMAIN}/
      N8N_PROXY_HOPS: "1"
      N8N_SECURE_COOKIE: "true"
      N8N_DIAGNOSTICS_ENABLED: "false"
      GENERIC_TIMEZONE: Europe/Kyiv
      EXECUTIONS_DATA_PRUNE: "true"
      EXECUTIONS_DATA_MAX_AGE: "72"
    volumes:
      - n8n_data:/home/node/.n8n

secrets:
  pg_admin_password:
    file: ./secrets/pg_admin_password

volumes:
  pg_data:
  n8n_data:

networks:
  default:
    driver: bridge
    driver_opts:
      com.docker.network.bridge.name: psyho0
    ipam:
      config:
        - subnet: 172.19.0.0/24
          gateway: 172.19.0.1
```

Notes:

- The n8n version is pinned on purpose. Check the release notes for a current stable version before you deploy and pin that; update deliberately, after a backup.
- `workflow.json` and `WEBHOOK_HOST` assume the bridge gateway `172.19.0.1`. If that subnet is already used on your host (`docker network ls`, `ip route`), choose another one and change the gateway here, in the bot's `.env`, and in the **HTTP: Notify Doctor** node.
- `EXECUTIONS_DATA_PRUNE` and `EXECUTIONS_DATA_MAX_AGE` keep patient names and answers from piling up in n8n's execution history. Check the variable names against the n8n documentation for the version you pin.
- The `postgres` superuser is not used by the application. The application gets its own unprivileged role in the next step.

Start the stack:

```bash
cd /opt/psyho-stack
docker compose up -d
docker compose ps
```

Wait until `postgres` is `healthy`.

## 5. Database role and schema

Create the application role and database, then apply the schema as that role. `schema.sql` is idempotent, so you can run it again after updates.

```bash
docker exec -i psyho-postgres-1 psql -U postgres -v ON_ERROR_STOP=1 <<EOF
CREATE ROLE psyhotest_user LOGIN PASSWORD '$DB_PASSWORD';
CREATE DATABASE psyhotest OWNER psyhotest_user;
REVOKE CONNECT ON DATABASE psyhotest FROM PUBLIC;
EOF

sudo cat /opt/psyho-test-bot/schema.sql | docker exec -i psyho-postgres-1 psql -U psyhotest_user -d psyhotest -v ON_ERROR_STOP=1
```

If the database server is shared with other projects, keep the `REVOKE` line: by default every role may connect to every database.

## 6. Bot service

Save this as `/etc/systemd/system/psyhotestbot.service`:

```ini
[Unit]
Description=PsyhoTestBot
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=psyhobot
Group=psyhobot
WorkingDirectory=/opt/psyho-test-bot
ExecStart=/opt/psyho-test-bot/venv/bin/python /opt/psyho-test-bot/bot.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

The bot binds to the Docker bridge gateway, which exists only while the Compose network is up; `Restart=always` covers the case where the bot starts first. Allow n8n to reach it through the firewall and start the service:

```bash
sudo ufw allow in on psyho0 to 172.19.0.1 port 8082 proto tcp comment 'n8n -> bot'
sudo systemctl daemon-reload
sudo systemctl enable --now psyhotestbot
sleep 3
systemctl is-active psyhotestbot
sudo journalctl -u psyhotestbot -n 20 --no-pager
```

The log should end with `Webhook server started on port 8082` and `Run polling for bot`. A `409 Conflict` means another process is polling with the same bot token.

## 7. Patient page, Nginx and TLS

Publish the page:

```bash
sudo install -d -o www-data -g www-data /var/www/psyho-miniapp
sudo install -m 644 -o www-data -g www-data /opt/psyho-test-bot/index.html /var/www/psyho-miniapp/index.html
```

Rate limiting needs a zone in the `http` context:

```bash
sudo tee /etc/nginx/conf.d/psyho-limits.conf >/dev/null <<'EOF'
limit_req_zone $binary_remote_addr zone=psyho_webhook:10m rate=10r/s;
EOF
```

The site file below uses `__DOMAIN__` as a template token, which the following `sed` replaces. The quoted heredoc stops the shell from touching Nginx's own `$` variables.

```bash
sudo tee /etc/nginx/sites-available/psyho >/dev/null <<'EOF'
server {
    listen 80;
    listen [::]:80;
    server_name __DOMAIN__;
    server_tokens off;

    root /var/www/psyho-miniapp;
    index index.html;

    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer" always;

    location = /webhook/session {
        if ($args !~ "^token=[A-Za-z0-9-]{1,64}$") { return 404; }
        limit_req zone=psyho_webhook burst=30 nodelay;
        limit_req_status 429;
        limit_except GET { deny all; }
        proxy_pass http://127.0.0.1:5678;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }

    location = /webhook/psychotest {
        limit_req zone=psyho_webhook burst=30 nodelay;
        limit_req_status 429;
        limit_except POST { deny all; }
        proxy_pass http://127.0.0.1:5678;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
        client_max_body_size 1m;
    }

    location / {
        try_files $uri $uri/ =404;
    }
}
EOF
sudo sed -i "s/__DOMAIN__/$DOMAIN/g" /etc/nginx/sites-available/psyho
sudo ln -sf /etc/nginx/sites-available/psyho /etc/nginx/sites-enabled/psyho
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d "$DOMAIN" --redirect
```

Certbot rewrites the site file: it adds the TLS directives to this server block and creates a separate port-80 redirect. Keep that result; do not paste the original file over it. The `token` filter is deliberate: it rejects anything that is not a plain token before the request reaches n8n. If your workflow needs other query parameters on `/webhook/session`, extend the pattern.

Do not add `X-Frame-Options: DENY` or a restrictive `frame-ancestors` policy: Telegram shows the page inside its own web view.

## 8. n8n

Open the editor through an SSH tunnel from your own computer, then browse to `http://localhost:5678`:

```bash
ssh -L 5678:127.0.0.1:5678 your-user@your-server
```

If the sign-in does not work over plain `http://localhost` in your browser, temporarily set `N8N_SECURE_COOKIE` to `"false"` in the Compose file, run `docker compose up -d`, create the owner account, and set it back.

1. Create the **owner account**.
2. **Credentials → Create credential → Postgres:** host `postgres`, port `5432`, database `psyhotest`, user `psyhotest_user`, the application database password, SSL disabled. Read the password from `DATABASE_URL` in `/opt/psyho-test-bot/.env`.
3. **Credentials → Create credential → Header Auth:** name `X-Webhook-Secret`, value equal to `WEBHOOK_SECRET` from the same `.env`.
4. **Workflows → Import from file:** select `workflow.json`. The workflow file contains no credentials, so open each Postgres node and select the Postgres credential, and open **HTTP: Notify Doctor** and select the Header Auth credential. Check its URL: `http://172.19.0.1:8082/result`, or your gateway if you changed the subnet.
5. Recommended hardening: in both webhook triggers (**POST /psychotest**, **GET /session**) open **Options → Allowed Origins (CORS)** and set `https://your-domain`. In **Respond: Session Data** and **Respond: 405**, remove the `Access-Control-Allow-Origin: *` response header; the page and the webhooks share one origin, so it is not needed.
6. **Publish** the workflow. In n8n 2.x the production webhook runs the published version, so publish again after every edit. After an n8n restart the webhooks can take a short while to register, and a `404 "webhook is not registered"` during that window is normal.

## 9. Verification

Each check below proves one layer. Run the first group on the server.

```bash
curl -s -o /dev/null -w 'page: %{http_code} (expect 200)\n' "https://$DOMAIN/"
curl -s -o /dev/null -w '.env: %{http_code} (expect 404)\n' "https://$DOMAIN/.env"
curl -s -o /dev/null -w 'other webhook path: %{http_code} (expect 404)\n' "https://$DOMAIN/webhook/anything"
curl -s -o /dev/null -w 'bad token format: %{http_code} (expect 404)\n' "https://$DOMAIN/webhook/session?token=x%27%20OR%20%271%27%3D%271"
curl -s -w ' [%{http_code}] unknown token (expect {"error":"Session not found"} 404)\n' "https://$DOMAIN/webhook/session?token=x"
curl -s -o /dev/null -w 'empty submission: %{http_code} (expect 400)\n' -X POST "https://$DOMAIN/webhook/psychotest" -H 'Content-Type: application/json' -d '{}'
curl -sI "https://$DOMAIN/" | grep -iE '^(server|strict-transport|x-content-type|referrer)'
```

The bot's receiver and the n8n → bot path:

```bash
S=$(sudo grep ^WEBHOOK_SECRET= /opt/psyho-test-bot/.env | cut -d= -f2)
curl -s -o /dev/null -w 'no secret: %{http_code} (expect 401)\n' -X POST http://172.19.0.1:8082/result
curl -s -o /dev/null -w 'with secret, empty body: %{http_code} (expect 400)\n' -X POST -H "X-Webhook-Secret: $S" -H 'Content-Type: application/json' -d '{}' http://172.19.0.1:8082/result
docker exec psyho-n8n-1 node -e "fetch('http://172.19.0.1:8082/result',{method:'POST'}).then(r=>console.log('from the n8n container:',r.status,'(expect 401)')).catch(e=>console.log('ERROR',e.cause&&e.cause.code||e.message))"
```

An `ETIMEDOUT` from the container means the UFW rule from step 6 is missing or the interface name is wrong.

Run this from a different machine to confirm that only 22, 80 and 443 are open (replace the address with your server's):

```bash
for p in 22 80 443 5432 5433 5678 8082; do timeout 3 bash -c "</dev/tcp/203.0.113.10/$p" 2>/dev/null && echo "$p open" || echo "$p closed"; done
```

End to end: send `/start` to your bot in Telegram, choose **New test**, enter a two-word name, open the link on your phone and complete PCL-5. You should receive a message with the result and a PDF button. Then check the database:

```bash
docker exec -i psyho-postgres-1 psql -U psyhotest_user -d psyhotest -c "SELECT status, used_at FROM tokens ORDER BY created_at DESC LIMIT 1;" -c "SELECT test_type, score, status FROM results ORDER BY completed_at DESC LIMIT 1;"
```

Expected: the token is `completed` and the result is `notified`.

## 10. Operations

Logs:

```bash
sudo journalctl -u psyhotestbot -f
docker compose -f /opt/psyho-stack/docker-compose.yml logs -f n8n
docker exec -i psyho-postgres-1 psql -U psyhotest_user -d psyhotest -c "SELECT n8n_execution_id, node_name, left(error_message, 80) FROM error_logs ORDER BY created_at DESC LIMIT 10;"
```

**Updating the bot or the page:**

```bash
sudo -u psyhobot git -C /opt/psyho-test-bot pull
sudo install -m 644 -o www-data -g www-data /opt/psyho-test-bot/index.html /var/www/psyho-miniapp/index.html
sudo cat /opt/psyho-test-bot/schema.sql | docker exec -i psyho-postgres-1 psql -U psyhotest_user -d psyhotest -v ON_ERROR_STOP=1
sudo systemctl restart psyhotestbot
```

**Updating the workflow.** n8n does not synchronise with git. Import the new `workflow.json` as a new workflow, relink its credentials, unpublish the old workflow and publish the new one. Two published workflows with the same webhook paths conflict.

**Exporting the workflow back to git.** Never commit a raw n8n export. This command exports the workflow and strips instance identifiers; it stops if a long hex string remains.

```bash
docker exec psyho-n8n-1 n8n export:workflow --all 2>/dev/null > /tmp/n8n_export.json
python3 - <<'PY'
import json, re
raw = open('/tmp/n8n_export.json', encoding='utf-8').read()
workflows = json.loads(raw[raw.index('['):])
assert len(workflows) == 1, 'expected exactly one workflow, found %d' % len(workflows)
w = workflows[0]
for key in ('id', 'versionId', 'activeVersionId', 'active', 'shared', 'triggerCount', 'createdAt', 'updatedAt',
            'isArchived', 'meta', 'staticData', 'pinData', 'tags', 'homeProject', 'scopes'):
    w.pop(key, None)
w['active'] = False
text = json.dumps(w, ensure_ascii=False, indent=2)
assert not re.findall(r'\b[0-9a-fA-F]{32,}\b', text), 'long hex string found'
open('workflow.json', 'w', encoding='utf-8').write(text + '\n')
print('workflow.json written, nodes:', len(w['nodes']))
PY
rm -f /tmp/n8n_export.json
```

**Updating n8n.** Take a backup first, read the release notes of the target version, change the image tag in the Compose file, run `docker compose up -d`, and repeat the verification in step 9.

**Backups.** The database dump contains patient names and answers, and the n8n volume contains the key that encrypts your stored credentials. Treat both as sensitive: encrypt them and copy them off the server, since backups kept only on the same disk are lost together with it.

```bash
sudo install -d -m 700 /var/backups/psyho
docker exec psyho-postgres-1 pg_dump -U psyhotest_user -Fc psyhotest | sudo tee /var/backups/psyho/psyhotest-$(date +%F).dump >/dev/null

docker compose -f /opt/psyho-stack/docker-compose.yml stop n8n
docker run --rm -v psyho_n8n_data:/data -v /var/backups/psyho:/backup alpine tar czf /backup/n8n-data-$(date +%F).tgz -C /data .
docker compose -f /opt/psyho-stack/docker-compose.yml start n8n
```

Stopping n8n for the archive keeps its SQLite database consistent. Schedule these commands (for example a root cron entry at 03:30), delete old copies on a schedule that matches your retention policy, and test a restore at least once: `pg_restore --list` on the dump shows whether it is readable, and a restore into an empty scratch database proves the rest.

## 11. Security checklist

- From another machine, only ports 22, 80 and 443 respond (step 9).
- SSH accepts keys only and does not allow root. Put these two lines into `/etc/ssh/sshd_config.d/99-hardening.conf`, run `sudo sshd -t`, reload with `sudo systemctl reload ssh`, and verify a second login before closing your current session:

  ```
  PermitRootLogin no
  PasswordAuthentication no
  ```

- `.env` is `600` and owned by `psyhobot`; nothing secret is in git. Run `git status` and `git diff --cached` before every commit.
- Rotate secrets when they leak or people leave. For a bot token use @BotFather `/revoke`. For `WEBHOOK_SECRET`, generate a new value, change it in the bot's `.env` and in the n8n Header Auth credential, then restart the bot.
- Restrict who can register as a doctor before you share the bot's username (see the README's known limitations).
- Keep n8n, Docker images and the operating system updated, and watch the security advisories of n8n.
- Decide on retention: how long results stay in `results`, in backups and in n8n's execution history.

## 12. Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| `404 … webhook is not registered` on a production URL | The workflow is not published, or n8n has just restarted. Publish it, or wait a few seconds |
| Bot exits with `WEBHOOK_SECRET is required` | `WEBHOOK_SECRET` is missing or empty in `.env` |
| `HTTP: Notify Doctor` fails with `401` | The Header Auth credential holds an old or wrong secret. It must equal `WEBHOOK_SECRET` |
| `HTTP: Notify Doctor` times out | The UFW rule for the bridge is missing, the bot is down, or the gateway address differs from the node's URL |
| The doctor gets no message but the result is stored | The result stays `scored`. Fix the cause above; there is no automatic resend |
| SQL errors mentioning `sex` or `details` | The schema is outdated. Run `schema.sql` again (step 5) |
| Patient sees "Не вдалося надіслати" | The submission was rejected or scoring failed. Look at `error_logs` and the n8n execution; the token is released, so the patient can retry |
| `409` on submit | The token is already used, or another request holds it (`processing`) |
| `429` | Nginx rate limit; the burst allowance was exceeded |
| Bot log shows `409 Conflict` | Another process polls with the same bot token |
| Page opens but the test does not load | `GET /webhook/session` returns 404: check the token format, the published workflow and the Nginx `token` filter |
