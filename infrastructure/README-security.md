# Sentinel — IT Security Deployment Guide

Operational hardening steps for the Example Broadcaster production server. All steps require root/sudo access and should be performed by IT Admin (IT Admin).

---

## 1. Ollama Port Security (Nginx Reverse Proxy)

Ollama listens on port 11434 with no authentication by default. The Nginx reverse proxy adds bearer token validation on port 11435, then the firewall blocks direct access to 11434.

### Step 1 — Generate a bearer token

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
# Example output: a3f8c2e1d4b7...  (64 hex chars)
```

### Step 2 — Deploy Nginx config

```bash
sudo cp infrastructure/nginx-ollama.conf /etc/nginx/sites-available/ollama-proxy

# Replace the placeholder token in the config
sudo sed -i 's/YOUR_STRONG_BEARER_TOKEN_HERE/YOUR_ACTUAL_TOKEN/' \
    /etc/nginx/sites-available/ollama-proxy

sudo ln -s /etc/nginx/sites-available/ollama-proxy \
           /etc/nginx/sites-enabled/ollama-proxy

sudo nginx -t          # validate config
sudo systemctl reload nginx
```

### Step 3 — Set token in Sentinel .env

```bash
# In api/.env:
OLLAMA_BASE_URL=http://127.0.0.1:11435
OLLAMA_BEARER_TOKEN=YOUR_ACTUAL_TOKEN
```

### Step 4 — Restart API

```bash
kill $(lsof -ti:8000)
cd ~/sentinel/api && nohup .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 > nohup.out 2>&1 &
```

### Verify

```bash
# Should return 200 with model list
curl -H "Authorization: Bearer YOUR_ACTUAL_TOKEN" http://localhost:11435/api/tags

# Should return 401
curl http://localhost:11435/api/tags
```

---

## 2. Firewall Rules

Block all external access to Ollama and restrict scraper outbound to known domains only.

```bash
# Install ufw if not present
sudo apt install -y ufw

# Default deny incoming, allow outgoing
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH (do this BEFORE enabling ufw or you will lock yourself out)
sudo ufw allow 22/tcp

# Allow Sentinel services
sudo ufw allow 8000/tcp   # API
sudo ufw allow 3000/tcp   # Dashboard (or restrict to LAN only below)
sudo ufw allow 11435/tcp  # Ollama via Nginx (LAN only)

# BLOCK direct Ollama access
sudo ufw deny 11434

# Restrict dashboard to LAN subnet (replace 192.168.1.0/24 with your LAN)
sudo ufw delete allow 3000/tcp
sudo ufw allow from 192.168.1.0/24 to any port 3000

# Enable and verify
sudo ufw enable
sudo ufw status verbose
```

---

## 3. DMZ Proxy for Scrapers (Squid)

The Veriff scrapers make outbound HTTP requests to fact-check APIs. Running them through a forward proxy:
- Logs all outbound requests for audit
- Blocks unexpected outbound destinations
- Prevents SSRF from escaping the LAN

### Install Squid

```bash
sudo apt install -y squid
```

### Config skeleton: `/etc/squid/squid.conf`

```
# Sentinel — Squid forward proxy for Veriff scrapers

# Allowed outbound destinations for fact-check scrapers
acl sentinel_scrapers src 127.0.0.1/32

acl allowed_sites dstdomain .pib.gov.in
acl allowed_sites dstdomain .altnews.in
acl allowed_sites dstdomain .vishvasnews.com
acl allowed_sites dstdomain .factcrescendo.com
acl allowed_sites dstdomain .indiatoday.in
acl allowed_sites dstdomain .thequint.com
acl allowed_sites dstdomain .newschecker.in
acl allowed_sites dstdomain .youtube.com
acl allowed_sites dstdomain .googleapis.com

# Also required — the scrapers and agents call these too. Omitting them
# silently breaks Google News/Search fan-out and the Meta integration.
acl allowed_sites dstdomain .google.com
acl allowed_sites dstdomain news.google.com
acl allowed_sites dstdomain .facebook.com
acl allowed_sites dstdomain graph.facebook.com
acl allowed_sites dstdomain .huggingface.co    # embedding model download (first run)
acl allowed_sites dstdomain .pib.gov.in

http_access allow sentinel_scrapers allowed_sites
http_access deny all

http_port 3128
```

### Enable and point scrapers at proxy

```bash
sudo systemctl enable squid
sudo systemctl start squid

# In api/.env or agents environment:
HTTP_PROXY=http://127.0.0.1:3128
HTTPS_PROXY=http://127.0.0.1:3128
```

---

## 4. mTLS Setup (Post-Delivery)

For production environments where Ollama and the API run on separate servers, replace the bearer token proxy with mutual TLS.

### Generate certificates

```bash
# CA
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt -subj "/CN=Sentinel CA"

# Server cert (for Ollama/Nginx)
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=ollama.sentinel.internal"
openssl x509 -req -days 365 -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt

# Client cert (for API)
openssl genrsa -out client.key 2048
openssl req -new -key client.key -out client.csr -subj "/CN=sentinel-api"
openssl x509 -req -days 365 -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out client.crt
```

### Nginx mTLS config (replaces nginx-ollama.conf)

```nginx
server {
    listen 11435 ssl;
    ssl_certificate     /etc/ssl/sentinel/server.crt;
    ssl_certificate_key /etc/ssl/sentinel/server.key;
    ssl_client_certificate /etc/ssl/sentinel/ca.crt;
    ssl_verify_client   on;

    location /api/ {
        proxy_pass http://127.0.0.1:11434;
        proxy_read_timeout 120s;
    }
}
```

### API client (update OllamaClient in api/core/ollama_client.py)

```python
httpx.Client(
    timeout=timeout,
    cert=("/etc/ssl/sentinel/client.crt", "/etc/ssl/sentinel/client.key"),
    verify="/etc/ssl/sentinel/ca.crt",
)
```

---

## Certificate / Token Rotation Schedule

| Secret | Rotate | Procedure |
|---|---|---|
| Ollama bearer token | Every 90 days | Update Nginx config + api/.env, reload Nginx, restart API |
| mTLS client cert | Every 365 days | Reissue with CA, update api/.env paths, zero-downtime restart |
| JWT signing key | Every 180 days | Update JWT_SECRET_KEY in api/.env, restart API (all sessions invalidated) |
| DB app password | Every 180 days | ALTER ROLE sentinel_app PASSWORD '...', update DATABASE_URL in api/.env |

---

*Author: Nitin Pranav · github.com/niku34*
