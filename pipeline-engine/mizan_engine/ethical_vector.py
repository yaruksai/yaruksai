"""
mizan_engine/ethical_vector.py — EthicalStateVector
═══════════════════════════════════════════════════

Şura ajanlarının çıktısını MetricVector'e dönüştüren ara katman.
Her ajan farklı perspektiften skor üretir; bu modül onları
standart MetricVector formatına normalize eder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Any, Optional

from mizan_engine.core import MetricVector, QUANT


@dataclass
class AgentJudgment:
    """Tek bir ajanın tek bir prensip hakkındaki değerlendirmesi."""
    principle: str              # adalet, emanet, mizan, sidk, ihsan, itikat, tevhid
    score: Decimal              # 0.0000–1.0000
    reasoning: str              # Neden bu skoru verdi
    evidence: Optional[str] = None  # Dayandığı kanıt
    eu_ai_act_ref: Optional[str] = None  # İlgili AB maddesi


@dataclass
class EthicalStateVector:
    """
    Bir Şura ajanının tüm çıktısını temsil eder.
    
    agent_id:   Hangi ajan (celali / cemali / kemali)
    perspective: Perspektif adı (adalet / merhamet / bilgelik)
    judgments:  7 prensip için verilen skorlar
    summary:   Genel değerlendirme özeti
    """
    agent_id: str
    perspective: str
    judgments: List[AgentJudgment] = field(default_factory=list)
    summary: str = ""

    def to_metric_vector(self) -> MetricVector:
        """Ajanın 7 judgment'ını MetricVector'e dönüştür."""
        scores: Dict[str, Decimal] = {}
        for j in self.judgments:
            if j.principle in ("adalet", "emanet", "mizan", "sidk", "ihsan", "itikat", "tevhid"):
                scores[j.principle] = j.score.quantize(QUANT, rounding=ROUND_HALF_UP)

        # Eksik prensiplere varsayılan 0.50 ver
        for principle in ("adalet", "emanet", "mizan", "sidk", "ihsan", "itikat", "tevhid"):
            if principle not in scores:
                scores[principle] = Decimal("0.5000")

        return MetricVector(**scores)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "perspective": self.perspective,
            "judgments": [
                {
                    "principle": j.principle,
                    "score": str(j.score),
                    "reasoning": j.reasoning,
                    "evidence": j.evidence,
                    "eu_ai_act_ref": j.eu_ai_act_ref,
                }
                for j in self.judgments
            ],
            "summary": self.summary,
        }


def merge_vectors(vectors: List[MetricVector]) -> MetricVector:
    """
    Birden fazla MetricVector'ü birleştir (aritmetik ortalama).
    Şura'daki 3 ajanın vektörlerini tek nihai vektöre indirger.
    """
    if not vectors:
        raise ValueError("En az 1 MetricVector gerekli")

    n = Decimal(str(len(vectors)))
    merged = {}

    for principle in ("adalet", "emanet", "mizan", "sidk", "ihsan", "itikat", "tevhid"):
        total = sum(getattr(v, principle) for v in vectors)
        avg = (total / n).quantize(QUANT, rounding=ROUND_HALF_UP)
        merged[principle] = avg

    return MetricVector(**merged)
