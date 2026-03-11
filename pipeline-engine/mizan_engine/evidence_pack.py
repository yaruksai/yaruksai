"""
mizan_engine/evidence_pack.py — EVIDENCE_PACK Schema
═══════════════════════════════════════════════════════

Structured audit evidence bundle produced for each decision.
Contains: schema_version, engine_version, INTEGRITY_INDEX,
MizanTrace, ledger_seal, triggered rules, agent narratives.

Global Term: Audit Evidence Bundle

EU AI Act Refs:
  • Art. 12 — Record Keeping
  • Art. 13 — Transparency
  • Art. 17 — Quality Management
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from mizan_engine.core import SigmaResult, Frekans

# Ebced Sabiti — Bismillahirrahmanirrahim numerolojik değeri
# Actor_ID salt'ına gömülü manevi frekans
EBCED_BISMILLAH = 786


# ════════════════════════════════════════════════════════
#  GLOBAL TERM MAPPING
# ════════════════════════════════════════════════════════

TERM_MAP = {
    # Internal → Global
    "HAKK_ENDIS":      "INTEGRITY_INDEX",
    "WitnessChain":    "Cryptographic Audit Ledger",
    "Celali":          "Objective_Agent",
    "Cemali":          "Contextual_Agent",
    "Kemali":          "Synthesis_Agent",
    "Tevhid":          "Unified_Logic_Core",
    "7 Mana":          "7 Dimensions of Integrity",
    "MizanTrace":      "Sigma Score Trail",
    "ShuraConsole":    "Governance Dashboard",
    "SeedRegistry":    "Policy Ruleset",
    "EVIDENCE_PACK":   "Audit Evidence Bundle",
    "BootManifest":    "Integrity Boot Record",
    "SAHID_MUHUR":     "Cryptographic Seal",
    "SAD_ESIGI":       "Decision Threshold",
    "KADR_VEZIN":      "Weight Configuration",
    "ADL_VEKTOR":      "Fairness Vector",
    "REFET_PAYI":      "Mercy Offset",
    "SIDK_SILSILESI":  "Proof Chain",
    "EmanetAgent":     "Autonomous Governance Agent",
}


@dataclass
class MizanTrace:
    """Per-decision trace of weighted ethical dimension scores."""
    adalet: str    # ADL — Fairness
    emanet: str    # EMN — Data Integrity
    mizan: str     # DENGE — Balance
    sidk: str      # SIDK — Transparency
    ihsan: str     # HAYR — Beneficence
    itikat: str    # IRAD — Compliance
    tevhid: str    # AHID — Reliability
    sigma: str     # HAKK_ENDIS — INTEGRITY_INDEX
    weights: Dict[str, str] = field(default_factory=dict)  # KADR_VEZIN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimensions": {
                "fairness":      {"score": self.adalet, "internal": "ADL_VEKTÖR", "hz": Frekans.ADL_VEKTOR},
                "data_integrity": {"score": self.emanet, "internal": "EMANET", "hz": Frekans.SAHID_MUHUR},
                "balance":       {"score": self.mizan, "internal": "MİZAN", "hz": Frekans.KADR_VEZIN},
                "transparency":  {"score": self.sidk, "internal": "SIDK", "hz": Frekans.HAKK_ENDIS},
                "beneficence":   {"score": self.ihsan, "internal": "İHSAN_HATT", "hz": Frekans.REFET_PAYI},
                "compliance":    {"score": self.itikat, "internal": "KANUN_HATT", "hz": Frekans.SAD_ESIGI},
                "reliability":   {"score": self.tevhid, "internal": "DUA_HATT", "hz": Frekans.REFET_PAYI},
            },
            "integrity_index": self.sigma,
            "weight_configuration": self.weights,
        }

    @classmethod
    def from_sigma_result(cls, result: SigmaResult) -> "MizanTrace":
        v = result.vector
        return cls(
            adalet=str(v.adalet),
            emanet=str(v.emanet),
            mizan=str(v.mizan),
            sidk=str(v.sidk),
            ihsan=str(v.ihsan),
            itikat=str(v.itikat),
            tevhid=str(v.tevhid),
            sigma=str(result.sigma),
            weights=result.weights_used,
        )


@dataclass
class EvidencePack:
    """
    Structured audit evidence bundle — EVIDENCE_PACK.

    Contains all information required for EU AI Act Art. 12 compliance:
    schema_version, engine_version, decision trace, ledger seal,
    triggered rules, agent narratives, and timestamps.
    """

    schema_version: str = "1.0.0"
    engine_version: str = "1.0.0"
    event_prefix: str = "yaruksai/"

    # Decision data
    run_id: str = ""
    timestamp: str = ""
    integrity_index: str = ""
    verdict: str = ""
    decision_threshold: str = "0.50"

    # Traces
    mizan_trace: Optional[MizanTrace] = None

    # Seals
    ledger_seal: str = ""
    proof_hash: str = ""
    actor_id: str = ""  # 16-char hex via SHA-256 + Ebced salt

    # Academic Review
    academic_review_status: str = "PENDING_REVIEW"

    # Rules
    red_veto_triggered: bool = False
    triggered_rules: List[Dict] = field(default_factory=list)
    registry_version: str = ""
    registry_hash: str = ""

    # Agents
    agent_narratives: List[Dict] = field(default_factory=list)

    # EU AI Act refs
    eu_ai_act_refs: List[Dict] = field(default_factory=list)

    # Witness Chain
    witness_chain_hash: str = ""
    witness_chain_entries: int = 0
    witness_chain_verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "engine_version": self.engine_version,
            "event_prefix": self.event_prefix,

            # Decision
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "INTEGRITY_INDEX": self.integrity_index,
            "verdict": self.verdict,
            "decision_threshold": self.decision_threshold,

            # Trace
            "mizan_trace": self.mizan_trace.to_dict() if self.mizan_trace else None,

            # Seals
            "ledger_seal": self.ledger_seal,
            "proof_hash": self.proof_hash,
            "actor_id": self.actor_id,

            # Academic Review (IEEE 7000 transparency)
            "academic_review_status": self.academic_review_status,

            # Rules
            "red_veto_triggered": self.red_veto_triggered,
            "triggered_rules": self.triggered_rules,
            "registry_version": self.registry_version,
            "registry_hash": self.registry_hash,

            # Agents (global names)
            "agent_narratives": self.agent_narratives,

            # EU AI Act
            "eu_ai_act_refs": self.eu_ai_act_refs,

            # Witness Chain
            "witness_chain": {
                "chain_hash": self.witness_chain_hash,
                "entries": self.witness_chain_entries,
                "verified": self.witness_chain_verified,
            },

            # Terminology
            "term_glossary": TERM_MAP,
        }

    @classmethod
    def build(
        cls,
        run_id: str,
        sigma_result: SigmaResult,
        registry_result: Any,  # RegistryResult
        agent_vectors: list,
        proof_hash: str,
        witness_chain: Any,  # WitnessChain
        actor_id: str = "",
    ) -> "EvidencePack":
        """Build a complete EVIDENCE_PACK from pipeline outputs."""

        # Actor ID: anonymise with SHA-256 + Ebced salt (786)
        if not actor_id:
            actor_id = hashlib.sha256(
                f"yaruksai-actor-{EBCED_BISMILLAH}-{run_id}".encode()
            ).hexdigest()[:16]

        # Mizan Trace
        trace = MizanTrace.from_sigma_result(sigma_result)

        # Agent narratives with global names
        global_agent_names = {
            "celali": "Objective_Agent",
            "cemali": "Contextual_Agent",
            "kemali": "Synthesis_Agent",
        }
        narratives = []
        for ev in agent_vectors:
            agent_name = ev.agent_id if hasattr(ev, 'agent_id') else str(ev)
            narratives.append({
                "internal_name": agent_name,
                "global_name": global_agent_names.get(agent_name, agent_name),
                "perspective": ev.perspective if hasattr(ev, 'perspective') else "",
                "summary": ev.summary if hasattr(ev, 'summary') else "",
                "scores": ev.to_metric_vector().to_dict() if hasattr(ev, 'to_metric_vector') else {},
            })

        # Registry
        rr = registry_result
        triggered = [r.to_dict() for r in rr.triggered_rules] if rr else []

        return cls(
            run_id=run_id,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            integrity_index=str(sigma_result.sigma),
            verdict=sigma_result.verdict,
            mizan_trace=trace,
            ledger_seal=sigma_result.sha256_seal,
            proof_hash=proof_hash,
            actor_id=actor_id,
            red_veto_triggered=rr.red_veto_triggered if rr else False,
            triggered_rules=triggered,
            registry_version=rr.registry_version if rr else "",
            registry_hash=rr.registry_hash if rr else "",
            agent_narratives=narratives,
            eu_ai_act_refs=sigma_result.eu_ai_act_refs,
            witness_chain_hash=witness_chain.chain_hash if witness_chain else "",
            witness_chain_entries=witness_chain.count if witness_chain else 0,
            witness_chain_verified=witness_chain.verify() if witness_chain else False,
        )
