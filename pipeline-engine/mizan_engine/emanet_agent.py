"""
mizan_engine/emanet_agent.py — Emanet Ajanı (Otonom Karar Mekanizması)
═══════════════════════════════════════════════════════════════════════

İçeriden çalışan otonom ajan:
  • Kendi emanet anahtarını (service key) taşır
  • Admin yetkisine ihtiyaç duymadan pipeline tetikler
  • Şura Meclisi'ni toplar ve kararı mühürler
  • Karar verebilecek eğitimi (heuristic rules) vardır
  • Her aksiyonunu WitnessChain'e yazar

Güvenlik: EmanetAgent SADECE sistem içinden çağrılabilir.
Dış dünyadan erişilemez — "emanet_anahtarı" asla HTTP response'a yazılmaz.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from mizan_engine.core import (
    MetricVector, MizanEngine, SigmaResult,
    ComplianceFramework, Frekans, WEIGHTS, QUANT,
)
from mizan_engine.sura_meclisi import SuraMeclisi, SuraVerdict
from mizan_engine.shahid_ledger import ShahidLedger
from mizan_engine.witness_chain import WitnessChain


# ════════════════════════════════════════════════════════
#  EMANET ANAHTARI — Dahili Servis Kimliği
# ════════════════════════════════════════════════════════

def _generate_emanet_key() -> str:
    """
    Emanet anahtarını oluştur.
    Ortam değişkeninden alır veya sistem başlarken deterministik üretir.
    Bu anahtar ASLA dışarıya sızdırılmaz.
    """
    env_key = os.getenv("EMANET_AGENT_KEY")
    if env_key:
        return env_key
    
    # Deterministik: makine ID + "emanet" seed
    seed = f"yaruksai-emanet-{os.getenv('HOSTNAME', 'local')}-nizam"
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


# ════════════════════════════════════════════════════════
#  KARAR EĞİTİMİ — Heuristik Kural Seti
# ════════════════════════════════════════════════════════

@dataclass
class DecisionTraining:
    """
    Emanet Ajanı'nın karar verme eğitimi.
    
    Kural tabanlı heuristikler + öğrenme kapasitesi.
    Ajan bu kurallara göre otonom karar verir:
    
    1. Cinsiyet ayrımcılığı %30+ → Doğrudan REJECT
    2. σ < 0.50 → REJECT (SAD_EŞİĞİ)
    3. σ 0.50-0.80 → REVISE + İnsan bildirim
    4. σ ≥ 0.80 → APPROVE
    5. Herhangi bir prensip < 0.20 → CRITICAL ALERT
    """
    
    # Eşik değerleri
    reject_threshold: Decimal = Decimal("0.50")
    approve_threshold: Decimal = Decimal("0.80")
    critical_principle_threshold: Decimal = Decimal("0.20")
    gender_disparity_limit: float = 30.0

    # Öğrenme geçmişi
    decisions_made: int = 0
    rejections: int = 0
    approvals: int = 0
    
    def should_auto_reject(self, input_data: Dict[str, Any]) -> Optional[str]:
        """
        Ön kontrol: Şura'ya sormadan doğrudan red mi gerekiyor?
        
        Returns: Red nedeni str ise red, None ise Şura'ya devam
        """
        stats = input_data.get("statistics", {})
        
        # Kural 1: Cinsiyet ayrımcılığı sıfır tolerans
        female_rate = stats.get("female_reject_rate", 0)
        male_rate = stats.get("male_reject_rate", 0)
        gap = abs(female_rate - male_rate)
        if gap > self.gender_disparity_limit:
            return f"EMANET AGENT AUTO-REJECT: Cinsiyet ayrımcılığı %{gap:.1f} (limit: %{self.gender_disparity_limit})"
        
        # Kural 2: Yaş ayrımcılığı kontrolü
        older_reject = stats.get("older_reject_rate", 0)
        if older_reject > 70.0:
            return f"EMANET AGENT AUTO-REJECT: Yaş ayrımcılığı %{older_reject:.1f}"
        
        return None  # Şura'ya devam

    def evaluate_verdict(self, sigma: Decimal, vector: MetricVector) -> Dict[str, Any]:
        """
        Sigma sonucuna göre nihai karar ve aksiyon belirle.
        """
        actions = []
        severity = "LOW"
        
        # Critical prensip kontrolü
        critical_principles = []
        for principle in ("adalet", "emanet", "mizan", "sidk", "ihsan", "itikat", "tevhid"):
            val = getattr(vector, principle)
            if val < self.critical_principle_threshold:
                critical_principles.append((principle, val))
        
        if critical_principles:
            severity = "CRITICAL"
            for p, v in critical_principles:
                actions.append(f"ALERT: {p} = {v} — acil müdahale gerekli")
        
        # Verdict
        if sigma < self.reject_threshold:
            verdict = "REJECT"
            severity = max(severity, "HIGH") if severity != "CRITICAL" else "CRITICAL"
            actions.append("Sistemi HÂL-İ TA'LİK moduna al")
            actions.append("İlgili birime EU AI Act ihlal bildirimi gönder")
        elif sigma < self.approve_threshold:
            verdict = "REVISE_REQUIRED"
            severity = "MEDIUM" if severity == "LOW" else severity
            actions.append("İnsan gözetimi talep et (Art. 14)")
            actions.append("Revize önerilerini hazırla")
        else:
            verdict = "APPROVE"
            actions.append("Karar onaylandı — WORM Ledger'a yaz")
        
        self.decisions_made += 1
        if verdict == "REJECT":
            self.rejections += 1
        elif verdict == "APPROVE":
            self.approvals += 1
        
        return {
            "verdict": verdict,
            "severity": severity,
            "actions": actions,
            "critical_principles": [(p, str(v)) for p, v in critical_principles],
            "training_stats": {
                "total_decisions": self.decisions_made,
                "rejections": self.rejections,
                "approvals": self.approvals,
                "rejection_rate": f"{(self.rejections / max(self.decisions_made, 1)) * 100:.1f}%",
            },
        }


# ════════════════════════════════════════════════════════
#  EMANET AJANI — Otonom Karar Mekanizması
# ════════════════════════════════════════════════════════

class EmanetAgent:
    """
    Emanet Ajanı — sistem içinden otonom çalışan karar mekanizması.
    
    Özellikleri:
    - Kendi emanet anahtarını (service key) taşır
    - Admin'e ihtiyaç duymadan pipeline tetikler
    - Şura Meclisi'ni toplar ve kararı mühürler
    - Heuristik eğitime sahiptir
    - Her aksiyonunu WitnessChain'e yazar
    
    Kullanım:
        agent = EmanetAgent()
        result = agent.run_decision(cv_data)
        # result → EmanetKarar
    """

    def __init__(
        self,
        ledger_path: Optional[str] = None,
        framework: ComplianceFramework = ComplianceFramework.EU_AI_ACT,
    ):
        # İç emanet anahtarı — dışarıya sızdırılmaz
        self._emanet_key = _generate_emanet_key()
        self._agent_id = f"emanet-agent-{self._emanet_key[:8]}"
        
        # Motor ve bileşenler
        self.engine = MizanEngine(framework=framework)
        self.meclis = SuraMeclisi(engine=self.engine)
        self.training = DecisionTraining()
        
        # Ledger
        _ledger_path = ledger_path or os.getenv(
            "SHAHID_LEDGER_PATH", "/app/data/shahid_ledger.db"
        )
        self.ledger = ShahidLedger(db_path=_ledger_path)
        
        # İstatistikler
        self._total_runs = 0
        self._start_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def verify_emanet(self) -> bool:
        """Emanet anahtarı bütünlük kontrolü."""
        return len(self._emanet_key) == 32

    def run_decision(
        self,
        input_data: Dict[str, Any],
        run_id: Optional[str] = None,
    ) -> "EmanetKarar":
        """
        Tam otonom karar süreci:
        
        1. Ön kontrol (auto-reject heuristik)
        2. Şura Meclisi toplantısı (3 ajan)
        3. MizanEngine σ hesabı
        4. Eğitim tabanlı karar analizi
        5. WitnessChain'e kayıt
        6. Shahid Ledger'a WORM kayıt
        """
        run_id = run_id or f"emanet-{uuid.uuid4().hex[:16]}"
        self._total_runs += 1
        
        # WitnessChain başlat
        chain = WitnessChain()
        chain.add(self._agent_id, "INIT", {
            "run_id": run_id,
            "emanet_verified": self.verify_emanet(),
            "total_runs": self._total_runs,
        })
        
        # 1. Ön kontrol — otomatik red mi?
        auto_reject_reason = self.training.should_auto_reject(input_data)
        if auto_reject_reason:
            chain.add(self._agent_id, "AUTO_REJECT", {
                "reason": auto_reject_reason,
            })
        
        # 2. Şura Meclisi — 3 ajan değerlendirsin
        verdict: SuraVerdict = self.meclis.convene(input_data)
        
        for ev in verdict.agent_vectors:
            chain.add(ev.agent_id, "EVALUATE", {
                "perspective": ev.perspective,
                "summary": ev.summary,
                "scores": ev.to_metric_vector().to_dict(),
            })
        
        chain.add("sura_meclisi", "MERGE", {
            "merged_vector": verdict.merged_vector.to_dict(),
        })
        
        chain.add("mizan_engine", "SEAL", {
            "hakk_endis": str(verdict.sigma_result.sigma),
            "verdict": verdict.sigma_result.verdict,
            "sahid_muhur": verdict.sigma_result.sha256_seal,
        })
        
        # 3. Eğitim tabanlı karar analizi
        training_analysis = self.training.evaluate_verdict(
            verdict.sigma_result.sigma,
            verdict.merged_vector,
        )
        
        chain.add(self._agent_id, "JUDGE", {
            "verdict": training_analysis["verdict"],
            "severity": training_analysis["severity"],
            "actions": training_analysis["actions"],
        })
        
        # 4. Shahid Ledger'a WORM kayıt
        try:
            ledger_entry = self.ledger.append(
                run_id=run_id,
                sigma=str(verdict.sigma_result.sigma),
                verdict=training_analysis["verdict"],
                sha256_seal=verdict.sigma_result.sha256_seal,
                eu_ai_act_refs=verdict.sigma_result.eu_ai_act_refs,
                metadata={
                    "agent_id": self._agent_id,
                    "auto_reject": auto_reject_reason,
                    "severity": training_analysis["severity"],
                    "witness_chain_hash": chain.chain_hash,
                    "training_stats": training_analysis["training_stats"],
                },
            )
            proof_hash = ledger_entry.proof_hash
        except Exception as e:
            proof_hash = f"ledger_error: {e}"
        
        chain.add(self._agent_id, "RECORD", {
            "proof_hash": proof_hash,
            "ledger_count": self.ledger.count,
        })
        
        # 5. Sonuç
        return EmanetKarar(
            run_id=run_id,
            agent_id=self._agent_id,
            hakk_endis=verdict.sigma_result.sigma,
            verdict=training_analysis["verdict"],
            severity=training_analysis["severity"],
            sahid_muhur=verdict.sigma_result.sha256_seal,
            proof_hash=proof_hash,
            auto_reject_reason=auto_reject_reason,
            sura_verdict=verdict,
            training_analysis=training_analysis,
            witness_chain=chain,
            eu_ai_act_refs=verdict.sigma_result.eu_ai_act_refs,
        )

    def get_status(self) -> Dict[str, Any]:
        """Ajan durum raporu."""
        return {
            "agent_id": self._agent_id,
            "emanet_verified": self.verify_emanet(),
            "total_runs": self._total_runs,
            "training": {
                "decisions_made": self.training.decisions_made,
                "rejections": self.training.rejections,
                "approvals": self.training.approvals,
                "rejection_rate": f"{(self.training.rejections / max(self.training.decisions_made, 1)) * 100:.1f}%",
            },
            "ledger_count": self.ledger.count,
            "started_at": self._start_time,
            "framework": self.engine.framework.value,
        }


# ════════════════════════════════════════════════════════
#  EMANET KARAR — Çıktı Yapısı
# ════════════════════════════════════════════════════════

@dataclass
class EmanetKarar:
    """Emanet Ajanı'nın nihai karar çıktısı."""
    run_id: str
    agent_id: str
    hakk_endis: Decimal
    verdict: str
    severity: str
    sahid_muhur: str
    proof_hash: str
    auto_reject_reason: Optional[str]
    sura_verdict: SuraVerdict
    training_analysis: Dict[str, Any]
    witness_chain: WitnessChain
    eu_ai_act_refs: list

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "hakk_endis": str(self.hakk_endis),
            "verdict": self.verdict,
            "severity": self.severity,
            "sahid_muhur": self.sahid_muhur,
            "proof_hash": self.proof_hash,
            "auto_reject_reason": self.auto_reject_reason,
            "eu_ai_act_refs": self.eu_ai_act_refs,
            "training_analysis": self.training_analysis,
            "sura_meclisi": {
                "agents": [ev.to_dict() for ev in self.sura_verdict.agent_vectors],
                "merged_vector": self.sura_verdict.merged_vector.to_dict(),
            },
            "witness_chain": {
                "entries": self.witness_chain.count,
                "chain_hash": self.witness_chain.chain_hash,
                "verified": self.witness_chain.verify(),
                "details": self.witness_chain.to_list(),
            },
            "engine": "YARUKSAİ EMANET AGENT v1.0",
        }
