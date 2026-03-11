# Protocol Security & Performance Evaluation (fix_002)

## Amaç
Ajanlar arası iletişim protokolünü (REST/gRPC/MQ) güvenlik + ölçeklenebilirlik + performans kriterleriyle karşılaştırmak.

## Aday Protokoller
- **REST (HTTP/JSON)**
- **gRPC (HTTP/2 + protobuf)**
- **MQ (örn: NATS / Redis Streams / RabbitMQ)**

## Seçim Kriterleri
### Performans
- p95 latency (ms)
- throughput (req/s veya msg/s)
- connection overhead
- backpressure davranışı

### Güvenlik
- TLS desteği (mTLS mümkün mü?)
- kimlik doğrulama (token, cert)
- yetkilendirme (RBAC uyumu)
- replay / tamper riskleri
- audit log ile ilişkilendirme (trace_id)

### Operasyon
- gözlemlenebilirlik (metrics/logs/traces)
- devops maliyeti
- hata toleransı (retry, dead-letter, queue durability)

## MVP İçin Test Senaryoları
1) Normal yük: 10 rps/msg/s
2) Burst: 10x artış (100 rps/msg/s)
3) Failure injection:
   - timeout
   - drop
   - retry storm
4) Güvenlik:
   - invalid token/cert
   - replay attempt (aynı message id)

## Karar Kuralı (MVP)
- Eğer mTLS + yüksek throughput gerekiyorsa → gRPC veya MQ
- Eğer hızlı basitlik gerekiyorsa → REST
- Eğer dayanıklılık ve async koordinasyon kritikse → MQ

## Çıktı
Benchmark sonuçları `artifacts/protocol_benchmark.json` olarak saklanır.

---

## Timeout & Retry Kriterleri (fix: [HIGH][logic])

### Varsayılan Limitler (MVP)
- **Request timeout:** 2s (p95 hedefi: < 200ms ise yeterli tampon)
- **Max retry:** 2
- **Retry backoff:** exponential (200ms, 400ms) + jitter
- **Idempotency:** Retry yapılan çağrılar idempotent olmalı veya message_id ile dedupe yapılmalı

### Fail-Closed Kuralları
- 2 retry sonrası başarısızlık → "REVIEW_REQUIRED" veya "STOP" (iş kuralına göre)
- Aynı `message_id` tekrar gelirse → duplicate olarak işaretle, ikinciyi işleme alma

### Prototip Doğrulama
- Timeout injection (sleep/delay) ile p95 ölçümü
- Retry storm senaryosu: 100 mesaj/istek, %20 timeout -> sistem kararlı mı?
