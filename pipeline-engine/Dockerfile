# ─── YARUKSAİ Pipeline Engine ─────────────────────────────────
# Python 3.12 + CrewAI + FastAPI
# GÜVENLİK: --workers 1 (SSE + in-memory event bus)
# ──────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

# Sistem bağımlılıkları
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ─── Dependencies ─────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── Source Code ──────────────────────────────────────────────
COPY src/ ./src/
COPY app/ ./app/

# ─── Artifacts directory ─────────────────────────────────────
RUN mkdir -p /app/artifacts

# ─── Environment ─────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1
ENV ARTIFACT_ROOT=/app/artifacts
ENV MAX_CONCURRENT_RUNS=2
ENV SSE_PING_SECONDS=15

# No EXPOSE — bu container dış dünyaya port açmaz.
# Sadece Docker network üzerinden erişilebilir.

# ─── Healthcheck ─────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()" || exit 1

# ─── Run ─────────────────────────────────────────────────────
# KRİTİK: --workers 1 (SSE event bus in-memory, çoklu worker bozar)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
