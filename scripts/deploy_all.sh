#!/bin/bash
# ──────────────────────────────────────────────────────────────
# YARUKSAİ — Sunucu Kurulum Scripti
# Tek komutla: Ollama + LLM + Pipeline + Deploy
# ──────────────────────────────────────────────────────────────
# Kullanım: bash scripts/deploy_all.sh
# ──────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${GREEN}[YARUKSAİ]${NC} $1"; }
warn() { echo -e "${YELLOW}[UYARI]${NC} $1"; }
err() { echo -e "${RED}[HATA]${NC} $1"; exit 1; }

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo -e "${CYAN}"
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║         YARUKSAİ — Full Deploy Script         ║"
echo "  ║   Ollama + LLM + Pipeline + TS Engine         ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo -e "${NC}"

# ──────────────────────────────────────────────────────────────
# 1. OLLAMA KURULUMU
# ──────────────────────────────────────────────────────────────
log "ADIM 1/5: Ollama kurulumu..."

if command -v ollama &> /dev/null; then
    log "Ollama zaten kurulu: $(ollama --version 2>/dev/null || echo 'version unknown')"
else
    log "Ollama kuruluyor..."
    curl -fsSL https://ollama.ai/install.sh | sh
    log "Ollama kuruldu ✅"
fi

# Ollama servisini başlat (systemd varsa)
if systemctl is-active --quiet ollama 2>/dev/null; then
    log "Ollama servisi çalışıyor ✅"
else
    log "Ollama servisi başlatılıyor..."
    systemctl start ollama 2>/dev/null || ollama serve &
    sleep 3
fi

# ──────────────────────────────────────────────────────────────
# 2. LLM MODEL İNDİR
# ──────────────────────────────────────────────────────────────
log "ADIM 2/5: LLM modeli indiriliyor..."

# Sunucu RAM'ine göre model seç
TOTAL_RAM_GB=$(free -g 2>/dev/null | awk '/^Mem:/{print $2}' || echo "4")

if [ "$TOTAL_RAM_GB" -ge 16 ]; then
    MODEL="llama3.2:3b"
    log "16GB+ RAM — Llama 3.2 3B kullanılacak"
elif [ "$TOTAL_RAM_GB" -ge 8 ]; then
    MODEL="phi4-mini"
    log "8GB RAM — Phi-4 Mini kullanılacak"
else
    MODEL="gemma3:1b"
    log "4GB RAM — Gemma 3 1B kullanılacak (küçük ama çalışır)"
fi

if ollama list 2>/dev/null | grep -q "$MODEL"; then
    log "Model $MODEL zaten mevcut ✅"
else
    log "Model indiriliyor: $MODEL (bu biraz sürebilir...)"
    ollama pull "$MODEL"
    log "Model indirildi ✅"
fi

# Test
log "LLM test ediliyor..."
RESPONSE=$(curl -s http://localhost:11434/api/generate \
    -d "{\"model\":\"$MODEL\",\"prompt\":\"Merhaba, ben YARUKSAİ. 1 cümle ile yanıt ver.\",\"stream\":false}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('response','HATA')[:100])" 2>/dev/null || echo "BAĞLANTI HATASI")

if [ "$RESPONSE" != "BAĞLANTI HATASI" ] && [ -n "$RESPONSE" ]; then
    log "LLM çalışıyor ✅ → $RESPONSE"
else
    warn "LLM test yanıt vermedi, devam ediliyor..."
fi

# ──────────────────────────────────────────────────────────────
# 3. ENV GÜNCELLE
# ──────────────────────────────────────────────────────────────
log "ADIM 3/5: Environment güncelleniyor..."

# Ollama URL'sini .env.production'a ekle (yoksa)
if ! grep -q "OLLAMA_BASE_URL" .env.production 2>/dev/null; then
    cat >> .env.production << 'EOF'

# ─── Ollama (Self-Hosted LLM) ────────────────────────────────
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=gemma3:1b
LLM_FALLBACK_CHAIN=groq,ollama
EOF
    log ".env.production güncellendi ✅"
else
    log ".env.production zaten Ollama ayarlı ✅"
fi

# Modeli env'ye yaz
sed -i "s/OLLAMA_MODEL=.*/OLLAMA_MODEL=$MODEL/" .env.production 2>/dev/null || true

# ──────────────────────────────────────────────────────────────
# 4. DOCKER BUILD + UP
# ──────────────────────────────────────────────────────────────
log "ADIM 4/5: Docker container'lar build ediliyor..."

# Docker compose ile tüm servisleri build et
docker compose build --no-cache

log "Container'lar başlatılıyor..."
docker compose up -d

# Sağlık kontrolü (30 sn bekle)
log "Servisler başlatılıyor, 15 saniye bekleniyor..."
sleep 15

# TS Engine health
TS_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/health 2>/dev/null || echo "000")
if [ "$TS_HEALTH" = "200" ]; then
    log "TS Engine (7AI Council) ✅ HTTP 200"
else
    warn "TS Engine yanıt vermedi (HTTP $TS_HEALTH) — başlatılıyor olabilir"
fi

# Pipeline health (Docker network içinden)
PY_HEALTH=$(docker exec yaruksai-pipeline python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())" 2>/dev/null || echo "HATA")
if echo "$PY_HEALTH" | grep -q '"ok"'; then
    log "Pipeline Engine (CrewAI) ✅"
else
    warn "Pipeline Engine yanıt vermedi — logları kontrol et: docker compose logs yaruksai-pipeline"
fi

# ──────────────────────────────────────────────────────────────
# 5. NGINX RELOAD
# ──────────────────────────────────────────────────────────────
log "ADIM 5/5: Nginx yeniden yükleniyor..."

# Landing page'leri kopyala
if [ -d "landing" ]; then
    mkdir -p /var/www/yaruksai.com
    cp -r landing/* /var/www/yaruksai.com/ 2>/dev/null || true
    log "Landing pages kopyalandı ✅"
fi

# Nginx config kopyala ve test et
cp nginx/yaruksai.com /etc/nginx/conf.d/yaruksai.com 2>/dev/null || \
cp nginx/yaruksai.com /etc/nginx/sites-available/yaruksai.com 2>/dev/null || true

if nginx -t 2>/dev/null; then
    nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null
    log "Nginx yeniden yüklendi ✅"
else
    warn "Nginx config hatası — kontrol et: nginx -t"
fi

# ──────────────────────────────────────────────────────────────
# SONUÇ
# ──────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ YARUKSAİ DEPLOY TAMAMLANDI${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  🤖 LLM Model:     ${GREEN}$MODEL${NC} (Ollama — kendi sunucumuz)"
echo -e "  🌐 Web:           ${GREEN}https://yaruksai.com${NC}"
echo -e "  ⚙️  TS Engine:     ${GREEN}http://localhost:3000${NC}"
echo -e "  🐍 Pipeline:      ${GREEN}Docker network (8000)${NC}"
echo -e "  🤖 Ollama:        ${GREEN}http://localhost:11434${NC}"
echo ""
echo -e "  Test komutları:"
echo -e "    curl https://yaruksai.com/api/health"
echo -e "    curl http://localhost:11434/api/generate -d '{\"model\":\"$MODEL\",\"prompt\":\"test\"}'"
echo -e "    docker compose logs -f"
echo ""
