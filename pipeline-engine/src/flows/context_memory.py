# src/flows/context_memory.py
"""
YARUKSAİ — ContextMemory: Stage Handoff State Store
════════════════════════════════════════════════════
Sızıntı-geçirmez durum yöneticisi.

Her stage çıktısını:
  1. Deep Copy ile izole eder
  2. SHA-256 ile mühürler
  3. Shāhid Ledger'a "DATA_HANDOFF" olarak yazar
  4. Sonraki stage'e doğrulamalı aktarır

Bütünlük zinciri kırılırsa pipeline DURUR.
"""

import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class HandoffRecord:
    """Tek bir stage handoff kaydı."""
    __slots__ = ("stage", "hash", "ts", "size_bytes", "prev_hash")

    def __init__(self, stage: str, hash_val: str, ts: float, size_bytes: int, prev_hash: Optional[str]):
        self.stage = stage
        self.hash = hash_val
        self.ts = ts
        self.size_bytes = size_bytes
        self.prev_hash = prev_hash

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "hash": self.hash,
            "ts": self.ts,
            "size_bytes": self.size_bytes,
            "prev_hash": self.prev_hash,
        }


class IntegrityError(Exception):
    """Stage veri bütünlüğü ihlali."""
    pass


class ContextMemory:
    """
    Stage'ler arası SHA-256 doğrulamalı state store.
    
    Kullanım:
        ctx = ContextMemory(artifacts_dir)
        ctx.store("architect", architect_text)
        ...
        data = ctx.retrieve("architect")  # Deep copy + hash doğrulama
        ...
        ctx.verify_chain()  # Tüm zincir kontrolü
    """

    def __init__(self, artifacts_dir: Path, ledger_callback=None):
        self._store: Dict[str, Any] = {}
        self._hashes: Dict[str, str] = {}
        self._chain: List[HandoffRecord] = []
        self._artifacts_dir = artifacts_dir
        self._chain_file = artifacts_dir / "state_chain.json"
        self._ledger_callback = ledger_callback

        # Varsa önceki zinciri yükle (resume desteği)
        if self._chain_file.exists():
            try:
                raw = json.loads(self._chain_file.read_text(encoding="utf-8"))
                for rec in raw.get("chain", []):
                    self._chain.append(HandoffRecord(
                        stage=rec["stage"], hash_val=rec["hash"],
                        ts=rec["ts"], size_bytes=rec["size_bytes"],
                        prev_hash=rec.get("prev_hash"),
                    ))
                    self._hashes[rec["stage"]] = rec["hash"]
                print(f"[CTX_MEMORY] 🔄 Zincir yüklendi: {len(self._chain)} handoff")
            except Exception as e:
                print(f"[CTX_MEMORY] ⚠️ Zincir okunamadı, sıfırdan başlanıyor: {e}")

    @staticmethod
    def _compute_hash(data: Any) -> str:
        """Veriyi deterministik JSON'a çevirip SHA-256 hash'ini üret."""
        canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _deep_copy(data: Any) -> Any:
        """Veriyi güvenli şekilde deep copy'le."""
        if isinstance(data, str):
            return data  # Immutable, copy gerekmez
        return copy.deepcopy(data)

    def store(self, stage: str, data: Any) -> str:
        """
        Stage çıktısını kaydet.
        
        Returns: SHA-256 hash of stored data
        Raises: IntegrityError on hash mismatch after store
        """
        # 1. Deep Copy — orijinal referans korunmaz
        safe_data = self._deep_copy(data)

        # 2. SHA-256 Hash
        data_hash = self._compute_hash(safe_data)

        # 3. Doğrulama — copy sonrası hash tutarlılık
        verify_hash = self._compute_hash(safe_data)
        if data_hash != verify_hash:
            raise IntegrityError(
                f"[CTX_MEMORY] ❌ STORE INTEGRITY FAILURE: {stage} — "
                f"hash mismatch after deep copy. Data may be non-deterministic."
            )

        # 4. Kaydet
        self._store[stage] = safe_data
        self._hashes[stage] = data_hash

        # 5. Zincir kaydı
        prev_hash = self._chain[-1].hash if self._chain else None
        size_bytes = len(json.dumps(safe_data, default=str).encode("utf-8"))
        record = HandoffRecord(
            stage=stage, hash_val=data_hash,
            ts=time.time(), size_bytes=size_bytes,
            prev_hash=prev_hash,
        )
        self._chain.append(record)

        # 6. Disk'e yaz
        self._save_chain()

        # 7. Ledger callback (Shāhid Ledger'a DATA_HANDOFF yaz)
        if self._ledger_callback:
            try:
                self._ledger_callback({
                    "action": "DATA_HANDOFF",
                    "details": {
                        "stage": stage,
                        "hash": data_hash,
                        "size_bytes": size_bytes,
                        "prev_hash": prev_hash,
                        "chain_length": len(self._chain),
                    }
                })
            except Exception:
                pass  # Ledger hatası pipeline'ı durdurmamalı

        print(f"[CTX_MEMORY] 💾 {stage}: {size_bytes:,} bytes | hash={data_hash[:16]}...")
        return data_hash

    def retrieve(self, stage: str) -> Any:
        """
        Stage çıktısını doğrulamalı olarak oku.
        
        Returns: Deep copy of stored data
        Raises: IntegrityError if hash doesn't match (tampering detected)
        """
        if stage not in self._store:
            raise KeyError(f"[CTX_MEMORY] ❌ Stage '{stage}' bulunamadı")

        data = self._store[stage]
        stored_hash = self._hashes[stage]

        # Doğrulama — saklanan veri değiştirilmiş mi?
        current_hash = self._compute_hash(data)
        if current_hash != stored_hash:
            raise IntegrityError(
                f"[CTX_MEMORY] ❌ TAMPERING DETECTED: {stage} — "
                f"stored hash={stored_hash[:16]}... current={current_hash[:16]}..."
            )

        # Deep Copy ile döndür — çağıran kod orijinali bozamaz
        result = self._deep_copy(data)
        print(f"[CTX_MEMORY] ✅ {stage}: doğrulandı — hash={stored_hash[:16]}...")
        return result

    def has(self, stage: str) -> bool:
        """Stage verisi mevcut mu?"""
        return stage in self._store

    def verify_chain(self) -> Dict[str, Any]:
        """
        Tüm zinciri baştan sona doğrula.
        
        Returns: Doğrulama raporu
        Raises: IntegrityError on chain break
        """
        if not self._chain:
            return {"status": "empty", "stages": 0, "verified": True}

        errors = []
        for i, record in enumerate(self._chain):
            # 1. prev_hash zincir kontrolü
            if i == 0:
                if record.prev_hash is not None:
                    errors.append(f"Genesis record has non-null prev_hash: {record.prev_hash}")
            else:
                expected_prev = self._chain[i - 1].hash
                if record.prev_hash != expected_prev:
                    errors.append(
                        f"Chain break at #{i} ({record.stage}): "
                        f"prev_hash={record.prev_hash[:16]}... != expected={expected_prev[:16]}..."
                    )

            # 2. Store'daki veri hash kontrolü (eğer hala bellekte ise)
            if record.stage in self._store:
                current_hash = self._compute_hash(self._store[record.stage])
                if current_hash != record.hash:
                    errors.append(
                        f"Data integrity violation at {record.stage}: "
                        f"chain_hash={record.hash[:16]}... != current={current_hash[:16]}..."
                    )

        report = {
            "status": "FAIL" if errors else "PASS",
            "stages": len(self._chain),
            "verified": len(errors) == 0,
            "errors": errors,
            "chain_hash": self._chain[-1].hash if self._chain else None,
            "total_bytes": sum(r.size_bytes for r in self._chain),
            "first_stage": self._chain[0].stage if self._chain else None,
            "last_stage": self._chain[-1].stage if self._chain else None,
        }

        if errors:
            print(f"[CTX_MEMORY] ❌ CHAIN VERIFICATION FAILED: {len(errors)} error(s)")
            for err in errors:
                print(f"  → {err}")
            raise IntegrityError(
                f"Context chain integrity broken: {len(errors)} error(s). "
                f"First: {errors[0]}"
            )

        print(f"[CTX_MEMORY] ✅ Chain verified: {len(self._chain)} stages, "
              f"{report['total_bytes']:,} bytes total")
        return report

    def get_handoff_log(self) -> List[dict]:
        """Tüm handoff kayıtlarını döndür."""
        return [r.to_dict() for r in self._chain]

    def restore_from_checkpoint(self, stage: str, data: Any, stored_hash: str) -> None:
        """
        Checkpoint'ten yüklenen veriyi hash doğrulamasıyla geri yükle.
        Resume senaryoları için.
        """
        safe_data = self._deep_copy(data)
        current_hash = self._compute_hash(safe_data)

        if current_hash != stored_hash:
            raise IntegrityError(
                f"[CTX_MEMORY] ❌ CHECKPOINT RESTORE FAILED: {stage} — "
                f"expected={stored_hash[:16]}... got={current_hash[:16]}..."
            )

        self._store[stage] = safe_data
        self._hashes[stage] = stored_hash
        print(f"[CTX_MEMORY] 🔄 {stage}: checkpoint'ten restore edildi — doğrulandı")

    def _save_chain(self) -> None:
        """Zinciri diske kaydet."""
        chain_data = {
            "version": "1.0.0",
            "chain": [r.to_dict() for r in self._chain],
            "updated_at": time.time(),
        }
        self._chain_file.write_text(
            json.dumps(chain_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
