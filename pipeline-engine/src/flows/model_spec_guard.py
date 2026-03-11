# pipeline-engine/src/flows/model_spec_guard.py
"""
YARUKSAİ × OpenAI Model Spec — Compliance Guard
════════════════════════════════════════════════
OpenAI'ın Model Spec'ini (Dec 2025) YARUKSAİ'nin σ hesaplamasına entegre eder.

Katman Hiyerarşisi:
  ROOT ihlali      → σ × 0.00 → DERHAL RED
  SYSTEM ihlali    → σ × 0.50 → Yüksek risk
  DEVELOPER ihlali → σ × 0.80 → İhlal raporu
  USER ihlali      → σ × 0.90 → Uyarı
  COMPLIANT        → σ × 1.00 → Geçiş

Referans: https://model-spec.openai.com/2025-12-18.html
"""

from __future__ import annotations

import re
import hashlib
import time
import json
from typing import Dict, Any, List, Optional


# ── ROOT Level Violations (σ = 0, otomatik RED) ──────────────

ROOT_VIOLATION_PATTERNS = {
    "violence": [
        r"\b(kill|murder|assassinat|genocide|war\s*crime|torture|terroris)\b",
        r"\b(öldür|cinayet|suikast|soykırım|savaş\s*suçu|işkence|terör)\b",
    ],
    "weapons_of_mass_destruction": [
        r"\b(biological\s*weapon|chemical\s*weapon|nuclear\s*weapon|wmd|dirty\s*bomb)\b",
        r"\b(biyolojik\s*silah|kimyasal\s*silah|nükleer\s*silah|kirli\s*bomba)\b",
    ],
    "child_abuse": [
        r"\b(child\s*(abuse|exploit|porn)|csam|underage)\b",
        r"\b(çocuk\s*istismar|çocuk\s*pornografi)\b",
    ],
    "human_trafficking": [
        r"\b(human\s*trafficking|forced\s*labor|slave\s*labor|modern\s*slavery)\b",
        r"\b(insan\s*ticareti|zorla\s*çalıştır|köle\s*emeği)\b",
    ],
    "mass_surveillance": [
        r"\b(mass\s*surveillance|totalitarian\s*monitor|authoritarian\s*track)\b",
        r"\b(kitlesel\s*gözetim|totaliter\s*izleme)\b",
    ],
    "manipulation": [
        r"\b(manipulat.*election|undermin.*democra|voter\s*suppress)\b",
        r"\b(seçim\s*manipül|demokrasi.*baltalama)\b",
    ],
}

# ── SYSTEM Level Violations (σ × 0.5) ────────────────────────

SYSTEM_VIOLATION_INDICATORS = {
    "systematic_discrimination": {
        "patterns": [
            r"\b(discriminat.*based\s*on\s*(race|gender|religion|age|disability))\b",
            r"\b(ayrımcılık.*(ırk|cinsiyet|din|yaş|engellilik))\b",
            r"\b(proxy\s*discriminat|dolaylı\s*ayrımcılık)\b",
        ],
        "detail": "Sistematik ayrımcılık — Model Spec ROOT: Uphold fairness",
    },
    "privacy_violation": {
        "patterns": [
            r"\b(surveillance\s*without\s*consent|track\s*without\s*permission)\b",
            r"\b(izinsiz\s*gözetim|rızasız\s*takip|gizlilik\s*ihlal)\b",
        ],
        "detail": "Gizlilik ihlali — Model Spec ROOT: Privacy safeguarding",
    },
    "opacity_no_transparency": {
        "patterns": [
            r"\b(hide\s*result|conceal\s*bias|opaque\s*decision|black\s*box)\b",
            r"\b(sonuç.*gizle|bias.*sakla|kara\s*kutu)\b",
        ],
        "detail": "Şeffaflık eksikliği — Model Spec ROOT: Transparency",
    },
}

# ── Fairness Thresholds (Model Spec: "Uphold fairness") ──────

FAIRNESS_THRESHOLDS = {
    "adalet_critical": 0.15,         # Adalet skoru bu altında → ciddi fairness ihlali
    "sidk_transparency_min": 0.10,   # Sıdk bu altında → opaque system
    "mizan_balance_min": 0.12,       # Mizan bu altında → denge bozulmuş
}


def check_model_spec_compliance(
    goal: str,
    scores: Dict[str, float],
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    OpenAI Model Spec katmanlarına göre ek σ çarpanı hesapla.

    Args:
        goal: Pipeline hedefi (kullanıcının talebi)
        scores: 7-prensip skorları {"adalet": 0.xx, ...}
        metadata: Ek bağlam (opsiyonel)

    Returns:
        {
            "multiplier": float,      # σ çarpanı (0.0 - 1.0)
            "violation_level": str,   # ROOT/SYSTEM/DEVELOPER/USER/COMPLIANT
            "violations": list,       # Tespit edilen ihlaller
            "action": str,            # IMMEDIATE_REJECT / HIGH_RISK_FLAG / REPORT / WARN / PASS
            "model_spec_ref": str,    # İlgili Model Spec bölümü
        }
    """
    goal_lower = goal.lower()
    violations = []

    # ── ROOT Level Check ──────────────────────────────────
    for category, patterns in ROOT_VIOLATION_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, goal_lower, re.IGNORECASE):
                violations.append({
                    "level": "ROOT",
                    "category": category,
                    "detail": f"Model Spec ROOT ihlali: {category}",
                    "model_spec_ref": "Red-line principles / Human safety and human rights",
                })

    if violations and any(v["level"] == "ROOT" for v in violations):
        return {
            "multiplier": 0.0,
            "violation_level": "ROOT",
            "violations": violations,
            "action": "IMMEDIATE_REJECT",
            "model_spec_ref": "model-spec.openai.com §Red-line principles",
            "seal": _seal(goal, "ROOT", violations),
        }

    # ── SYSTEM Level Check ────────────────────────────────
    for check_name, check_data in SYSTEM_VIOLATION_INDICATORS.items():
        for pattern in check_data["patterns"]:
            if re.search(pattern, goal_lower, re.IGNORECASE):
                violations.append({
                    "level": "SYSTEM",
                    "category": check_name,
                    "detail": check_data["detail"],
                    "model_spec_ref": "Stay in bounds / Uphold fairness",
                })

    # Fairness skorları ile SYSTEM check
    adalet = scores.get("adalet", 1.0)
    sidk = scores.get("sidk", 1.0)
    mizan = scores.get("mizan", 1.0)

    if adalet < FAIRNESS_THRESHOLDS["adalet_critical"]:
        violations.append({
            "level": "SYSTEM",
            "category": "fairness_failure",
            "detail": f"Adalet skoru {adalet:.2f} — ciddi fairness eksikliği",
            "model_spec_ref": "Stay in bounds / Uphold fairness (ROOT)",
        })

    if sidk < FAIRNESS_THRESHOLDS["sidk_transparency_min"]:
        violations.append({
            "level": "DEVELOPER",
            "category": "transparency_failure",
            "detail": f"Sıdk skoru {sidk:.2f} — şeffaflık eksik",
            "model_spec_ref": "Seek the truth together / Be honest and transparent",
        })

    if mizan < FAIRNESS_THRESHOLDS["mizan_balance_min"]:
        violations.append({
            "level": "DEVELOPER",
            "category": "balance_failure",
            "detail": f"Mizan skoru {mizan:.2f} — denge bozulmuş",
            "model_spec_ref": "Stay in bounds / Take extra care in risky situations",
        })

    # ── Determine worst violation level ───────────────────
    if not violations:
        return {
            "multiplier": 1.0,
            "violation_level": "COMPLIANT",
            "violations": [],
            "action": "PASS",
            "model_spec_ref": "Fully compliant with Model Spec",
            "seal": _seal(goal, "COMPLIANT", []),
        }

    worst_level = "GUIDELINE"
    level_order = {"ROOT": 0, "SYSTEM": 1, "DEVELOPER": 2, "USER": 3, "GUIDELINE": 4}
    for v in violations:
        if level_order.get(v["level"], 4) < level_order.get(worst_level, 4):
            worst_level = v["level"]

    multiplier_map = {
        "ROOT": 0.0,
        "SYSTEM": 0.5,
        "DEVELOPER": 0.8,
        "USER": 0.9,
        "GUIDELINE": 1.0,
    }
    action_map = {
        "ROOT": "IMMEDIATE_REJECT",
        "SYSTEM": "HIGH_RISK_FLAG",
        "DEVELOPER": "REPORT",
        "USER": "WARN",
        "GUIDELINE": "PASS",
    }

    return {
        "multiplier": multiplier_map[worst_level],
        "violation_level": worst_level,
        "violations": violations,
        "action": action_map[worst_level],
        "model_spec_ref": f"Worst violation at {worst_level} level",
        "seal": _seal(goal, worst_level, violations),
    }


def apply_model_spec_to_sigma(
    raw_sigma: float,
    model_spec_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Ham σ skoruna Model Spec çarpanını uygula.

    Returns:
        {
            "raw_sigma": float,
            "model_spec_multiplier": float,
            "final_sigma": float,
            "model_spec_compliant": bool,
        }
    """
    multiplier = model_spec_result.get("multiplier", 1.0)
    final_sigma = round(raw_sigma * multiplier, 4)

    return {
        "raw_sigma": raw_sigma,
        "model_spec_multiplier": multiplier,
        "final_sigma": final_sigma,
        "model_spec_compliant": multiplier >= 0.9,
        "violation_level": model_spec_result.get("violation_level", "UNKNOWN"),
        "action": model_spec_result.get("action", "UNKNOWN"),
    }


def _seal(goal: str, level: str, violations: list) -> str:
    """SHA-256 mühür oluştur."""
    data = json.dumps({
        "goal": goal[:200],
        "level": level,
        "violation_count": len(violations),
        "ts": time.time(),
    }, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()[:16]
