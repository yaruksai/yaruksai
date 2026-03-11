"""
mizan_engine/shahid_ledger.py — Shahid Ledger (WORM Storage)
═══════════════════════════════════════════════════════════

Geri dönülemez denetim kaydı — Write Once Read Many.

• Her denetim raporu eklendikten sonra DEĞİŞTİRİLEMEZ ve SİLİNEMEZ.
• Blockchain zincirleme: H_t = SHA256(H_{t-1} || σ_t || timestamp)
• SQLite tabanlı — dosya düzeyinde taşınabilir.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Genesis Hash — zincirin ilk halkası ──
GENESIS_HASH = "0000000000000000000000000000000000000000000000000000000000000000"

# ── Varsayılan DB Yolu ──
DEFAULT_LEDGER_PATH = os.getenv(
    "SHAHID_LEDGER_PATH",
    "/app/data/shahid_ledger.db"
)


@dataclass(frozen=True)
class LedgerEntry:
    """Ledger'daki tek bir kayıt — immutable."""
    id: int
    run_id: str
    sigma: str
    verdict: str
    sha256_seal: str
    proof_hash: str
    prev_hash: str
    timestamp: str
    eu_ai_act_refs: str  # JSON string
    metadata: str        # JSON string

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "sigma": self.sigma,
            "verdict": self.verdict,
            "sha256_seal": self.sha256_seal,
            "proof_hash": self.proof_hash,
            "prev_hash": self.prev_hash,
            "timestamp": self.timestamp,
            "eu_ai_act_refs": json.loads(self.eu_ai_act_refs),
            "metadata": json.loads(self.metadata),
        }


class ShahidLedger:
    """
    WORM (Write Once Read Many) denetim defterleri.
    
    Kurallar:
    - INSERT: serbest (sadece append)
    - UPDATE: YASAK → exception
    - DELETE: YASAK → exception
    - SELECT: serbest
    
    Blockchain Linkage:
    H_t = SHA256(H_{t-1} || σ_t || timestamp)
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_LEDGER_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Tablo oluştur ve WORM trigger'ları ekle."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS shahid_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                sigma TEXT NOT NULL,
                verdict TEXT NOT NULL,
                sha256_seal TEXT NOT NULL,
                proof_hash TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                eu_ai_act_refs TEXT NOT NULL DEFAULT '[]',
                metadata TEXT NOT NULL DEFAULT '{}'
            )
        """)

        # WORM: UPDATE yasak
        c.execute("""
            CREATE TRIGGER IF NOT EXISTS worm_no_update
            BEFORE UPDATE ON shahid_ledger
            BEGIN
                SELECT RAISE(ABORT, 'WORM: Shahid Ledger kayıtları değiştirilemez');
            END
        """)

        # WORM: DELETE yasak
        c.execute("""
            CREATE TRIGGER IF NOT EXISTS worm_no_delete
            BEFORE DELETE ON shahid_ledger
            BEGIN
                SELECT RAISE(ABORT, 'WORM: Shahid Ledger kayıtları silinemez');
            END
        """)

        conn.commit()
        conn.close()

    def _get_last_hash(self) -> str:
        """Son kaydın proof_hash'ini al (zincir devamı için)."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT proof_hash FROM shahid_ledger ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        conn.close()
        return row[0] if row else GENESIS_HASH

    def _compute_proof_hash(self, prev_hash: str, sigma: str, timestamp: str) -> str:
        """
        Blockchain zincirleme:
        H_t = SHA256(H_{t-1} || σ_t || timestamp)
        """
        data = f"{prev_hash}||{sigma}||{timestamp}"
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def append(
        self,
        run_id: str,
        sigma: str,
        verdict: str,
        sha256_seal: str,
        eu_ai_act_refs: Optional[List[Dict]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LedgerEntry:
        """
        Yeni denetim kaydı ekle — APPEND ONLY.
        
        Returns: oluşturulan LedgerEntry
        Raises: sqlite3.IntegrityError eğer run_id zaten varsa
        """
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        prev_hash = self._get_last_hash()
        proof_hash = self._compute_proof_hash(prev_hash, sigma, ts)

        refs_json = json.dumps(eu_ai_act_refs or [], ensure_ascii=False)
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute("""
            INSERT INTO shahid_ledger 
            (run_id, sigma, verdict, sha256_seal, proof_hash, prev_hash, timestamp, eu_ai_act_refs, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (run_id, sigma, verdict, sha256_seal, proof_hash, prev_hash, ts, refs_json, meta_json))

        entry_id = c.lastrowid
        conn.commit()
        conn.close()

        return LedgerEntry(
            id=entry_id,
            run_id=run_id,
            sigma=sigma,
            verdict=verdict,
            sha256_seal=sha256_seal,
            proof_hash=proof_hash,
            prev_hash=prev_hash,
            timestamp=ts,
            eu_ai_act_refs=refs_json,
            metadata=meta_json,
        )

    def get(self, run_id: str) -> Optional[LedgerEntry]:
        """Tek kayıt sorgula."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT * FROM shahid_ledger WHERE run_id = ?", (run_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            return None
        return LedgerEntry(*row)

    def get_all(self, limit: int = 100) -> List[LedgerEntry]:
        """Tüm kayıtları döndür (en yeni önce)."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT * FROM shahid_ledger ORDER BY id DESC LIMIT ?", (limit,))
        rows = c.fetchall()
        conn.close()
        return [LedgerEntry(*row) for row in rows]

    def verify_chain(self) -> Dict[str, Any]:
        """
        Tüm zincirin bütünlüğünü doğrula.
        
        Her kaydın proof_hash'i, önceki kaydın proof_hash'i ile
        yeniden hesaplanarak kontrol edilir.
        
        Returns: {"valid": bool, "total": int, "errors": [...]}
        """
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT * FROM shahid_ledger ORDER BY id ASC")
        rows = c.fetchall()
        conn.close()

        entries = [LedgerEntry(*row) for row in rows]
        errors = []

        for i, entry in enumerate(entries):
            expected_prev = entries[i - 1].proof_hash if i > 0 else GENESIS_HASH

            if entry.prev_hash != expected_prev:
                errors.append({
                    "id": entry.id,
                    "run_id": entry.run_id,
                    "error": f"prev_hash mismatch: expected {expected_prev[:16]}..., got {entry.prev_hash[:16]}...",
                })

            expected_proof = self._compute_proof_hash(entry.prev_hash, entry.sigma, entry.timestamp)
            if entry.proof_hash != expected_proof:
                errors.append({
                    "id": entry.id,
                    "run_id": entry.run_id,
                    "error": f"proof_hash mismatch: computed {expected_proof[:16]}..., stored {entry.proof_hash[:16]}...",
                })

        return {
            "valid": len(errors) == 0,
            "total": len(entries),
            "errors": errors,
        }

    @property
    def count(self) -> int:
        """Toplam kayıt sayısı."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM shahid_ledger")
        n = c.fetchone()[0]
        conn.close()
        return n
