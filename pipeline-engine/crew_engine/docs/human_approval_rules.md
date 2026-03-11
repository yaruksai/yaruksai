# Human Approval Rules & RBAC (fix_001)

## Amaç
İnsan müdahalesi gereken karar noktalarını basitleştirmek, rol bazlı yetkilendirmeyi (RBAC) netleştirmek ve karar gecikmelerini önlemek.

## Roller
- **Owner (Sahip):** Sistemin kurallarını ve policy’leri değiştirir, acil durum override yapabilir.
- **Admin (Yönetici):** Operasyon ayarlarını yönetir, kullanıcı/rol atar.
- **Reviewer (Denetçi):** İnsan onayı gereken kararlarda onay/red verir.
- **Operator (Operatör):** Run başlatır, çıktıları izler, onay gerektirenleri Review’a gönderir.
- **Viewer (Görüntüleyici):** Sadece okur, onay veremez.

## Karar Tipleri (3 sınıf)
1) **AUTO**: Sistem otomatik karar verir.
2) **REVIEW_REQUIRED**: İnsan onayı olmadan ilerlemez (fail-closed).
3) **REVIEW_OPTIONAL**: İnsan isterse bakar; zaman aşımında AUTO’ya düşebilir.

## Basit İş Kuralları (MVP)
### Kural-1: Güvenlik/erişim/policy değişikliği
- Decision: REVIEW_REQUIRED
- Yetkili: Owner/Admin

### Kural-2: Protokol veya altyapı değişikliği
- Decision: REVIEW_REQUIRED
- Yetkili: Owner/Admin
- Gerekçe: sistem davranışı ve risk profili değişir

### Kural-3: Standart iş akışı çalıştırma
- Decision: AUTO
- Yetkili: Operator (başlatabilir)

### Kural-4: Kritik hata / geri kazanım / rollback
- Decision: REVIEW_REQUIRED
- Yetkili: Admin/Owner

## Onay Akışı (MVP)
1) Operator run başlatır
2) Mizan “REVIEW_REQUIRED” üretirse:
   - Artifact: `approval_request.json` üretilir
   - Reviewer onay/red verir: `approval_decision.json`
3) Onay yoksa sistem bekler (fail-closed)

## Timeout sonrası politika
- Onay alınamazsa sistem karar otomatik olarak reddeder (fail-closed güvenliği)
- Timeout süreleri konfigürasyon dosyasından veya ortam değişkeninden değiştirilebilir

## Güvenlik Test Planı
- RBAC izinleri katı şekilde kontrol edilir
- CLI onay sürecinde eşzamanlı işlemler kilitlenir
- Audit log hash zinciri doğruluğu düzenli test edilir
