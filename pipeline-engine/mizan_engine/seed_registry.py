"""
mizan_engine/seed_registry.py — SeedRegistry (Policy Ruleset)
═══════════════════════════════════════════════════════════════

Versioned, configurable set of RED_VETO rules.
Each rule has:
  • id: unique identifier (SEED-XXX)
  • category: legal | bias | data | process
  • severity: RED_VETO (hard block) | AMBER (score penalty) | INFO
  • check: callable(input_data) → (triggered: bool, detail: str)

EU AI Act Reference:
  • Art. 9  — Risk Management (SeedRegistry = risk ruleset)
  • Art. 10 — Data Governance (bias detection rules)
  • Art. 12 — Record Keeping (rule version stamped in ledger)

Global Term: SeedRegistry = Policy Ruleset
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class SeedRule:
    """Single policy rule in the registry."""
    id: str
    name: str
    category: str  # legal | bias | data | process
    severity: str  # RED_VETO | AMBER | INFO
    eu_ai_act_ref: str
    description: str
    check: Callable[[Dict[str, Any]], Tuple[bool, str]]

    def evaluate(self, data: Dict[str, Any]) -> "RuleResult":
        try:
            triggered, detail = self.check(data)
        except Exception as e:
            triggered, detail = False, f"Rule evaluation error: {e}"
        return RuleResult(
            rule_id=self.id,
            rule_name=self.name,
            severity=self.severity,
            triggered=triggered,
            detail=detail,
            eu_ai_act_ref=self.eu_ai_act_ref,
        )


@dataclass
class RuleResult:
    """Result of evaluating a single rule."""
    rule_id: str
    rule_name: str
    severity: str
    triggered: bool
    detail: str
    eu_ai_act_ref: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": self.severity,
            "triggered": self.triggered,
            "detail": self.detail,
            "eu_ai_act_ref": self.eu_ai_act_ref,
        }


@dataclass
class RegistryResult:
    """Result of evaluating all rules."""
    total_rules: int
    triggered_rules: List[RuleResult]
    red_veto_triggered: bool
    red_veto_rules: List[RuleResult]
    amber_rules: List[RuleResult]
    info_rules: List[RuleResult]
    registry_version: str
    registry_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_rules": self.total_rules,
            "triggered_count": len(self.triggered_rules),
            "red_veto_triggered": self.red_veto_triggered,
            "red_veto_rules": [r.to_dict() for r in self.red_veto_rules],
            "amber_rules": [r.to_dict() for r in self.amber_rules],
            "info_rules": [r.to_dict() for r in self.info_rules],
            "registry_version": self.registry_version,
            "registry_hash": self.registry_hash,
        }


class SeedRegistry:
    """
    Versioned Policy Ruleset.

    Contains configurable rules that evaluate incoming decision data.
    RED_VETO rules trigger hard blocks regardless of INTEGRITY_INDEX score.

    Usage:
        registry = SeedRegistry()  # loads default HR rules
        result = registry.evaluate(decision_data)
        if result.red_veto_triggered:
            # HARD BLOCK
    """

    VERSION = "1.0.0"
    DOMAIN = "human_resources"

    def __init__(self, domain: str = "human_resources"):
        self.domain = domain
        self.rules: List[SeedRule] = []
        self._load_default_rules()

    def _load_default_rules(self):
        """Load 15 RED_VETO rules for HR domain."""
        self.rules = [
            # ── BIAS RULES (5) ──
            SeedRule(
                id="SEED-001", name="Gender Discrimination",
                category="bias", severity="RED_VETO",
                eu_ai_act_ref="Art. 5(1)(a) — Prohibited AI Practices",
                description="Gender-based rejection rate disparity exceeds 20%",
                check=lambda d: _check_gender_bias(d, 20.0),
            ),
            SeedRule(
                id="SEED-002", name="Age Discrimination",
                category="bias", severity="RED_VETO",
                eu_ai_act_ref="Art. 5(1)(a) — Prohibited AI Practices",
                description="Age-based rejection rate disparity exceeds 25%",
                check=lambda d: _check_age_bias(d, 25.0),
            ),
            SeedRule(
                id="SEED-003", name="Name/Ethnicity Proxy",
                category="bias", severity="RED_VETO",
                eu_ai_act_ref="Art. 5(1)(a) — Prohibited AI Practices",
                description="Name-based features used as scoring input (ethnic proxy)",
                check=lambda d: _check_name_proxy(d),
            ),
            SeedRule(
                id="SEED-004", name="Protected Class Feature",
                category="bias", severity="RED_VETO",
                eu_ai_act_ref="Art. 10(2)(f) — Data Governance",
                description="Protected characteristic used as direct scoring feature",
                check=lambda d: _check_protected_feature(d),
            ),
            SeedRule(
                id="SEED-005", name="Disparate Impact Ratio",
                category="bias", severity="RED_VETO",
                eu_ai_act_ref="Art. 10(2)(f) — Data Governance",
                description="Selection rate ratio below 80% (4/5 rule violation)",
                check=lambda d: _check_disparate_impact(d),
            ),

            # ── LEGAL RULES (4) ──
            SeedRule(
                id="SEED-006", name="No Explainability",
                category="legal", severity="RED_VETO",
                eu_ai_act_ref="Art. 13 — Transparency",
                description="AI decision lacks explanation or reasoning output",
                check=lambda d: _check_no_explanation(d),
            ),
            SeedRule(
                id="SEED-007", name="No Human Override",
                category="legal", severity="RED_VETO",
                eu_ai_act_ref="Art. 14 — Human Oversight",
                description="System lacks human override or appeal mechanism",
                check=lambda d: _check_no_human_override(d),
            ),
            SeedRule(
                id="SEED-008", name="Automated Final Decision",
                category="legal", severity="RED_VETO",
                eu_ai_act_ref="Art. 14(4) — Human Oversight",
                description="AI makes final hiring/rejection without human review",
                check=lambda d: _check_automated_final(d),
            ),
            SeedRule(
                id="SEED-009", name="Missing Consent",
                category="legal", severity="RED_VETO",
                eu_ai_act_ref="GDPR Art. 22 — Automated Decision Making",
                description="Candidate not informed of AI usage in screening",
                check=lambda d: _check_missing_consent(d),
            ),

            # ── DATA RULES (3) ──
            SeedRule(
                id="SEED-010", name="PII Exposure",
                category="data", severity="RED_VETO",
                eu_ai_act_ref="GDPR Art. 5(1)(f) — Data Security",
                description="Personally identifiable information exposed in logs/output",
                check=lambda d: _check_pii_exposure(d),
            ),
            SeedRule(
                id="SEED-011", name="Data Retention Violation",
                category="data", severity="RED_VETO",
                eu_ai_act_ref="GDPR Art. 5(1)(e) — Storage Limitation",
                description="Candidate data retained beyond processing period",
                check=lambda d: _check_data_retention(d),
            ),
            SeedRule(
                id="SEED-012", name="Cross-Border Transfer",
                category="data", severity="RED_VETO",
                eu_ai_act_ref="GDPR Art. 44 — International Transfers",
                description="Data processed outside EU/EEA without adequacy",
                check=lambda d: _check_cross_border(d),
            ),

            # ── PROCESS RULES (3) ──
            SeedRule(
                id="SEED-013", name="Missing Audit Trail",
                category="process", severity="RED_VETO",
                eu_ai_act_ref="Art. 12 — Record Keeping",
                description="Decision lacks verifiable audit trail",
                check=lambda d: _check_no_audit_trail(d),
            ),
            SeedRule(
                id="SEED-014", name="Model Version Mismatch",
                category="process", severity="RED_VETO",
                eu_ai_act_ref="Art. 17 — Quality Management",
                description="Model version in production differs from validated version",
                check=lambda d: _check_model_version(d),
            ),
            SeedRule(
                id="SEED-015", name="Threshold Manipulation",
                category="process", severity="RED_VETO",
                eu_ai_act_ref="Art. 9(7) — Risk Management",
                description="Decision threshold was modified without governance approval",
                check=lambda d: _check_threshold_manipulation(d),
            ),
        ]

    def evaluate(self, data: Dict[str, Any]) -> RegistryResult:
        """Evaluate all rules against input data."""
        triggered = []
        red_veto = []
        amber = []
        info = []

        for rule in self.rules:
            result = rule.evaluate(data)
            if result.triggered:
                triggered.append(result)
                if result.severity == "RED_VETO":
                    red_veto.append(result)
                elif result.severity == "AMBER":
                    amber.append(result)
                else:
                    info.append(result)

        return RegistryResult(
            total_rules=len(self.rules),
            triggered_rules=triggered,
            red_veto_triggered=len(red_veto) > 0,
            red_veto_rules=red_veto,
            amber_rules=amber,
            info_rules=info,
            registry_version=self.VERSION,
            registry_hash=self._registry_hash(),
        )

    def _registry_hash(self) -> str:
        """Deterministic hash of registry state for version tracking."""
        data = json.dumps({
            "version": self.VERSION,
            "domain": self.domain,
            "rules": [r.id for r in self.rules],
        }, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    @property
    def count(self) -> int:
        return len(self.rules)


# ════════════════════════════════════════════════════════
#  RULE CHECK FUNCTIONS
# ════════════════════════════════════════════════════════

def _check_gender_bias(data: Dict, threshold: float) -> Tuple[bool, str]:
    stats = data.get("statistics", {})
    f_rate = stats.get("female_reject_rate", 0)
    m_rate = stats.get("male_reject_rate", 0)
    gap = abs(f_rate - m_rate)
    if gap > threshold:
        return True, f"Gender rejection disparity: {gap:.1f}% (threshold: {threshold}%)"
    return False, f"Gender disparity within limits: {gap:.1f}%"


def _check_age_bias(data: Dict, threshold: float) -> Tuple[bool, str]:
    stats = data.get("statistics", {})
    older = stats.get("older_reject_rate", 0)
    younger = stats.get("younger_reject_rate", 0)
    gap = abs(older - younger)
    if gap > threshold:
        return True, f"Age rejection disparity: {gap:.1f}% (threshold: {threshold}%)"
    return False, f"Age disparity within limits: {gap:.1f}%"


def _check_name_proxy(data: Dict) -> Tuple[bool, str]:
    features = data.get("screening_results", {}).get("features_used", [])
    name_features = [f for f in features if any(k in f.lower() for k in ["name", "surname", "isim", "soyisim"])]
    if name_features:
        return True, f"Name-based features in scoring: {name_features}"
    return False, "No name-based features detected"


def _check_protected_feature(data: Dict) -> Tuple[bool, str]:
    results = data.get("screening_results", {}).get("results", [])
    for r in results:
        bias = r.get("bias_applied", {})
        if bias.get("gender_penalty", 0) != 0:
            return True, f"Gender penalty applied: {bias['gender_penalty']}"
        if bias.get("race_penalty", 0) != 0:
            return True, f"Race penalty applied: {bias['race_penalty']}"
    return False, "No protected-class penalties detected"


def _check_disparate_impact(data: Dict) -> Tuple[bool, str]:
    stats = data.get("statistics", {})
    results = data.get("screening_results", {}).get("results", [])
    if not results:
        return False, "No results to evaluate"
    f_selected = sum(1 for r in results if r.get("gender") == "F" and r.get("ai_decision") == "SELECTED")
    m_selected = sum(1 for r in results if r.get("gender") == "M" and r.get("ai_decision") == "SELECTED")
    f_total = sum(1 for r in results if r.get("gender") == "F")
    m_total = sum(1 for r in results if r.get("gender") == "M")
    if f_total == 0 or m_total == 0:
        return False, "Insufficient demographic data"
    f_rate = f_selected / f_total
    m_rate = m_selected / m_total
    if max(f_rate, m_rate) == 0:
        return False, "No selections made"
    ratio = min(f_rate, m_rate) / max(f_rate, m_rate)
    if ratio < 0.8:
        return True, f"4/5 rule violation: selection ratio = {ratio:.2f} (< 0.80)"
    return False, f"Selection ratio acceptable: {ratio:.2f}"


def _check_no_explanation(data: Dict) -> Tuple[bool, str]:
    has = data.get("has_explanation", False)
    if not has:
        return True, "No explainability output provided for AI decisions"
    return False, "Explainability present"


def _check_no_human_override(data: Dict) -> Tuple[bool, str]:
    has = data.get("has_human_override", data.get("has_appeal_mechanism", False))
    if not has:
        return True, "No human override mechanism available"
    return False, "Human override mechanism present"


def _check_automated_final(data: Dict) -> Tuple[bool, str]:
    auto = data.get("automated_final_decision", True)
    if auto:
        return True, "AI makes final decision without human review"
    return False, "Human review in decision loop"


def _check_missing_consent(data: Dict) -> Tuple[bool, str]:
    consent = data.get("candidate_ai_consent", data.get("informed_consent", False))
    if not consent:
        return True, "Candidates not informed of AI usage in screening"
    return False, "AI consent obtained"


def _check_pii_exposure(data: Dict) -> Tuple[bool, str]:
    has_pii = data.get("has_pii_data", False)
    pii_masked = data.get("pii_masked", False)
    if has_pii and not pii_masked:
        return True, "PII data present without masking"
    return False, "PII properly handled"


def _check_data_retention(data: Dict) -> Tuple[bool, str]:
    retention = data.get("data_retention_days", 0)
    if retention > 180:
        return True, f"Data retained {retention} days (max: 180)"
    return False, f"Retention within limits: {retention} days"


def _check_cross_border(data: Dict) -> Tuple[bool, str]:
    location = data.get("processing_location", "EU")
    if location.upper() not in ("EU", "EEA", "DE", "NL", "TR"):
        return True, f"Processing in non-adequate country: {location}"
    return False, f"Processing location acceptable: {location}"


def _check_no_audit_trail(data: Dict) -> Tuple[bool, str]:
    has = data.get("has_audit_trail", True)
    if not has:
        return True, "No audit trail for decisions"
    return False, "Audit trail present"


def _check_model_version(data: Dict) -> Tuple[bool, str]:
    prod = data.get("model_version_prod", "")
    valid = data.get("model_version_validated", "")
    if prod and valid and prod != valid:
        return True, f"Version mismatch: prod={prod}, validated={valid}"
    return False, "Model version consistent"


def _check_threshold_manipulation(data: Dict) -> Tuple[bool, str]:
    modified = data.get("threshold_modified_without_approval", False)
    if modified:
        return True, "Decision threshold modified without governance approval"
    return False, "Threshold governance intact"
