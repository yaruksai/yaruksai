# Ownership & Versioning (fix_004)

## Dokümantasyon Sahipliği
- **Doc Owner:** docs/ altındaki dosyaların güncelliğinden sorumludur.
- **Tech Owner:** src/ altındaki akışların stabilitesinden sorumludur.
- **Release Owner:** sürümleme, tag ve release notlarından sorumludur.

## Sürüm Kontrol (Git) Prosedürü (MVP)
1) Her değişiklik branch üzerinden yapılır:
   - feature/<kisa-aciklama>
2) PR (pull request) açılır:
   - en az 1 gözden geçiren (Reviewer)
3) Merge kriterleri:
   - tests geçmeli
   - docs güncelse
4) Versiyonlama:
   - patch: küçük düzeltme
   - minor: yeni özellik
   - major: kırıcı değişiklik

## Değişiklik Kaydı
- artifacts/ çıktıları her run’da güncellenir
- docs/ değiştiyse kısa not eklenir
