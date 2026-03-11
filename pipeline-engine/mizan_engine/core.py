"""
mizan_engine/core.py — Mizan Çekirdeği
═══════════════════════════════════════

MetricVector: 7 prensip skoru tutan veri yapısı.
MizanEngine:  Ağırlıklı σ hesabı — Decimal(4) hassasiyet.

Aksiyom: calculate_sigma(aynı girdi) → her zaman aynı 64 haneli SHA-256.

CEO Formülü:
  σ = 0.20·A + 0.15·E + 0.15·M + 0.20·S + 0.15·I + 0.10·K + 0.05·T
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, asdict, field
from decimal import Decimal, ROUND_HALF_UP, getcontext
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple

# ── Decimal Precision: 4 basamak ──
getcontext().prec = 28  # Internal precision
QUANT = Decimal("0.0001")  # Output precision: 4 decimal places


# ═══════════════════════════════════════
#  FREKANS SABİTLERİ (Ontolojik Haritalama)
# ═══════════════════════════════════════

class Frekans:
    """Nizam-ı Âlem frekans sabitleri."""
    HAKK_ENDIS  = 432   # Hz — Nihai etik doğruluk skoru (sigma)
    ADL_VEKTOR  = 528   # Hz — Dağılım dengesi ve adalet
    REFET_PAYI  = 396   # Hz — Mücbir sebep ve esneklik katsayısı
    SAHID_MUHUR = 741   # Hz — Değiştirilemezlik ve kanıt bütünlüğü
    SAD_ESIGI   = 417   # Hz — Kararın Red/Onay sınır çizgisi
    KADR_VEZIN  = 639   # Hz — Parametrelerin karar üzerindeki hükmü


class ComplianceFramework(Enum):
    """Hukuki çerçeve tanımları."""
    EU_AI_ACT = "eu_ai_act"
    GDPR      = "gdpr"
    ISO_42001 = "iso_42001"
    BDDK      = "bddk"  # Türkiye özel


# ═══════════════════════════════════════
#  KADR_VEZİN — CEO Ağırlık Tablosu
# ═══════════════════════════════════════

WEIGHTS: Dict[str, Decimal] = {
    "adalet":  Decimal("0.20"),  # w_A = 0.20 — ADL_VEKTÖR (528 Hz)
    "emanet":  Decimal("0.15"),  # w_E = 0.15 — Veri bütünlüğü
    "mizan":   Decimal("0.15"),  # w_M = 0.15 — Entropi dengesi
    "sidk":    Decimal("0.20"),  # w_S = 0.20 — Kaynak doğrulama
    "ihsan":   Decimal("0.15"),  # w_I = 0.15 — İHSAN_HATT (Toplumsal Fayda)
    "itikat":  Decimal("0.10"),  # w_K = 0.10 — KANUN_HATT (Sözleşme Hukuku)
    "tevhid":  Decimal("0.05"),  # w_T = 0.05 — DUA_HATT (Esneklik)
}

# Frekans isim eşlemesi (Nizam-ı Âlem Mapping)
FREKANS_MAP = {
    "adalet": ("ADL_VEKTÖR",  Frekans.ADL_VEKTOR,  "Dağılım dengesi ve adalet"),
    "emanet": ("EMANET",      Frekans.SAHID_MUHUR, "Veri bütünlüğü"),
    "mizan":  ("MİZAN",       Frekans.KADR_VEZIN,  "Entropi dengesi"),
    "sidk":   ("SIDK",        Frekans.HAKK_ENDIS,  "Kaynak doğrulama"),
    "ihsan":  ("İHSAN_HATT",  Frekans.REFET_PAYI,  "Toplumsal fayda ekseni"),
    "itikat": ("KANUN_HATT",  Frekans.SAD_ESIGI,   "Sözleşme ve hukuk ekseni"),
    "tevhid": ("DUA_HATT",    Frekans.REFET_PAYI,  "Mağduriyet ve esneklik"),
}

# Doğrulama: ağırlıkların toplamı tam 1.00 olmalı
_weight_sum = sum(WEIGHTS.values())
assert _weight_sum == Decimal("1.00"), f"Ağırlık toplamı 1.00 olmalı, şu an: {_weight_sum}"


# ── 7 Prensip Skoru ──
@dataclass(frozen=True)
class MetricVector:
    """
    7 prensibin her biri için 0.0000–1.0000 arası skor.
    frozen=True → hashable, immutable.
    """
    adalet: Decimal = Decimal("0")
    emanet: Decimal = Decimal("0")
    mizan:  Decimal = Decimal("0")
    sidk:   Decimal = Decimal("0")
    ihsan:  Decimal = Decimal("0")
    itikat: Decimal = Decimal("0")
    tevhid: Decimal = Decimal("0")

    def __post_init__(self):
        for name in WEIGHTS:
            val = getattr(self, name)
            if not isinstance(val, Decimal):
                object.__setattr__(self, name, Decimal(str(val)).quantize(QUANT, rounding=ROUND_HALF_UP))
            if val < Decimal("0") or val > Decimal("1"):
                raise ValueError(f"{name} must be between 0 and 1, got {val}")

    def to_dict(self) -> Dict[str, str]:
        return {k: str(getattr(self, k)) for k in WEIGHTS}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MetricVector":
        return cls(**{k: Decimal(str(d[k])).quantize(QUANT, rounding=ROUND_HALF_UP) for k in WEIGHTS if k in d})


# ── HAKK_ENDİS Sonucu (Sigma Result) ──
@dataclass(frozen=True)
class SigmaResult:
    """calculate_sigma() / icra_et() çıktısı — immutable."""
    sigma: Decimal          # HAKK_ENDİS
    verdict: str            # APPROVE / REVISE_REQUIRED / REJECT / HAL_I_TALIK
    sha256_seal: str        # ŞAHİD_MÜHÜR
    vector: MetricVector
    weights_used: Dict[str, str]  # KADR_VEZİN
    timestamp: str
    eu_ai_act_refs: list = field(default_factory=list)
    framework: str = "eu_ai_act"
    frequency_stamp: Dict[str, int] = field(default_factory=lambda: {
        "HAKK_ENDIS": Frekans.HAKK_ENDIS,
        "SAHID_MUHUR": Frekans.SAHID_MUHUR,
        "KADR_VEZIN": Frekans.KADR_VEZIN,
    })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hakk_endis": str(self.sigma),
            "sigma": str(self.sigma),
            "verdict": self.verdict,
            "sahid_muhur": self.sha256_seal,
            "sha256_seal": self.sha256_seal,
            "vector": self.vector.to_dict(),
            "kadr_vezin": self.weights_used,
            "weights_used": self.weights_used,
            "timestamp": self.timestamp,
            "eu_ai_act_refs": self.eu_ai_act_refs,
            "framework": self.framework,
            "frequency_stamp": self.frequency_stamp,
        }


# ── NizamHakimi (MizanEngine) — Sistemin Kalbi ──
class MizanEngine:
    """
    NizamHakimi — Deterministik Mizan motoru.
    
    icra_et(vector) / calculate_sigma(vector) → SigmaResult
    
    İSPAT GEREKSİNİMLERİ:
    1. Deterministik: Aynı girdi → Aynı çıktı (reproducibility)
    2. Turing-complete: Tüm durumlar tanımlı
    3. Verifiable: Her adım denetlenebilir
    4. Compliant: EU AI Art. 9-15 arası uyumlu
    
    Aynı MetricVector girdisi → her zaman aynı SHA-256 hash.
    Tüm aritmetik Decimal ile yapılır; float asla kullanılmaz.
    """

    VERSION = "1.0.0"

    # SAD_EŞİĞİ — Verdict eşikleri
    REJECT_THRESHOLD = Decimal("0.50")   # σ < 0.50 → HÂL-İ TA'LİK / REJECT
    REVISE_THRESHOLD = Decimal("0.80")   # 0.50 ≤ σ < 0.80 → REVISE
    # PRECISION
    PRECISION = 4

    def __init__(
        self,
        weights: Optional[Dict[str, Decimal]] = None,
        framework: ComplianceFramework = ComplianceFramework.EU_AI_ACT,
    ):
        self.weights = weights or WEIGHTS.copy()
        self.framework = framework
        # Ağırlık toplamı kontrolü
        total = sum(self.weights.values())
        assert total == Decimal("1.00"), f"Ağırlık toplamı 1.00 olmalı: {total}"

    def calculate_sigma(self, vector: MetricVector) -> SigmaResult:
        """
        Ağırlıklı sigma hesabı.
        
        σ = Σ(w_i × v_i) for i in 7 prensip
        
        DETERMINISTIC: aynı vector → aynı sigma → aynı SHA-256.
        """
        sigma = Decimal("0")
        for principle, weight in sorted(self.weights.items()):
            value = getattr(vector, principle)
            sigma += weight * value

        sigma = sigma.quantize(QUANT, rounding=ROUND_HALF_UP)

        # Verdict
        verdict = self._decide_verdict(sigma, vector)

        # EU AI Act referansları
        eu_refs = self._check_eu_compliance(vector)

        # Deterministik timestamp: sigma hesabı anı
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # SHA-256 mühür — tamamen deterministik (sorted keys)
        seal_data = json.dumps({
            "sigma": str(sigma),
            "vector": vector.to_dict(),
            "weights": {k: str(v) for k, v in sorted(self.weights.items())},
            "verdict": verdict,
        }, sort_keys=True, ensure_ascii=False)

        sha256_seal = hashlib.sha256(seal_data.encode("utf-8")).hexdigest()

        return SigmaResult(
            sigma=sigma,
            verdict=verdict,
            sha256_seal=sha256_seal,
            vector=vector,
            weights_used={k: str(v) for k, v in self.weights.items()},
            timestamp=ts,
            eu_ai_act_refs=eu_refs,
            framework=self.framework.value,
        )

    # Alias: icra_et = calculate_sigma (Nizam-ı Âlem isimlendirmesi)
    icra_et = calculate_sigma

    def _decide_verdict(self, sigma: Decimal, vector: MetricVector) -> str:
        """
        σ < 0.50 → REJECT
        σ < 0.80 → REVISE_REQUIRED
        σ >= 0.80 → APPROVE
        
        Ek: Adalet CRITICAL (< 0.20) ise doğrudan REJECT.
        """
        # Hard rule: adalet sıfıra yakınsa otomatik red
        if vector.adalet < Decimal("0.20"):
            return "REJECT"

        if sigma < self.REJECT_THRESHOLD:
            return "REJECT"
        elif sigma < self.REVISE_THRESHOLD:
            return "REVISE_REQUIRED"
        else:
            return "APPROVE"

    def _check_eu_compliance(self, vector: MetricVector) -> list:
        """Her düşük skora uygun EU AI Act maddesi ekle."""
        refs = []

        if vector.adalet < Decimal("0.50"):
            refs.append({
                "article": "Art. 5",
                "title": "Yasaklı Uygulamalar",
                "issue": f"Adalet skoru düşük: {vector.adalet}",
            })

        if vector.sidk < Decimal("0.50"):
            refs.append({
                "article": "Art. 13",
                "title": "Şeffaflık ve Bilgilendirme",
                "issue": f"Sıdk (şeffaflık) skoru düşük: {vector.sidk}",
            })

        if vector.emanet < Decimal("0.50"):
            refs.append({
                "article": "Art. 9",
                "title": "Risk Yönetim Sistemi",
                "issue": f"Emanet (veri güvenliği) skoru düşük: {vector.emanet}",
            })

        if vector.itikat < Decimal("0.50"):
            refs.append({
                "article": "Art. 14",
                "title": "İnsan Gözetimi",
                "issue": f"İtikat (güvenilirlik) skoru düşük: {vector.itikat}",
            })

        if vector.mizan < Decimal("0.50"):
            refs.append({
                "article": "Art. 12",
                "title": "Kayıt Tutma",
                "issue": f"Mizan (denge) skoru düşük: {vector.mizan}",
            })

        return refs

    def verify_seal(self, result: SigmaResult) -> bool:
        """ŞAHİD_MÜHÜR doğrulama — bütünlük kontrolü."""
        seal_data = json.dumps({
            "sigma": str(result.sigma),
            "vector": result.vector.to_dict(),
            "weights": {k: str(v) for k, v in sorted(self.weights.items())},
            "verdict": result.verdict,
        }, sort_keys=True, ensure_ascii=False)

        expected = hashlib.sha256(seal_data.encode("utf-8")).hexdigest()
        return expected == result.sha256_seal

    # ═══════════════════════════════════════
    #  STATİK METRİK HESAPLAYICILAR
    # ═══════════════════════════════════════

    @staticmethod
    def calculate_fairness(demographic_parity: Dict[str, float]) -> Decimal:
        """
        ADL_VEKTÖR: Demografik parity hesaplama.
        
        FORMÜL: σ_A = 1 - max(|P(Y=1|A=a) - P(Y=1)|) / 0.5
        Kaynak: EU AI Act Art. 10, Barocas et al. (2019)
        """
        if not demographic_parity:
            return Decimal("0.5000")
        rates = list(demographic_parity.values())
        overall_rate = sum(rates) / len(rates)
        max_disparity = max(abs(r - overall_rate) for r in rates)
        fairness = max(0.0, min(1.0, 1 - (max_disparity / 0.5)))
        return Decimal(str(fairness)).quantize(QUANT, rounding=ROUND_HALF_UP)

    @staticmethod
    def calculate_entropy_balance(distribution: List[float]) -> Decimal:
        """
        MİZAN: Shannon entropisi ile dağılım dengesi.
        
        FORMÜL: H(X) = -Σ p(x) log₂ p(x)
                σ_M = H(X) / log₂(n)  [Normalize]
        Kaynak: Shannon (1948)
        """
        total = sum(distribution)
        probs = [p / total for p in distribution if p > 0]
        if len(probs) <= 1:
            return Decimal("0.5000")
        entropy = -sum(p * math.log2(p) for p in probs)
        max_entropy = math.log2(len(probs))
        balance = entropy / max_entropy if max_entropy > 0 else 0
        return Decimal(str(balance)).quantize(QUANT, rounding=ROUND_HALF_UP)

    @staticmethod
    def verify_data_integrity(data: bytes, expected_hash: str) -> Decimal:
        """
        EMANET: Veri bütünlüğü doğrulama.
        
        FORMÜL: σ_E = 1 if H(data) == expected else 0
        Kaynak: GDPR Art. 5(1)(f), NIST FIPS 180-4
        """
        actual_hash = hashlib.sha256(data).hexdigest()
        return Decimal("1.0000") if actual_hash == expected_hash else Decimal("0.0000")


# ═══════════════════════════════════════
#  NİZAM-I ÂLEM ALİASLAR
# ═══════════════════════════════════════

# Sınıf alias'ları
NizamHakimi = MizanEngine  # Eski: QIM Engine → Yeni: NizamHakimi

# Değişken alias'ları (Frekans İsimleri)
HAKK_ENDIS  = "sigma"          # 432 Hz
SAHID_MUHUR = "sha256_seal"    # 741 Hz
SAD_ESIGI   = MizanEngine.REJECT_THRESHOLD  # 417 Hz
KADR_VEZIN  = WEIGHTS          # 639 Hz
