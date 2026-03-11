"""
mizan_engine/witness_chain.py — WitnessChain (Kanıt Zinciri)
════════════════════════════════════════════════════════════

Şura ajanlarının kararlarını kronolojik sırayla zincirler.
Her WitnessEntry, önceki entry'nin hash'ini içerir → değişmezlik.

TypeScript src/evidence/types.ts'in Python karşılığı.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


WITNESS_GENESIS = "WITNESS_GENESIS_0000000000000000000000000000000000000000"


@dataclass(frozen=True)
class WitnessEntry:
    """Tek bir tanık kaydı — immutable."""
    sequence: int           # Sıra numarası
    agent_id: str           # celali / cemali / kemali / mizan_engine
    action: str             # EVALUATE / MERGE / SEAL / JUDGE
    evidence_hash: str      # İçerik hash'i
    prev_hash: str          # Önceki entry'nin hash'i
    entry_hash: str         # Bu entry'nin hash'i
    timestamp: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sequence": self.sequence,
            "agent_id": self.agent_id,
            "action": self.action,
            "evidence_hash": self.evidence_hash,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }


class WitnessChain:
    """
    Şura kararlarını kronolojik, değiştirilemez zincir olarak tutar.
    
    Kullanım:
        chain = WitnessChain()
        chain.add("celali", "EVALUATE", {"score": 0.05, ...})
        chain.add("cemali", "EVALUATE", {"score": 0.20, ...})
        chain.add("kemali", "EVALUATE", {"score": 0.40, ...})
        chain.add("mizan_engine", "SEAL", {"sigma": "0.2833", ...})
        
        chain.verify()  # → True
        chain.to_list() # → [WitnessEntry, ...]
    """

    def __init__(self):
        self._entries: List[WitnessEntry] = []

    def _compute_evidence_hash(self, payload: Dict[str, Any]) -> str:
        """Payload'ın SHA-256 hash'ini hesapla."""
        data = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def _compute_entry_hash(self, agent_id: str, action: str, evidence_hash: str, prev_hash: str, ts: str) -> str:
        """Entry hash: tüm alanların birleşimi."""
        data = f"{agent_id}|{action}|{evidence_hash}|{prev_hash}|{ts}"
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    @property
    def last_hash(self) -> str:
        """Son entry'nin hash'i veya genesis."""
        return self._entries[-1].entry_hash if self._entries else WITNESS_GENESIS

    def add(self, agent_id: str, action: str, payload: Optional[Dict[str, Any]] = None) -> WitnessEntry:
        """
        Yeni witness kaydı ekle.
        
        agent_id: celali / cemali / kemali / mizan_engine / sura_meclisi
        action: EVALUATE / MERGE / SEAL / JUDGE / EMERGENCY_STOP
        payload: İlgili veri (skor, verdict, vb.)
        """
        payload = payload or {}
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        prev_hash = self.last_hash
        evidence_hash = self._compute_evidence_hash(payload)
        entry_hash = self._compute_entry_hash(agent_id, action, evidence_hash, prev_hash, ts)

        entry = WitnessEntry(
            sequence=len(self._entries) + 1,
            agent_id=agent_id,
            action=action,
            evidence_hash=evidence_hash,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            timestamp=ts,
            payload=payload,
        )

        self._entries.append(entry)
        return entry

    def verify(self) -> bool:
        """Zincirin bütünlüğünü doğrula."""
        for i, entry in enumerate(self._entries):
            # prev_hash kontrolü
            expected_prev = self._entries[i - 1].entry_hash if i > 0 else WITNESS_GENESIS
            if entry.prev_hash != expected_prev:
                return False

            # evidence_hash kontrolü
            expected_evidence = self._compute_evidence_hash(entry.payload)
            if entry.evidence_hash != expected_evidence:
                return False

            # entry_hash kontrolü
            expected_entry = self._compute_entry_hash(
                entry.agent_id, entry.action, entry.evidence_hash, entry.prev_hash, entry.timestamp
            )
            if entry.entry_hash != expected_entry:
                return False

        return True

    def to_list(self) -> List[Dict[str, Any]]:
        """Tüm entry'leri dict listesi olarak döndür."""
        return [e.to_dict() for e in self._entries]

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def chain_hash(self) -> str:
        """Tüm zincirin birleşik hash'i."""
        if not self._entries:
            return WITNESS_GENESIS
        all_hashes = "|".join(e.entry_hash for e in self._entries)
        return hashlib.sha256(all_hashes.encode("utf-8")).hexdigest()
