# ─── YARUKSAİ + Yaruk Strateji Nginx Configuration ───────────
# yaruksai.com   → YARUKSAİ (AI Ethics Engine)
# yaruk.com.tr   → Yaruk Strateji A.Ş. (Corporate)
# ──────────────────────────────────────────────────────────────

limit_req_zone $binary_remote_addr zone=yaruksai_limit:10m rate=30r/s;

# ─── yaruk.com.tr (Corporate) ────────────────────────────────
server {
    listen 80;
    listen 443 ssl;
    server_name yaruk.com.tr www.yaruk.com.tr;

    ssl_certificate /etc/nginx/ssl/self.crt;
    ssl_certificate_key /etc/nginx/ssl/self.key;

    root /var/www/yaruk.com.tr;
    index index.html;

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;

    location / {
        try_files $uri $uri/ /index.html;
    }

    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml;
}

# ─── yaruksai.com (AI Product) — Default ─────────────────────
server {
    listen 80 default_server;
    listen 443 ssl default_server;
    server_name yaruksai.com www.yaruksai.com _;

    ssl_certificate /etc/nginx/ssl/self.crt;
    ssl_certificate_key /etc/nginx/ssl/self.key;

    root /var/www/yaruksai.com;
    index index.html;

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Strict-Transport-Security "max-age=31536000" always;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # ─── Pipeline Admin Endpoints (Kill-Switch, Boot Status) ───
    location /api/admin/ {
        proxy_pass http://127.0.0.1:8000/api/admin/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 30s;
        limit_req zone=yaruksai_limit burst=5 nodelay;
    }

    # ─── Pipeline Demo Endpoints (AlphaHR) ───────────────────
    location /api/demo/ {
        proxy_pass http://127.0.0.1:8000/api/demo/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 120s;
        limit_req zone=yaruksai_limit burst=5 nodelay;
    }

    # ─── /v1/ Audit & Verify (FEAM OS — Pipeline Engine) ─────
    location /v1/ {
        proxy_pass http://127.0.0.1:8000/v1/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 120s;
        limit_req zone=yaruksai_limit burst=10 nodelay;
    }

    # ─── /api/v1/ Audit, Verify, Ledger, Emanet ─────────────
    location /api/v1/ {
        proxy_pass http://127.0.0.1:8000/api/v1/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 120s;
        limit_req zone=yaruksai_limit burst=10 nodelay;
    }

    # ─── /api/auth/ Login & Token ────────────────────────────
    location /api/auth/ {
        proxy_pass http://127.0.0.1:8000/api/auth/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 30s;
        limit_req zone=yaruksai_limit burst=10 nodelay;
    }

    # ─── /api/crm/ CRM Messages & Orders ────────────────────
    location /api/crm/ {
        proxy_pass http://127.0.0.1:8000/api/crm/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 30s;
        limit_req zone=yaruksai_limit burst=10 nodelay;
    }

    # ─── /api/content/ Content Pool ──────────────────────────
    location /api/content/ {
        proxy_pass http://127.0.0.1:8000/api/content/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 30s;
        limit_req zone=yaruksai_limit burst=10 nodelay;
    }

    # ─── /api/site/ Site CRUD ────────────────────────────────
    location /api/site/ {
        proxy_pass http://127.0.0.1:8000/api/site/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 30s;
        limit_req zone=yaruksai_limit burst=10 nodelay;
    }

    # ─── /api/config/ Weights ────────────────────────────────
    location /api/config/ {
        proxy_pass http://127.0.0.1:8000/api/config/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 30s;
        limit_req zone=yaruksai_limit burst=5 nodelay;
    }

    # ─── /api/memory/ Collective Memory ──────────────────────
    location /api/memory/ {
        proxy_pass http://127.0.0.1:8000/api/memory/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 30s;
        limit_req zone=yaruksai_limit burst=10 nodelay;
    }

    # ─── /api/certificate — PDF Sertifika ────────────────────
    location /api/certificate {
        proxy_pass http://127.0.0.1:8000/api/certificate;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 60s;
    }

    # ─── /health — Pipeline Health Check ─────────────────────
    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # ─── /auth/ — Auth Token Endpoint ────────────────────────
    location /auth/ {
        proxy_pass http://127.0.0.1:8000/auth/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 30s;
    }

    # ─── TS Engine API (7AI Council) — CATCH-ALL ─────────────
    location /api/ {
        proxy_pass http://127.0.0.1:3000/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 30s;
        proxy_read_timeout 120s;
        limit_req zone=yaruksai_limit burst=20 nodelay;
    }

    # ─── Pipeline SSE Stream (KRİTİK: buffering OFF) ────────
    # Bu blok /api/pipeline/stream/'den ÖNCE eşleşmeli.
    location /api/pipeline/stream/ {
        proxy_pass http://127.0.0.1:8000/api/pipeline/stream/;

        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE için zorunlu: buffering ve cache kapalı
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding on;

        # Uzun timeout: pipeline saatlerce çalışabilir
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;

        add_header Cache-Control "no-cache, no-transform" always;
        add_header X-Accel-Buffering "no" always;
    }

    # ─── Pipeline API (Normal Endpoints) ─────────────────────
    location /api/pipeline/ {
        proxy_pass http://127.0.0.1:8000/api/pipeline/;

        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 10s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;

        limit_req zone=yaruksai_limit burst=10 nodelay;
    }

    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml;
}
