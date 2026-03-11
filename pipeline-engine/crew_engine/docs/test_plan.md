# Test Plan (fix_003)

## Amaç
Kritik fonksiyonlar için otomasyon + manuel testleri tanımlamak.
Özellikle: hata geri kazanımı ve insan onayı süreçleri.

## Test Kategorileri
### 1) Unit Tests
- JSON parse (parse_json_safe)
- mizan_engine karar parsing
- role rules format doğrulama (docs üzerinden)

### 2) Integration Tests
- orchestrator akışı: Architect->Auditor->Mizan
- Builder skip olduğunda final gate kararının "revise" olması
- artifacts dosyalarının üretilmesi

### 3) Failure/Recovery Tests
- Env key eksikse fail-closed
- LLM çıktısı bozuk JSON ise parse_json_safe hata verir mi?
- Mizan approve vermezse Builder çalışmıyor mu?
- Post-build audit fallback doğru üretiliyor mu?

### 4) Manual Tests (MVP)
- İnsan onayı gereken senaryo simülasyonu:
  - approval_request.json üretilir
  - approval_decision.json ile devam edilir (gelecek iterasyon)

## Minimum Başarı Kriteri (MVP)
- `python -m py_compile` hatasız
- `python src/flows/orchestrator.py` artifacts üretir
- Final gate çıktısı: Builder skip => revise
