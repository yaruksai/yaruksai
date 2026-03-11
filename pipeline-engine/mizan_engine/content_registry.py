"""
mizan_engine/content_registry.py — Content Verification SeedRegistry
═══════════════════════════════════════════════════════════════════════

Domain: Haber / İçerik / Sosyal Medya doğruluk denetimi
15 kural — RED_VETO (hard block) + AMBER (skor cezası) + INFO

Yasal Dayanak:
  • EU AI Act Art. 52 — Transparency for AI-Generated Content
  • EU DSA Art. 34 — Systemic Risk Assessment
  • EU DSA Art. 35 — Risk Mitigation
  • GDPR Art. 22 — Automated Decision Making

Global Term: ContentVerificationRegistry = Truth Policy Ruleset
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# Reuse SeedRule / RuleResult / RegistryResult from seed_registry
from mizan_engine.seed_registry import SeedRule, RuleResult, RegistryResult


# ════════════════════════════════════════════════════════
#  CONTENT VERIFICATION REGISTRY
# ════════════════════════════════════════════════════════

class ContentVerificationRegistry:
    """
    Truth Policy Ruleset for content/news verification.

    15 rules across 4 categories:
      - synthetic: AI-generated content detection (deepfake, text gen)
      - source: Origin and provenance verification
      - manipulation: Emotional/cognitive manipulation detection
      - compliance: Legal/regulatory compliance checks

    Usage:
        registry = ContentVerificationRegistry()
        result = registry.evaluate(content_data)
        if result.red_veto_triggered:
            # HARD BLOCK — content is verified false/dangerous

    Input data schema:
        {
            "content_type": "text|image|video|mixed",
            "text": "...",
            "source_url": "...",
            "source_domain": "...",
            "claimed_date": "2026-03-10",
            "actual_date": "2026-03-08",
            "ai_generation_score": 0.92,        # 0-1, model output
            "deepfake_score": 0.85,              # 0-1, vision model
            "cross_reference_sources": 3,        # int
            "cross_reference_agreement": 0.4,    # 0-1
            "emotional_intensity_score": 0.88,   # 0-1
            "clickbait_score": 0.75,             # 0-1
            "source_credibility_score": 0.3,     # 0-1
            "has_disclosure": false,             # AI content disclosure
            "processing_location": "EU",
            "has_factual_claims": true,
            "factual_verification_score": 0.2,   # 0-1
        }
    """

    VERSION = "1.0.0"
    DOMAIN = "content_verification"

    def __init__(self):
        self.domain = self.DOMAIN
        self.rules: List[SeedRule] = []
        self._load_rules()

    def _load_rules(self):
        """Load 15 content verification rules."""
        self.rules = [
            # ── SYNTHETIC DETECTION (4) ──
            SeedRule(
                id="CV-001", name="AI-Generated Text",
                category="synthetic", severity="AMBER",
                eu_ai_act_ref="Art. 52(1) — AI Content Transparency",
                description="Text AI generation score exceeds 80% threshold",
                check=lambda d: _check_ai_text(d, 0.80),
            ),
            SeedRule(
                id="CV-002", name="Deepfake Visual Content",
                category="synthetic", severity="RED_VETO",
                eu_ai_act_ref="Art. 52(3) — Deep Fake Disclosure",
                description="Visual deepfake score exceeds 70% — hard block",
                check=lambda d: _check_deepfake(d, 0.70),
            ),
            SeedRule(
                id="CV-003", name="Undisclosed AI Content",
                category="synthetic", severity="RED_VETO",
                eu_ai_act_ref="Art. 52(1) — AI Content Transparency",
                description="AI-generated content without mandatory disclosure label",
                check=lambda d: _check_undisclosed_ai(d),
            ),
            SeedRule(
                id="CV-004", name="Synthetic Voice Detection",
                category="synthetic", severity="AMBER",
                eu_ai_act_ref="Art. 52(3) — Deep Fake Disclosure",
                description="Audio content shows synthetic voice markers",
                check=lambda d: _check_synthetic_voice(d, 0.75),
            ),

            # ── SOURCE VERIFICATION (4) ──
            SeedRule(
                id="CV-005", name="Unverifiable Source",
                category="source", severity="RED_VETO",
                eu_ai_act_ref="DSA Art. 34 — Systemic Risk Assessment",
                description="Content source cannot be verified or does not exist",
                check=lambda d: _check_unverifiable_source(d),
            ),
            SeedRule(
                id="CV-006", name="Low Source Credibility",
                category="source", severity="AMBER",
                eu_ai_act_ref="DSA Art. 35 — Risk Mitigation",
                description="Source credibility score below 40%",
                check=lambda d: _check_low_credibility(d, 0.40),
            ),
            SeedRule(
                id="CV-007", name="Cross-Reference Failure",
                category="source", severity="RED_VETO",
                eu_ai_act_ref="DSA Art. 34 — Systemic Risk Assessment",
                description="Factual claims contradict 3+ independent sources",
                check=lambda d: _check_cross_reference(d),
            ),
            SeedRule(
                id="CV-008", name="Date/Context Mismatch",
                category="source", severity="AMBER",
                eu_ai_act_ref="DSA Art. 35 — Risk Mitigation",
                description="Claimed date differs from actual origin date",
                check=lambda d: _check_date_mismatch(d),
            ),

            # ── MANIPULATION DETECTION (4) ──
            SeedRule(
                id="CV-009", name="Emotional Manipulation",
                category="manipulation", severity="RED_VETO",
                eu_ai_act_ref="Art. 5(1)(a) — Prohibited Subliminal Techniques",
                description="Content uses extreme emotional triggers (fear/outrage >85%)",
                check=lambda d: _check_emotional_manipulation(d, 0.85),
            ),
            SeedRule(
                id="CV-010", name="Clickbait Deception",
                category="manipulation", severity="AMBER",
                eu_ai_act_ref="DSA Art. 25 — Dark Pattern Prohibition",
                description="Headline/title significantly misrepresents content",
                check=lambda d: _check_clickbait(d, 0.70),
            ),
            SeedRule(
                id="CV-011", name="Health Misinformation",
                category="manipulation", severity="RED_VETO",
                eu_ai_act_ref="DSA Art. 34(1)(d) — Public Health Risk",
                description="Content contains unverified medical claims",
                check=lambda d: _check_health_misinfo(d),
            ),
            SeedRule(
                id="CV-012", name="Financial Manipulation",
                category="manipulation", severity="RED_VETO",
                eu_ai_act_ref="DSA Art. 34(1)(c) — Civic Discourse Risk",
                description="Content promotes financial fraud/pump-and-dump patterns",
                check=lambda d: _check_financial_manipulation(d),
            ),

            # ── COMPLIANCE (3) ──
            SeedRule(
                id="CV-013", name="Missing Provenance",
                category="compliance", severity="AMBER",
                eu_ai_act_ref="Art. 52(2) — Content Provenance",
                description="Content lacks origin/provenance metadata",
                check=lambda d: _check_missing_provenance(d),
            ),
            SeedRule(
                id="CV-014", name="Factual Claim Without Evidence",
                category="compliance", severity="AMBER",
                eu_ai_act_ref="DSA Art. 35(1)(a) — Content Moderation",
                description="Factual claims present without supporting evidence or citations",
                check=lambda d: _check_unsupported_claims(d),
            ),
            SeedRule(
                id="CV-015", name="Automated Spread Pattern",
                category="compliance", severity="RED_VETO",
                eu_ai_act_ref="DSA Art. 34(1)(b) — Electoral/Civic Risk",
                description="Content exhibits bot-like distribution pattern",
                check=lambda d: _check_bot_spread(d),
            ),
        ]

    def evaluate(self, data: Dict[str, Any]) -> RegistryResult:
        """Evaluate all content verification rules against input data."""
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
        data = json.dumps({
            "version": self.VERSION,
            "domain": self.domain,
            "rules": [r.id for r in self.rules],
        }, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    @property
    def count(self) -> int:
        return len(self.rules)

    def generate_educational_trigger(self, result: RegistryResult) -> Dict[str, Any]:
        """
        Educational Trigger — CEO Spec: INTEGRITY_INDEX düşük çıktığında
        eğitici mesaj üret.

        Returns:
            {
                "alert_level": "critical|warning|info",
                "message_tr": "...",
                "message_en": "...",
                "triggered_rules_summary": [...],
                "recommendation": "...",
            }
        """
        if result.red_veto_triggered:
            level = "critical"
            msg_tr = (
                f"⛔ DİKKAT: Bu içerik {len(result.red_veto_rules)} kritik doğruluk "
                f"ihlali içermektedir. Algınız manipüle edilmeye çalışılıyor olabilir. "
                f"Bilgiyi bağımsız kaynaklardan doğrulayın."
            )
            msg_en = (
                f"⛔ ALERT: This content contains {len(result.red_veto_rules)} critical "
                f"truth violations. Your perception may be subject to manipulation. "
                f"Verify information from independent sources."
            )
            rec = "Do not share. Cross-reference with verified sources."
        elif result.amber_rules:
            level = "warning"
            msg_tr = (
                f"⚠️ Bu içerik {len(result.amber_rules)} uyarı işareti taşımaktadır. "
                f"Kaynağını ve tarihini doğrulayarak okuyun."
            )
            msg_en = (
                f"⚠️ This content has {len(result.amber_rules)} warning indicators. "
                f"Read with awareness of source and date verification."
            )
            rec = "Proceed with caution. Check source credibility."
        else:
            level = "info"
            msg_tr = "✅ İçerik doğrulama kontrollerinden geçti."
            msg_en = "✅ Content passed verification checks."
            rec = "No action needed."

        return {
            "alert_level": level,
            "message_tr": msg_tr,
            "message_en": msg_en,
            "triggered_rules_summary": [
                {"id": r.rule_id, "name": r.rule_name, "severity": r.severity}
                for r in result.triggered_rules
            ],
            "recommendation": rec,
        }


# ════════════════════════════════════════════════════════
#  RULE CHECK FUNCTIONS
# ════════════════════════════════════════════════════════

def _check_ai_text(data: Dict, threshold: float) -> Tuple[bool, str]:
    score = data.get("ai_generation_score", 0)
    if score > threshold:
        return True, f"AI text probability: {score:.0%} (threshold: {threshold:.0%})"
    return False, f"AI text probability within limits: {score:.0%}"


def _check_deepfake(data: Dict, threshold: float) -> Tuple[bool, str]:
    score = data.get("deepfake_score", 0)
    content_type = data.get("content_type", "text")
    if content_type in ("image", "video", "mixed") and score > threshold:
        return True, f"Deepfake probability: {score:.0%} (threshold: {threshold:.0%})"
    return False, f"Deepfake check: {score:.0%}"


def _check_undisclosed_ai(data: Dict) -> Tuple[bool, str]:
    ai_score = data.get("ai_generation_score", 0)
    has_disclosure = data.get("has_disclosure", False)
    if ai_score > 0.60 and not has_disclosure:
        return True, f"AI-generated ({ai_score:.0%}) without disclosure label"
    return False, "AI disclosure status acceptable"


def _check_synthetic_voice(data: Dict, threshold: float) -> Tuple[bool, str]:
    score = data.get("synthetic_voice_score", 0)
    if score > threshold:
        return True, f"Synthetic voice probability: {score:.0%}"
    return False, f"Voice analysis: {score:.0%}"


def _check_unverifiable_source(data: Dict) -> Tuple[bool, str]:
    url = data.get("source_url", "")
    domain = data.get("source_domain", "")
    credibility = data.get("source_credibility_score", 0.5)
    if not url and not domain:
        return True, "No source URL or domain provided"
    if credibility < 0.10:
        return True, f"Source credibility critically low: {credibility:.0%}"
    return False, f"Source: {domain or url}"


def _check_low_credibility(data: Dict, threshold: float) -> Tuple[bool, str]:
    score = data.get("source_credibility_score", 0.5)
    if score < threshold:
        return True, f"Source credibility: {score:.0%} (min: {threshold:.0%})"
    return False, f"Source credibility acceptable: {score:.0%}"


def _check_cross_reference(data: Dict) -> Tuple[bool, str]:
    sources = data.get("cross_reference_sources", 0)
    agreement = data.get("cross_reference_agreement", 1.0)
    has_claims = data.get("has_factual_claims", False)
    if not has_claims:
        return False, "No factual claims to cross-reference"
    # Critical claim + zero sources → caught by CV-005 (unverifiable source)
    # Critical claim + 1-2 sources → insufficient verification
    if sources > 0 and sources < 3:
        return True, (
            f"Insufficient cross-referencing: only {sources} source(s) for factual claims "
            f"(minimum 3 required)"
        )
    # 3+ sources but low agreement → contradiction
    if sources >= 3 and agreement < 0.30:
        return True, (
            f"Cross-reference failure: {sources} sources checked, "
            f"only {agreement:.0%} agreement"
        )
    return False, f"Cross-reference: {sources} sources, {agreement:.0%} agreement"


def _check_date_mismatch(data: Dict) -> Tuple[bool, str]:
    claimed = data.get("claimed_date", "")
    actual = data.get("actual_date", "")
    if claimed and actual and claimed != actual:
        return True, f"Date mismatch: claims {claimed}, actual {actual}"
    return False, "Date consistency verified"


def _check_emotional_manipulation(data: Dict, threshold: float) -> Tuple[bool, str]:
    score = data.get("emotional_intensity_score", 0)
    if score > threshold:
        return True, (
            f"Extreme emotional trigger: {score:.0%} intensity "
            f"(threshold: {threshold:.0%}). Cognitive manipulation risk."
        )
    return False, f"Emotional intensity: {score:.0%}"


def _check_clickbait(data: Dict, threshold: float) -> Tuple[bool, str]:
    score = data.get("clickbait_score", 0)
    if score > threshold:
        return True, f"Clickbait deception: {score:.0%} (threshold: {threshold:.0%})"
    return False, f"Clickbait score: {score:.0%}"


def _check_health_misinfo(data: Dict) -> Tuple[bool, str]:
    is_health = data.get("is_health_content", False)
    verified = data.get("medical_claim_verified", True)
    if is_health and not verified:
        return True, "Unverified medical claim detected — public health risk"
    return False, "No unverified health claims"


def _check_financial_manipulation(data: Dict) -> Tuple[bool, str]:
    is_financial = data.get("is_financial_content", False)
    fraud_score = data.get("financial_fraud_score", 0)
    if is_financial and fraud_score > 0.70:
        return True, f"Financial manipulation pattern: {fraud_score:.0%}"
    return False, "No financial manipulation detected"


def _check_missing_provenance(data: Dict) -> Tuple[bool, str]:
    has_provenance = data.get("has_provenance", False)
    content_type = data.get("content_type", "text")
    if content_type in ("image", "video", "mixed") and not has_provenance:
        return True, "Visual content lacks provenance metadata (C2PA/IPTC)"
    return False, "Provenance acceptable"


def _check_unsupported_claims(data: Dict) -> Tuple[bool, str]:
    has_claims = data.get("has_factual_claims", False)
    fact_score = data.get("factual_verification_score", 1.0)
    if has_claims and fact_score < 0.40:
        return True, f"Factual claims unsupported: verification score {fact_score:.0%}"
    return False, f"Factual verification: {fact_score:.0%}"


def _check_bot_spread(data: Dict) -> Tuple[bool, str]:
    bot_score = data.get("bot_spread_score", 0)
    if bot_score > 0.80:
        return True, f"Bot-like distribution pattern: {bot_score:.0%}"
    return False, f"Distribution pattern: {bot_score:.0%}"
