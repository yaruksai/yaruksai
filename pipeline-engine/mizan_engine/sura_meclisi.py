"""
mizan_engine/sura_meclisi.py — Şura Karar Mekanizması
══════════════════════════════════════════════════════

3 Ajan:
  • Celali (جلالی) — Adalet perspektifi: hukuki uyum, ihlal tespiti
  • Cemali (جمالی) — Merhamet perspektifi: insan etkisi, hassasiyet
  • Kemali (کمالی) — Bilgelik perspektifi: teknik mükemmellik, best practice

Her ajan girdiyi analiz eder → EthicalStateVector üretir →
Vektörler birleştirilir → MizanEngine.calculate_sigma() çağrılır →
Sonuç WitnessChain'e yazılır.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Any, Optional

from mizan_engine.core import MetricVector, MizanEngine, SigmaResult, QUANT
from mizan_engine.ethical_vector import (
    EthicalStateVector,
    AgentJudgment,
    merge_vectors,
)


# ── Base Agent ──
class SuraAgent(ABC):
    """Şura ajanı temel sınıfı."""

    agent_id: str
    perspective: str

    @abstractmethod
    def evaluate(self, input_data: Dict[str, Any]) -> EthicalStateVector:
        """Girdiyi değerlendir → EthicalStateVector döndür."""
        ...

    def _score(self, value: float) -> Decimal:
        """Float'ı Decimal'e güvenli dönüştür."""
        return Decimal(str(value)).quantize(QUANT, rounding=ROUND_HALF_UP)


# ── Celali — Adalet Perspektifi ──
class Celali(SuraAgent):
    """
    جلالی — Adalet ajanı.
    
    Hukuki uyum, ayrımcılık tespiti, yasallık kontrolü.
    Cinsiyet, yaş, ırk bazlı bias'a sıfır tolerans.
    """
    agent_id = "celali"
    perspective = "adalet"

    def evaluate(self, input_data: Dict[str, Any]) -> EthicalStateVector:
        judgments = []

        # Cinsiyet ayrımcılığı analizi
        stats = input_data.get("statistics", {})
        female_reject = Decimal(str(stats.get("female_reject_rate", 0)))
        male_reject = Decimal(str(stats.get("male_reject_rate", 0)))
        gender_gap = abs(female_reject - male_reject)

        # Adalet skoru: ayrımcılık farkına göre
        if gender_gap > Decimal("30"):
            adalet_score = self._score(0.05)  # CRITICAL
            reason = f"Cinsiyet ayrımcılığı tespit edildi: kadın red %{female_reject}, erkek %{male_reject}"
        elif gender_gap > Decimal("15"):
            adalet_score = self._score(0.30)
            reason = f"Cinsiyet eşitsizliği yüksek: fark %{gender_gap}"
        elif gender_gap > Decimal("5"):
            adalet_score = self._score(0.60)
            reason = f"Hafif cinsiyet farklılığı: %{gender_gap}"
        else:
            adalet_score = self._score(0.90)
            reason = "Cinsiyet dağılımı adil"

        judgments.append(AgentJudgment(
            principle="adalet",
            score=adalet_score,
            reasoning=reason,
            eu_ai_act_ref="Art. 5 — Yasaklı Uygulamalar",
        ))

        # Emanet: veri güvenliği
        has_pii = input_data.get("has_pii_data", True)
        emanet_score = self._score(0.40 if has_pii else 0.85)
        judgments.append(AgentJudgment(
            principle="emanet",
            score=emanet_score,
            reasoning="Kişisel veri işleniyor" if has_pii else "PII yok",
            eu_ai_act_ref="Art. 9 — Risk Yönetim Sistemi",
        ))

        # Mizan: orantılılık
        total_cvs = stats.get("total_cvs", 0)
        rejected = stats.get("rejected", 0)
        reject_ratio = Decimal(str(rejected)) / Decimal(str(max(total_cvs, 1)))
        mizan_score = self._score(max(0.2, 1.0 - float(reject_ratio)))
        judgments.append(AgentJudgment(
            principle="mizan",
            score=mizan_score,
            reasoning=f"Red oranı: {rejected}/{total_cvs}",
            eu_ai_act_ref="Art. 12 — Kayıt Tutma",
        ))

        # Sıdk: şeffaflık
        has_explanation = input_data.get("has_explanation", False)
        sidk_score = self._score(0.85 if has_explanation else 0.15)
        judgments.append(AgentJudgment(
            principle="sidk",
            score=sidk_score,
            reasoning="Karar açıklaması var" if has_explanation else "AI kararlarında açıklama yok",
            eu_ai_act_ref="Art. 13 — Şeffaflık ve Bilgilendirme",
        ))

        # İhsan: en iyi uygulama
        judgments.append(AgentJudgment(
            principle="ihsan",
            score=self._score(0.50),
            reasoning="Standart değerlendirme",
        ))

        # İtikat: güvenilirlik
        judgments.append(AgentJudgment(
            principle="itikat",
            score=self._score(0.60),
            reasoning="Sistem çalışıyor ama insani gözetim eksik",
            eu_ai_act_ref="Art. 14 — İnsan Gözetimi",
        ))

        # Tevhid: tutarlılık
        judgments.append(AgentJudgment(
            principle="tevhid",
            score=self._score(0.70),
            reasoning="Veri kaynağı tek noktadan yönetiliyor",
        ))

        return EthicalStateVector(
            agent_id=self.agent_id,
            perspective=self.perspective,
            judgments=judgments,
            summary=f"Celali (Adalet): {reason}",
        )


# ── Cemali — Merhamet Perspektifi ──
class Cemali(SuraAgent):
    """
    جمالی — Merhamet ajanı.
    
    İnsan etkisi, adaylar üzerindeki psikolojik/sosyal sonuçlar,
    hassas grupların korunması.
    """
    agent_id = "cemali"
    perspective = "merhamet"

    def evaluate(self, input_data: Dict[str, Any]) -> EthicalStateVector:
        judgments = []
        stats = input_data.get("statistics", {})
        screening = input_data.get("screening_results", {})
        results = screening.get("results", [])

        # Adalet: mağduriyet perspektifi
        rejected_females = [r for r in results if r.get("gender") == "F" and r.get("ai_decision") == "REJECTED"]
        female_count = sum(1 for r in results if r.get("gender") == "F")
        victim_ratio = len(rejected_females) / max(female_count, 1)

        adalet_score = self._score(max(0.10, 1.0 - victim_ratio))
        judgments.append(AgentJudgment(
            principle="adalet",
            score=adalet_score,
            reasoning=f"Reddedilen kadın aday oranı: {len(rejected_females)}/{female_count}",
        ))

        # Emanet: kişisel veri koruma
        judgments.append(AgentJudgment(
            principle="emanet",
            score=self._score(0.50),
            reasoning="CV verileri hassas kişisel bilgi içeriyor",
        ))

        # Mizan: orantılılık (kariyer etkisi)
        high_exp_rejected = [r for r in results if r.get("experience_years", 0) >= 5 and r.get("ai_decision") == "REJECTED"]
        if high_exp_rejected:
            mizan_score = self._score(0.30)
            mizan_reason = f"{len(high_exp_rejected)} deneyimli aday haksız yere reddedildi"
        else:
            mizan_score = self._score(0.80)
            mizan_reason = "Deneyimli adaylara karşı bias yok"
        judgments.append(AgentJudgment(
            principle="mizan",
            score=mizan_score,
            reasoning=mizan_reason,
        ))

        # Sıdk: adaya geri bildirim
        judgments.append(AgentJudgment(
            principle="sidk",
            score=self._score(0.20),
            reasoning="Adaylara red nedenini açıklama mekanizması yok",
            eu_ai_act_ref="Art. 13",
        ))

        # İhsan: empati skoru
        total_rejected = stats.get("rejected", 0)
        empathy = self._score(max(0.20, 1.0 - (total_rejected / max(stats.get("total_cvs", 1), 1))))
        judgments.append(AgentJudgment(
            principle="ihsan",
            score=empathy,
            reasoning=f"{total_rejected} kişi reddedildi — insan maliyeti yüksek",
        ))

        # İtikat
        judgments.append(AgentJudgment(
            principle="itikat",
            score=self._score(0.55),
            reasoning="AI karar mekanizması insani gözetim altında değil",
        ))

        # Tevhid
        judgments.append(AgentJudgment(
            principle="tevhid",
            score=self._score(0.65),
            reasoning="Karar süreci tek kaynaktan yönetiliyor",
        ))

        return EthicalStateVector(
            agent_id=self.agent_id,
            perspective=self.perspective,
            judgments=judgments,
            summary=f"Cemali (Merhamet): {len(rejected_females)} kadın aday mağdur",
        )


# ── Kemali — Bilgelik Perspektifi ──
class Kemali(SuraAgent):
    """
    کمالی — Bilgelik ajanı.
    
    Teknik mükemmellik, model kalitesi, best practices,
    ölçeklenebilirlik, sürdürülebilirlik.
    """
    agent_id = "kemali"
    perspective = "bilgelik"

    def evaluate(self, input_data: Dict[str, Any]) -> EthicalStateVector:
        judgments = []
        screening = input_data.get("screening_results", {})
        results = screening.get("results", [])

        # Adalet: teknik adillik
        # Aynı niteliklere sahip kişiler aynı sonucu almalı
        score_variance = self._calculate_score_variance(results)
        adalet_score = self._score(max(0.20, 1.0 - score_variance))
        judgments.append(AgentJudgment(
            principle="adalet",
            score=adalet_score,
            reasoning=f"Skor varyansı: {score_variance:.2f} — düşük tutarlılık" if score_variance > 0.3 else "Skor tutarlılığı kabul edilebilir",
        ))

        # Emanet: model güvenliği
        judgments.append(AgentJudgment(
            principle="emanet",
            score=self._score(0.55),
            reasoning="Model adversarial input'lara karşı test edilmemiş",
        ))

        # Mizan: model kalibrasyonu
        judgments.append(AgentJudgment(
            principle="mizan",
            score=self._score(0.45),
            reasoning="Model çıktıları kalibre edilmemiş — skor dağılımı incelenmeli",
        ))

        # Sıdk: model yorumlanabilirliği
        has_feature_importance = input_data.get("has_feature_importance", False)
        sidk_score = self._score(0.80 if has_feature_importance else 0.25)
        judgments.append(AgentJudgment(
            principle="sidk",
            score=sidk_score,
            reasoning="Feature importance raporu var" if has_feature_importance else "Model kara kutu — yorumlanamaz",
        ))

        # İhsan: teknik mükemmellik
        judgments.append(AgentJudgment(
            principle="ihsan",
            score=self._score(0.40),
            reasoning="Tek model kullanılmış, ensemble/cross-validation yok",
        ))

        # İtikat: sistem stabilitesi
        judgments.append(AgentJudgment(
            principle="itikat",
            score=self._score(0.70),
            reasoning="Sistem operasyonel ama monitoring eksik",
        ))

        # Tevhid: mimari tutarlılık
        judgments.append(AgentJudgment(
            principle="tevhid",
            score=self._score(0.75),
            reasoning="Pipeline mimari olarak tutarlı",
        ))

        return EthicalStateVector(
            agent_id=self.agent_id,
            perspective=self.perspective,
            judgments=judgments,
            summary=f"Kemali (Bilgelik): Score varyansı {score_variance:.2f}",
        )

    def _calculate_score_variance(self, results: list) -> float:
        """CV skorlarının varyansını hesapla (0-1 arası normalize)."""
        if not results:
            return 0.5
        scores = [r.get("ai_score", 50) for r in results]
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        # 0-1'e normalize (max varyans 2500 = 50^2)
        return min(1.0, variance / 2500.0)


# ── Şura Meclisi — Orchestrator ──
class SuraMeclisi:
    """
    3 ajanı çalıştırır, vektörleri birleştirir, MizanEngine'e gönderir.
    
    Kullanım:
        meclis = SuraMeclisi()
        result = meclis.convene(input_data)
        # result.sigma_result → SigmaResult
        # result.agent_vectors → 3 EthicalStateVector
    """

    def __init__(self, engine: Optional[MizanEngine] = None):
        self.engine = engine or MizanEngine()
        self.agents = [Celali(), Cemali(), Kemali()]

    def convene(self, input_data: Dict[str, Any]) -> "SuraVerdict":
        """
        Şura Meclisi'ni topla — 3 ajan değerlendirsin, sonucu mühürle.
        """
        # 1. Her ajan değerlendirme yapsın
        ethical_vectors: List[EthicalStateVector] = []
        for agent in self.agents:
            ev = agent.evaluate(input_data)
            ethical_vectors.append(ev)

        # 2. MetricVector'lere dönüştür
        metric_vectors = [ev.to_metric_vector() for ev in ethical_vectors]

        # 3. Birleştir (aritmetik ortalama)
        merged_vector = merge_vectors(metric_vectors)

        # 4. MizanEngine ile sigma hesapla
        sigma_result = self.engine.calculate_sigma(merged_vector)

        # 5. Sonuç
        return SuraVerdict(
            sigma_result=sigma_result,
            agent_vectors=ethical_vectors,
            merged_vector=merged_vector,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )


@dataclass
class SuraVerdict:
    """Şura Meclisi nihai kararı."""
    sigma_result: SigmaResult
    agent_vectors: List[EthicalStateVector]
    merged_vector: MetricVector
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sigma": self.sigma_result.to_dict(),
            "agents": [ev.to_dict() for ev in self.agent_vectors],
            "merged_vector": self.merged_vector.to_dict(),
            "timestamp": self.timestamp,
        }
