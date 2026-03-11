# Audit Log Compliance Matrix (MVP)

This project ships with a default audit retention policy, but **retention/backup/access** MUST be configurable per:
- **Customer contract** (SLA, industry requirements)
- **Local legal obligations** (country/region)
- **Data classification** (PII, financial, security events)

## Default (Safe Baseline)
- Hot retention: 180 days
- Cold archive: 2 years
- Daily backup + weekly integrity verification (hash-chain)

## Configurable Parameters
- RETENTION_HOT_DAYS
- RETENTION_COLD_DAYS
- BACKUP_SCHEDULE_CRON
- LEGAL_HOLD (on/off, case id)
- EXPORT_FORMAT (jsonl/parquet)

## Process
1) Customer onboarding selects a compliance profile.
2) System records profile in immutable audit metadata.
3) Quarterly compliance review: retention + access logs + restore drill.

## Notes
- Defaults are conservative; **policy is not one-size-fits-all**.
- Any override requires explicit approval and is logged.
