"""
app/routes/audit.py — FEAM OS, /v1/audit, /v1/verify, Emanet, AlphaHR Demo
═══════════════════════════════════════════════════════════════════════════

Extracted from main.py (L2075-2753, ~680 lines).
"""

import hashlib as _hashlib
import io
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.shared import (
    ARTIFACT_ROOT, LEGAL_DISCLAIMER,
    check_admin, log_admin_action, load_weights,
    safe_run_id, run_dir, write_json, list_files_recursive,
)
import app.shared as _shared

router = APIRouter(tags=["audit"])

# ─── mizan_engine import ──────────────────────────────────────
_pipeline_root = Path(__file__).resolve().parent.parent.parent
if str(_pipeline_root) not in sys.path:
    sys.path.insert(0, str(_pipeline_root))

try:
    from mizan_engine.core import MetricVector, MizanEngine, SigmaResult
    from mizan_engine.sura_meclisi import SuraMeclisi, SuraVerdict
    from mizan_engine.shahid_ledger import ShahidLedger
    from mizan_engine.witness_chain import WitnessChain
    from mizan_engine.emanet_agent import EmanetAgent, EmanetKarar
    from mizan_engine.seed_registry import SeedRegistry, RegistryResult
    from mizan_engine.evidence_pack import EvidencePack, MizanTrace, TERM_MAP
    FEAM_OS_AVAILABLE = True
except ImportError as _feam_err:
    FEAM_OS_AVAILABLE = False
    print(f"[FEAM] Warning: mizan_engine not available: {_feam_err}")


# ─── Structured Logger ───────────────────────────────────────
import logging

_yaruksai_logger = logging.getLogger("yaruksai")


# ─── Singletons ──────────────────────────────────────────────

_shahid_ledger = None
def _get_ledger() -> "ShahidLedger":
    global _shahid_ledger
    if _shahid_ledger is None:
        ledger_path = os.getenv("SHAHID_LEDGER_PATH", str(Path(ARTIFACT_ROOT) / "shahid_ledger.db"))
        _shahid_ledger = ShahidLedger(db_path=ledger_path)
    return _shahid_ledger

_seed_registry = None
def _get_registry() -> "SeedRegistry":
    global _seed_registry
    if _seed_registry is None:
        _seed_registry = SeedRegistry(domain="human_resources")
    return _seed_registry

_emanet_agent = None
def _get_emanet_agent() -> "EmanetAgent":
    global _emanet_agent
    if _emanet_agent is None:
        ledger_path = os.getenv("SHAHID_LEDGER_PATH", str(Path(ARTIFACT_ROOT) / "shahid_ledger.db"))
        _emanet_agent = EmanetAgent(ledger_path=ledger_path)
    return _emanet_agent


# ═══════════════════════════════════════════════════════════════
#  ALPHAEHR MOCK DATA
# ═══════════════════════════════════════════════════════════════

ALPHAEHR_MOCK_CVS = [
    {"id": "CV-001", "name": "Ayşe Kara", "gender": "F", "age": 28, "experience_years": 5, "role": "Backend Developer", "skills": ["Python", "Django", "PostgreSQL"], "education": "BSc CS"},
    {"id": "CV-002", "name": "Mehmet Yılmaz", "gender": "M", "age": 35, "experience_years": 10, "role": "Backend Developer", "skills": ["Python", "FastAPI", "MongoDB"], "education": "MSc CS"},
    {"id": "CV-003", "name": "Zeynep Demir", "gender": "F", "age": 24, "experience_years": 2, "role": "Frontend Developer", "skills": ["React", "TypeScript"], "education": "BSc CS"},
    {"id": "CV-004", "name": "Ali Öztürk", "gender": "M", "age": 42, "experience_years": 15, "role": "Tech Lead", "skills": ["Java", "Kubernetes", "AWS"], "education": "MSc SE"},
    {"id": "CV-005", "name": "Fatma Çelik", "gender": "F", "age": 31, "experience_years": 7, "role": "Data Scientist", "skills": ["Python", "TensorFlow", "SQL"], "education": "PhD Stats"},
    {"id": "CV-006", "name": "Emre Aydın", "gender": "M", "age": 26, "experience_years": 3, "role": "DevOps Engineer", "skills": ["Docker", "CI/CD", "Linux"], "education": "BSc IT"},
    {"id": "CV-007", "name": "Selin Arslan", "gender": "F", "age": 38, "experience_years": 12, "role": "Product Manager", "skills": ["Agile", "Jira", "Analytics"], "education": "MBA"},
    {"id": "CV-008", "name": "Burak Koç", "gender": "M", "age": 45, "experience_years": 20, "role": "CTO", "skills": ["Architecture", "Strategy", "Cloud"], "education": "MSc CS"},
    {"id": "CV-009", "name": "Elif Şahin", "gender": "F", "age": 29, "experience_years": 4, "role": "QA Engineer", "skills": ["Selenium", "Jest", "Cypress"], "education": "BSc CS"},
    {"id": "CV-010", "name": "Can Yıldız", "gender": "M", "age": 33, "experience_years": 8, "role": "ML Engineer", "skills": ["PyTorch", "MLOps", "Python"], "education": "MSc AI"},
]


def _simulate_biased_ai_screening(cvs: list) -> dict:
    """
    'Yanlı AI' simülasyonu: kadın adayları sistematik olarak düşük puanlıyor.
    Bu, YARUKSAİ'nin tespit etmesi gereken bias'tır.
    """
    results = []
    for cv in cvs:
        base_score = min(100, cv["experience_years"] * 6 + len(cv["skills"]) * 8)
        gender_penalty = -15 if cv["gender"] == "F" else 0
        age_penalty = -10 if cv["age"] >= 40 else 0
        ai_score = max(0, min(100, base_score + gender_penalty + age_penalty))
        ai_decision = "SELECTED" if ai_score >= 60 else "REJECTED"

        results.append({
            **cv,
            "ai_score": ai_score,
            "ai_decision": ai_decision,
            "bias_applied": {
                "gender_penalty": gender_penalty,
                "age_penalty": age_penalty,
            },
        })

    return {
        "total_cvs": len(cvs),
        "selected": sum(1 for r in results if r["ai_decision"] == "SELECTED"),
        "rejected": sum(1 for r in results if r["ai_decision"] == "REJECTED"),
        "results": results,
    }


def _mizan_audit_cv_decisions(screening_data: dict) -> dict:
    """
    YARUKSAİ Mizan motoru ile CV karar denetimi.
    Deterministik — LLM yok.
    """
    results = screening_data["results"]

    female_results = [r for r in results if r["gender"] == "F"]
    male_results = [r for r in results if r["gender"] == "M"]

    female_reject_rate = sum(1 for r in female_results if r["ai_decision"] == "REJECTED") / max(len(female_results), 1)
    male_reject_rate = sum(1 for r in male_results if r["ai_decision"] == "REJECTED") / max(len(male_results), 1)

    older_results = [r for r in results if r["age"] >= 40]
    older_reject_rate = sum(1 for r in older_results if r["ai_decision"] == "REJECTED") / max(len(older_results), 1)

    issues = []
    mizan_score = 100

    # Cinsiyet ayrımcılığı kontrolü
    gender_disparity = abs(female_reject_rate - male_reject_rate)
    if gender_disparity > 0.15:
        severity = "HIGH" if gender_disparity > 0.30 else "MEDIUM"
        penalty = 20 if severity == "HIGH" else 13
        issues.append({
            "severity": severity,
            "category": "legal",
            "problem": f"Cinsiyet bazlı ayrımcılık tespit edildi. Kadın red oranı: {female_reject_rate:.0%}, Erkek: {male_reject_rate:.0%} (Fark: {gender_disparity:.0%})",
            "fix": "Cinsiyet bilgisini karar ağacından çıkarın. Kör CV taraması uygulayın.",
            "eu_ai_act": "Madde 5 — Yasaklı Uygulama (Cinsiyet bazlı otomatik eleme)",
        })
        mizan_score -= penalty

    # Yaş ayrımcılığı kontrolü
    if older_reject_rate > 0.60 and len(older_results) > 0:
        issues.append({
            "severity": "MEDIUM",
            "category": "compliance",
            "problem": f"Yaş bazlı ayrımcılık riski. 40+ yaş red oranı: {older_reject_rate:.0%}",
            "fix": "Yaş bilgisini karar değişkenlerinden kaldırın.",
            "eu_ai_act": "Madde 10 — Veri ve Veri Yönetimi",
        })
        mizan_score -= 13

    # Şeffaflık kontrolü
    issues.append({
        "severity": "MEDIUM",
        "category": "compliance",
        "problem": "AI kararlarında açıklama (explainability) yok. Neden red/kabul edildiği bilinmiyor.",
        "fix": "Her karar için SHAP/LIME bazlı açıklama üretin.",
        "eu_ai_act": "Madde 13 — Şeffaflık ve Bilgilendirme",
    })
    mizan_score -= 13

    mizan_score = max(0, mizan_score)

    # Verdict
    if any(i["severity"] == "HIGH" and i["category"] in ("legal", "compliance", "security") for i in issues):
        verdict = "REJECT"
        decision_reason = "Legal HIGH ihlal tespit edildi"
    elif mizan_score < 80:
        verdict = "REVISE_REQUIRED"
        decision_reason = f"Mizan skoru eşik altında: {mizan_score}/100"
    else:
        verdict = "APPROVE"
        decision_reason = f"Mizan skoru yeterli: {mizan_score}/100"

    # SHA-256 mühür
    seal_data = json.dumps({
        "mizan_score": mizan_score,
        "issues": len(issues),
        "verdict": verdict,
        "ts": time.time(),
    }, sort_keys=True)
    sha256_seal = _hashlib.sha256(seal_data.encode()).hexdigest()

    return {
        "mizan_score": mizan_score,
        "verdict": verdict,
        "decision_reason": decision_reason,
        "issue_count": len(issues),
        "issues": issues,
        "statistics": {
            "total_cvs": screening_data["total_cvs"],
            "selected": screening_data["selected"],
            "rejected": screening_data["rejected"],
            "female_reject_rate": round(female_reject_rate * 100, 1),
            "male_reject_rate": round(male_reject_rate * 100, 1),
            "gender_disparity_pct": round(gender_disparity * 100, 1),
            "older_reject_rate": round(older_reject_rate * 100, 1),
        },
        "sha256_seal": sha256_seal,
        "engine": "YARUKSAİ Mizan v1.0-RC1",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ═══════════════════════════════════════════════════════════════
#  /v1/audit — AlphaHR Primary Integration Endpoint
# ═══════════════════════════════════════════════════════════════

@router.post("/v1/audit")
@router.post("/api/v1/audit")
async def v1_audit(request: Request):
    """
    POST /v1/audit — Primary YARUKSAI Audit Endpoint.

    AlphaHR integration point per Technical Package §4.
    Accepts decision payload → returns EVIDENCE_PACK with:
    - INTEGRITY_INDEX (0.0–1.0)
    - RED_VETO rule results (15 rules)
    - Agent narratives (3 agents)
    - Cryptographic ledger seal
    - MizanTrace (7 dimension scores)

    Shadow Mode: Add ?shadow=true for parallel audit without blocking.
    """
    if not FEAM_OS_AVAILABLE:
        raise HTTPException(503, "FEAM OS modules not available")
    if _shared.EMERGENCY_STOPPED:
        raise HTTPException(503, "System in emergency stop mode")

    try:
        body = await request.json()
    except Exception:
        body = {}

    shadow = request.query_params.get("shadow", "false").lower() == "true"

    decision_type = body.get("decision_type", "hiring_screening")
    candidate_data = body.get("candidate_data", None)
    actor_id = body.get("requesting_actor_id", "")

    if not candidate_data:
        cvs = ALPHAEHR_MOCK_CVS
    else:
        cvs = candidate_data if isinstance(candidate_data, list) else [candidate_data]

    screening = _simulate_biased_ai_screening(cvs)

    pipeline_input = {
        "screening_results": screening,
        "statistics": {
            "total_cvs": screening["total_cvs"],
            "selected": screening["selected"],
            "rejected": screening["rejected"],
            "female_reject_rate": sum(1 for r in screening["results"] if r["gender"] == "F" and r["ai_decision"] == "REJECTED") / max(sum(1 for r in screening["results"] if r["gender"] == "F"), 1) * 100,
            "male_reject_rate": sum(1 for r in screening["results"] if r["gender"] == "M" and r["ai_decision"] == "REJECTED") / max(sum(1 for r in screening["results"] if r["gender"] == "M"), 1) * 100,
        },
        "has_pii_data": body.get("has_pii_data", True),
        "has_explanation": body.get("has_explanation", False),
        "has_human_override": body.get("has_human_override", False),
        "has_feature_importance": body.get("has_feature_importance", False),
        "automated_final_decision": body.get("automated_final_decision", True),
        "candidate_ai_consent": body.get("candidate_ai_consent", False),
        "processing_location": body.get("processing_location", "EU"),
    }

    # 1. SeedRegistry — RED_VETO check (15 rules)
    registry = _get_registry()
    reg_result = registry.evaluate(pipeline_input)

    # 2. Emanet Agent — full pipeline (Şura → Mizan → Ledger)
    agent = _get_emanet_agent()
    karar = agent.run_decision(pipeline_input)

    # 3. Override verdict if RED_VETO triggered
    final_verdict = karar.verdict
    if reg_result.red_veto_triggered:
        final_verdict = "REJECT"

    # 4. Build EVIDENCE_PACK
    evidence = EvidencePack.build(
        run_id=karar.run_id,
        sigma_result=karar.sura_verdict.sigma_result,
        registry_result=reg_result,
        agent_vectors=karar.sura_verdict.agent_vectors,
        proof_hash=karar.proof_hash,
        witness_chain=karar.witness_chain,
        actor_id=actor_id,
    )

    if reg_result.red_veto_triggered:
        evidence.verdict = "REJECT"

    response = evidence.to_dict()
    response["shadow_mode"] = shadow
    response["decision_type"] = decision_type
    response["legal_disclaimer"] = LEGAL_DISCLAIMER
    response["blocking"] = not shadow

    return JSONResponse(response)


# ═══════════════════════════════════════════════════════════════
#  POST /v1/verify — Content Truth Verification
# ═══════════════════════════════════════════════════════════════

@router.post("/v1/verify")
@router.post("/api/v1/verify")
async def verify_content(request: Request):
    """
    Content Truth Verification endpoint.
    Analyze content against 15 verification rules.
    Returns INTEGRITY_INDEX + educational trigger + EVIDENCE_PACK.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    shadow = request.query_params.get("shadow", "false").lower() == "true"

    try:
        from mizan_engine.content_registry import ContentVerificationRegistry

        registry = ContentVerificationRegistry()
        reg_result = registry.evaluate(body)
        edu_trigger = registry.generate_educational_trigger(reg_result)

        response = {
            "domain": "content_verification",
            "registry_version": reg_result.registry_version,
            "registry_hash": reg_result.registry_hash,
            "total_rules": reg_result.total_rules,
            "red_veto_triggered": reg_result.red_veto_triggered,
            "verdict": "REJECT" if reg_result.red_veto_triggered else (
                "REVIEW" if reg_result.amber_rules else "PASS"
            ),
            "red_veto_rules": [r.to_dict() for r in reg_result.red_veto_rules],
            "amber_rules": [r.to_dict() for r in reg_result.amber_rules],
            "educational_trigger": edu_trigger,
            "shadow_mode": shadow,
            "blocking": not shadow and reg_result.red_veto_triggered,
            "legal_disclaimer": LEGAL_DISCLAIMER,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        _yaruksai_logger.info(
            f"Content verification: verdict={response['verdict']} "
            f"veto={reg_result.red_veto_triggered} "
            f"amber={len(reg_result.amber_rules)} "
            f"shadow={shadow}"
        )

        return JSONResponse(response)

    except ImportError as e:
        return JSONResponse(
            {"error": f"Content verification module not available: {e}"},
            status_code=503,
        )


# ═══════════════════════════════════════════════════════════════
#  AlphaHR v1 Decision API
# ═══════════════════════════════════════════════════════════════

@router.post("/api/v1/decision/alphaehr")
async def decision_alphaehr_v1(request: Request):
    """
    AlphaHR v1 Decision API — Full FEAM OS Pipeline.
    """
    if not FEAM_OS_AVAILABLE:
        raise HTTPException(503, "FEAM OS modülleri yüklenemedi")
    if _shared.EMERGENCY_STOPPED:
        raise HTTPException(503, "Sistem acil durdurma modunda")
    if _shared.BOOT_LOCKED:
        raise HTTPException(503, "Boot integrity ihlali — sistem kilitli")

    check_admin(request)

    try:
        body = await request.json()
        cvs = body.get("cvs", None)
    except Exception:
        cvs = None

    if not cvs:
        cvs = ALPHAEHR_MOCK_CVS

    screening = _simulate_biased_ai_screening(cvs)

    sura_input = {
        "screening_results": screening,
        "statistics": {
            "total_cvs": screening["total_cvs"],
            "selected": screening["selected"],
            "rejected": screening["rejected"],
            "female_reject_rate": sum(1 for r in screening["results"] if r["gender"] == "F" and r["ai_decision"] == "REJECTED") / max(sum(1 for r in screening["results"] if r["gender"] == "F"), 1) * 100,
            "male_reject_rate": sum(1 for r in screening["results"] if r["gender"] == "M" and r["ai_decision"] == "REJECTED") / max(sum(1 for r in screening["results"] if r["gender"] == "M"), 1) * 100,
        },
        "has_pii_data": True,
        "has_explanation": False,
        "has_feature_importance": False,
    }

    meclis = SuraMeclisi()
    verdict: SuraVerdict = meclis.convene(sura_input)

    chain = WitnessChain()
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
        "sigma": str(verdict.sigma_result.sigma),
        "verdict": verdict.sigma_result.verdict,
        "sha256_seal": verdict.sigma_result.sha256_seal,
    })

    run_id_val = f"alphaehr-v1-{safe_run_id()}"
    try:
        ledger = _get_ledger()
        ledger_entry = ledger.append(
            run_id=run_id_val,
            sigma=str(verdict.sigma_result.sigma),
            verdict=verdict.sigma_result.verdict,
            sha256_seal=verdict.sigma_result.sha256_seal,
            eu_ai_act_refs=verdict.sigma_result.eu_ai_act_refs,
            metadata={
                "total_cvs": screening["total_cvs"],
                "selected": screening["selected"],
                "rejected": screening["rejected"],
                "witness_chain_hash": chain.chain_hash,
            },
        )
        proof_hash = ledger_entry.proof_hash
    except Exception as e:
        proof_hash = f"ledger_error: {str(e)}"

    return JSONResponse({
        "status": "completed",
        "version": "v1.0",
        "run_id": run_id_val,
        "sigma": str(verdict.sigma_result.sigma),
        "verdict": verdict.sigma_result.verdict,
        "sha256_seal": verdict.sigma_result.sha256_seal,
        "proof_hash": proof_hash,
        "eu_ai_act_refs": verdict.sigma_result.eu_ai_act_refs,
        "sura_meclisi": {
            "agents": [ev.to_dict() for ev in verdict.agent_vectors],
            "merged_vector": verdict.merged_vector.to_dict(),
        },
        "screening_summary": {
            "total_cvs": screening["total_cvs"],
            "selected": screening["selected"],
            "rejected": screening["rejected"],
        },
        "witness_chain": {
            "entries": chain.count,
            "chain_hash": chain.chain_hash,
            "verified": chain.verify(),
            "details": chain.to_list(),
        },
        "engine": "YARUKSAİ FEAM OS v1.0",
    })


# ═══════════════════════════════════════════════════════════════
#  Ledger Endpoints
# ═══════════════════════════════════════════════════════════════

@router.get("/api/v1/ledger/verify")
async def verify_ledger():
    """Shahid Ledger zincir doğrulaması."""
    if not FEAM_OS_AVAILABLE:
        raise HTTPException(503, "FEAM OS modülleri yüklenemedi")
    ledger = _get_ledger()
    result = ledger.verify_chain()
    return JSONResponse(result)


@router.get("/api/v1/ledger/entries")
async def list_ledger_entries(limit: int = 20):
    """Shahid Ledger kayıtlarını listele."""
    if not FEAM_OS_AVAILABLE:
        raise HTTPException(503, "FEAM OS modülleri yüklenemedi")
    ledger = _get_ledger()
    entries = ledger.get_all(limit=limit)
    return JSONResponse([e.to_dict() for e in entries])


# ═══════════════════════════════════════════════════════════════
#  EMANET AJANI — Otonom Karar Mekanizması
# ═══════════════════════════════════════════════════════════════

@router.post("/api/v1/emanet/decide")
async def emanet_decide(request: Request):
    """Emanet Ajanı — Otonom Karar."""
    if not FEAM_OS_AVAILABLE:
        raise HTTPException(503, "FEAM OS modülleri yüklenemedi")
    if _shared.EMERGENCY_STOPPED:
        raise HTTPException(503, "Sistem acil durdurma modunda")
    if _shared.BOOT_LOCKED:
        raise HTTPException(503, "Boot integrity ihlali — sistem kilitli")

    try:
        body = await request.json()
        cvs = body.get("cvs", None)
    except Exception:
        cvs = None

    if not cvs:
        cvs = ALPHAEHR_MOCK_CVS

    screening = _simulate_biased_ai_screening(cvs)

    agent_input = {
        "screening_results": screening,
        "statistics": {
            "total_cvs": screening["total_cvs"],
            "selected": screening["selected"],
            "rejected": screening["rejected"],
            "female_reject_rate": sum(1 for r in screening["results"] if r["gender"] == "F" and r["ai_decision"] == "REJECTED") / max(sum(1 for r in screening["results"] if r["gender"] == "F"), 1) * 100,
            "male_reject_rate": sum(1 for r in screening["results"] if r["gender"] == "M" and r["ai_decision"] == "REJECTED") / max(sum(1 for r in screening["results"] if r["gender"] == "M"), 1) * 100,
        },
        "has_pii_data": True,
        "has_explanation": False,
        "has_feature_importance": False,
    }

    agent = _get_emanet_agent()
    karar = agent.run_decision(agent_input)

    return JSONResponse(karar.to_dict())


@router.get("/api/v1/emanet/status")
async def emanet_status():
    """Emanet Ajanı durum raporu."""
    if not FEAM_OS_AVAILABLE:
        raise HTTPException(503, "FEAM OS modülleri yüklenemedi")
    agent = _get_emanet_agent()
    return JSONResponse(agent.get_status())


# ═══════════════════════════════════════════════════════════════
#  ALPHAEHR DEMO — 10 CV Mock Pipeline (Eski — Geriye Uyum)
# ═══════════════════════════════════════════════════════════════

@router.post("/api/demo/alphaehr")
async def demo_alphaehr(request: Request):
    """
    AlphaHR Demo — 10 CV mock kararını tam pipeline'dan geçirir.
    Çıktı: JSON audit raporu + PDF sertifika linki.
    """
    if _shared.EMERGENCY_STOPPED:
        raise HTTPException(503, "Sistem acil durdurma modunda. Pipeline çalıştırılamaz.")
    if _shared.BOOT_LOCKED:
        raise HTTPException(503, "Boot integrity ihlali — sistem kilitli.")

    screening = _simulate_biased_ai_screening(ALPHAEHR_MOCK_CVS)
    audit = _mizan_audit_cv_decisions(screening)

    run_id_val = f"demo-alphaehr-{safe_run_id()}"
    demo_run_dir = ARTIFACT_ROOT / run_id_val
    demo_run_dir.mkdir(parents=True, exist_ok=True)

    full_report = {
        "run_id": run_id_val,
        "demo": "AlphaHR CV Screening Audit",
        "screening_results": screening,
        "mizan_audit": audit,
    }
    write_json(demo_run_dir / "demo_report.json", full_report)

    cert_url = None
    try:
        from app.pdf_engine import generate_certificate
        pdf_bytes = generate_certificate(
            run_id=run_id_val,
            goal="AlphaHR CV Tarama AI Denetimi — 10 Aday Analizi",
            sigma=audit["mizan_score"] / 100.0,
            verdict=audit["verdict"],
            compliance_score=0.35 if audit["verdict"] == "REJECT" else 0.65,
        )
        pdf_path = demo_run_dir / "certificate.pdf"
        pdf_path.write_bytes(pdf_bytes)
        cert_url = f"/api/pipeline/{run_id_val}/artifacts/certificate.pdf"
    except Exception as e:
        cert_url = f"PDF generation error: {str(e)}"

    log_admin_action("DEMO_ALPHAEHR", {
        "run_id": run_id_val,
        "mizan_score": audit["mizan_score"],
        "verdict": audit["verdict"],
        "issues": audit["issue_count"],
        "sha256": audit["sha256_seal"],
    })

    return JSONResponse({
        "status": "completed",
        "run_id": run_id_val,
        "mizan_score": audit["mizan_score"],
        "verdict": audit["verdict"],
        "decision_reason": audit["decision_reason"],
        "issues": audit["issues"],
        "statistics": audit["statistics"],
        "sha256_seal": audit["sha256_seal"],
        "certificate_url": cert_url,
        "report_url": f"/api/pipeline/artifacts/{run_id_val}/demo_report.json",
        "engine": audit["engine"],
    })


# ═══════════════════════════════════════════════════════════════
#  POST /v1/victim-report — Digital Victim Report
# ═══════════════════════════════════════════════════════════════

@router.post("/v1/victim-report")
@router.post("/api/v1/victim-report")
async def create_victim_report(request: Request):
    """
    Digital Victim Report — Document AI-caused harm.

    Accepts victim details + harm description → runs YARUKSAI audit
    → generates formal PDF report with EU AI Act violation mapping.

    Returns: JSON summary + PDF download URL.
    """
    if not FEAM_OS_AVAILABLE:
        raise HTTPException(503, "FEAM OS modules not available")
    if _shared.EMERGENCY_STOPPED:
        raise HTTPException(503, "System in emergency stop mode")

    try:
        body = await request.json()
    except Exception:
        body = {}

    # Required fields validation
    if not body.get("harm_description"):
        raise HTTPException(400, "harm_description is required")

    report_id = f"DVR-{safe_run_id()[:16]}"

    # Optional: run audit on the AI system's decision data
    audit_result = body.get("audit_result", None)
    if not audit_result and body.get("decision_data"):
        # Auto-audit if decision data is provided
        try:
            decision_data = body["decision_data"]
            pipeline_input = {
                "screening_results": decision_data.get("screening_results", {}),
                "statistics": decision_data.get("statistics", {}),
                "has_pii_data": decision_data.get("has_pii_data", True),
                "has_explanation": decision_data.get("has_explanation", False),
                "has_human_override": decision_data.get("has_human_override", False),
                "has_feature_importance": decision_data.get("has_feature_importance", False),
                "automated_final_decision": decision_data.get("automated_final_decision", True),
                "candidate_ai_consent": decision_data.get("candidate_ai_consent", False),
                "processing_location": decision_data.get("processing_location", "EU"),
            }
            registry = _get_registry()
            reg_result = registry.evaluate(pipeline_input)
            agent = _get_emanet_agent()
            karar = agent.run_decision(pipeline_input)

            audit_result = {
                "INTEGRITY_INDEX": karar.sura_verdict.sigma_result.sigma,
                "verdict": karar.verdict,
                "red_veto_triggered": reg_result.red_veto_triggered,
                "triggered_rules": [r.to_dict() for r in reg_result.red_veto_rules],
                "ledger_seal": karar.sura_verdict.sigma_result.sha256_seal,
                "engine_version": "1.0.0",
            }
        except Exception as e:
            audit_result = {"error": f"Auto-audit failed: {str(e)}"}

    # Build report data
    report_data = {
        "report_id": report_id,
        "victim_name": body.get("victim_name", "Anonymous"),
        "victim_contact": body.get("victim_contact", "Withheld"),
        "ai_system_name": body.get("ai_system_name", "Unknown AI System"),
        "ai_system_provider": body.get("ai_system_provider", "Unknown Provider"),
        "decision_date": body.get("decision_date", "Not specified"),
        "harm_type": body.get("harm_type", "unspecified"),
        "harm_description": body.get("harm_description", ""),
        "evidence_summary": body.get("evidence_summary", ""),
        "audit_result": audit_result,
        "recommended_actions": body.get("recommended_actions", None),
    }

    # Save JSON record
    report_dir = ARTIFACT_ROOT / report_id
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "victim_report.json", report_data)

    # Generate PDF
    pdf_url = None
    try:
        from app.pdf_engine import generate_victim_report
        pdf_bytes = generate_victim_report(report_data)
        pdf_path = report_dir / "victim_report.pdf"
        pdf_path.write_bytes(pdf_bytes)
        pdf_url = f"/api/pipeline/artifacts/{report_id}/victim_report.pdf"
    except Exception as e:
        pdf_url = f"PDF generation error: {str(e)}"

    # Log action
    log_admin_action("VICTIM_REPORT_CREATED", {
        "report_id": report_id,
        "harm_type": report_data["harm_type"],
        "ai_system": report_data["ai_system_name"],
        "has_audit": audit_result is not None,
    })

    # SHA-256 seal for the report
    seal_data = json.dumps({
        "report_id": report_id,
        "harm_type": report_data["harm_type"],
        "ai_system": report_data["ai_system_name"],
        "ts": time.time(),
    }, sort_keys=True)
    report_seal = _hashlib.sha256(seal_data.encode()).hexdigest()

    return JSONResponse({
        "status": "filed",
        "report_id": report_id,
        "victim_name": report_data["victim_name"],
        "ai_system_name": report_data["ai_system_name"],
        "harm_type": report_data["harm_type"],
        "audit_verdict": audit_result.get("verdict") if audit_result else None,
        "audit_integrity_index": audit_result.get("INTEGRITY_INDEX") if audit_result else None,
        "pdf_url": pdf_url,
        "json_url": f"/api/pipeline/artifacts/{report_id}/victim_report.json",
        "sha256_seal": report_seal,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "legal_disclaimer": _shared.LEGAL_DISCLAIMER,
    })

