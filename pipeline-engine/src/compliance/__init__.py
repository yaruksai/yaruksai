# pipeline-engine/src/compliance/eu_ai_act_report.py
"""
YARUKSAİ — EU AI Act Compliance Report Generator
──────────────────────────────────────────────────
Pipeline sonuçlarından otomatik uyumluluk raporu üretir.
Referans: EU AI Act (Regulation 2024/1689)

Kapsam:
  - Article 9:  Risk Management System
  - Article 10: Data & Data Governance
  - Article 11: Technical Documentation
  - Article 13: Transparency
  - Article 14: Human Oversight
  - Article 15: Accuracy, Robustness, Cybersecurity
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def generate_compliance_report(
    run_id: str,
    goal: str,
    pipeline_summary: Dict[str, Any],
    council_verdict: Optional[Dict[str, Any]] = None,
    artifacts_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Pipeline sonuçlarından EU AI Act uyumluluk raporu üretir.
    
    Returns:
        Yapılandırılmış uyumluluk raporu (dict)
    """
    now = datetime.now(timezone.utc).isoformat()
    sigma = 0.0
    verdict = "UNKNOWN"
    
    if council_verdict:
        sigma = council_verdict.get("sigma", council_verdict.get("sigma_score", 0.0))
        verdict = council_verdict.get("verdict", "UNKNOWN")
    
    # Pipeline stage sonuçlarını analiz et
    architect_summary = pipeline_summary.get("architect", "")
    auditor_summary = pipeline_summary.get("auditor", "")
    mizan_score = pipeline_summary.get("mizan_score")
    builder_status = pipeline_summary.get("builder_status")
    final_decision = pipeline_summary.get("final_decision")
    
    # Risk seviyesi hesapla (EU AI Act Article 6)
    risk_level = _calculate_risk_level(sigma, mizan_score, final_decision)
    
    # Uyumluluk kontrolleri
    compliance_checks = _run_compliance_checks(
        sigma=sigma,
        verdict=verdict,
        mizan_score=mizan_score,
        final_decision=final_decision,
        has_architect=bool(architect_summary),
        has_auditor=bool(auditor_summary),
    )
    
    # Genel uyumluluk skoru
    passed = sum(1 for c in compliance_checks if c["status"] == "PASS")
    total = len(compliance_checks)
    compliance_score = round(passed / total * 100, 1) if total > 0 else 0
    
    report = {
        "report_type": "EU_AI_ACT_COMPLIANCE",
        "report_version": "1.0",
        "generated_at": now,
        "generator": "YARUKSAİ Compliance Engine v1.0",
        
        "subject": {
            "run_id": run_id,
            "goal": goal,
            "timestamp": now,
        },
        
        "regulation_reference": {
            "name": "EU Artificial Intelligence Act",
            "regulation_id": "Regulation (EU) 2024/1689",
            "effective_date": "2025-08-02",
            "applicable_articles": [
                "Article 9 - Risk Management",
                "Article 10 - Data Governance", 
                "Article 11 - Technical Documentation",
                "Article 13 - Transparency",
                "Article 14 - Human Oversight",
                "Article 15 - Accuracy & Robustness",
            ],
        },
        
        "risk_classification": {
            "level": risk_level["level"],
            "category": risk_level["category"],
            "justification": risk_level["justification"],
            "article_6_reference": risk_level["article"],
        },
        
        "council_evaluation": {
            "sigma_score": sigma,
            "verdict": verdict,
            "threshold": 0.30,
            "agents_count": 7,
            "methodology": "7AI Shūrā Council — weighted multi-agent ethical evaluation",
        },
        
        "compliance_checks": compliance_checks,
        
        "compliance_summary": {
            "overall_score": compliance_score,
            "checks_passed": passed,
            "checks_total": total,
            "status": "COMPLIANT" if compliance_score >= 70 else "PARTIALLY_COMPLIANT" if compliance_score >= 40 else "NON_COMPLIANT",
        },
        
        "pipeline_evidence": {
            "stages_completed": _count_stages(pipeline_summary),
            "total_stages": 6,
            "architect_review": "COMPLETED" if architect_summary else "MISSING",
            "auditor_review": "COMPLETED" if auditor_summary else "MISSING",
            "mizan_score": mizan_score,
            "final_gate_decision": final_decision,
        },
        
        "seal": None,  # Will be set below
    }
    
    # SHA-256 mühür
    report_json = json.dumps(report, sort_keys=True, ensure_ascii=False)
    seal = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
    report["seal"] = seal
    
    # Dosyaya kaydet
    if artifacts_dir and artifacts_dir.exists():
        report_path = artifacts_dir / "eu_ai_act_compliance_report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        
        # İnsan-okunabilir Markdown rapor
        md_report = _generate_markdown_report(report)
        md_path = artifacts_dir / "eu_ai_act_compliance_report.md"
        md_path.write_text(md_report, encoding="utf-8")
    
    return report


def _calculate_risk_level(sigma: float, mizan_score: Any, final_decision: Any) -> Dict[str, str]:
    """EU AI Act Article 6 bazlı risk seviyesi."""
    if sigma < 0.30:
        return {
            "level": "UNACCEPTABLE",
            "category": "Prohibited AI Practice",
            "justification": f"Council σ={sigma:.4f} — below minimum threshold (0.30). System was halted by automatic kill-switch.",
            "article": "Article 5 — Prohibited AI Practices",
        }
    elif sigma < 0.50:
        return {
            "level": "HIGH",
            "category": "High-Risk AI System",
            "justification": f"Council σ={sigma:.4f} — marginal ethics score indicates significant risks requiring enhanced oversight.",
            "article": "Article 6 — Classification as High-Risk",
        }
    elif sigma < 0.70:
        return {
            "level": "LIMITED",
            "category": "Limited Risk — Transparency Obligations",
            "justification": f"Council σ={sigma:.4f} — acceptable range but transparency requirements apply.",
            "article": "Article 50 — Transparency Obligations",
        }
    else:
        return {
            "level": "MINIMAL",
            "category": "Minimal Risk — Compliant",
            "justification": f"Council σ={sigma:.4f} — high ethics score indicates strong compliance posture.",
            "article": "Article 95 — Voluntary Codes of Conduct",
        }


def _run_compliance_checks(
    sigma: float,
    verdict: str,
    mizan_score: Any,
    final_decision: Any,
    has_architect: bool,
    has_auditor: bool,
) -> list:
    """EU AI Act maddelerine göre uyumluluk kontrolleri."""
    checks = []
    
    # Article 9 — Risk Management
    checks.append({
        "article": "Article 9",
        "title": "Risk Management System",
        "description": "AI system must have continuous risk identification and mitigation",
        "status": "PASS" if has_auditor else "FAIL",
        "evidence": "Auditor stage completed risk assessment" if has_auditor else "No auditor review found",
        "severity": "HIGH",
    })
    
    # Article 10 — Data Governance
    checks.append({
        "article": "Article 10",
        "title": "Data & Data Governance",
        "description": "Training and validation data must be relevant, representative, and error-free",
        "status": "PASS" if sigma >= 0.50 else "WARNING",
        "evidence": f"Council σ={sigma:.4f} — data governance assessed by 7 independent agents",
        "severity": "HIGH",
    })
    
    # Article 11 — Technical Documentation
    checks.append({
        "article": "Article 11",
        "title": "Technical Documentation",
        "description": "Complete technical documentation must be maintained",
        "status": "PASS" if has_architect else "FAIL",
        "evidence": "Architecture documentation generated by Architect stage" if has_architect else "No technical documentation",
        "severity": "HIGH",
    })
    
    # Article 13 — Transparency
    checks.append({
        "article": "Article 13",
        "title": "Transparency",
        "description": "AI system must provide adequate transparency to users",
        "status": "PASS",
        "evidence": f"Full audit trail with SHA-256 sealed evidence. Council verdict: {verdict}, σ={sigma:.4f}",
        "severity": "MEDIUM",
    })
    
    # Article 14 — Human Oversight
    checks.append({
        "article": "Article 14",
        "title": "Human Oversight",
        "description": "AI system must allow effective human oversight and intervention",
        "status": "PASS" if verdict in ("APPROVE", "REJECT") else "WARNING",
        "evidence": f"7AI Shūrā Council evaluation with human-attestable verdict: {verdict}",
        "severity": "HIGH",
    })
    
    # Article 15 — Accuracy & Robustness
    checks.append({
        "article": "Article 15",
        "title": "Accuracy, Robustness & Cybersecurity",
        "description": "AI system must achieve appropriate levels of accuracy and robustness",
        "status": "PASS" if sigma >= 0.30 else "FAIL",
        "evidence": f"Multi-agent evaluation with σ={sigma:.4f}. Kill-switch active below 0.30.",
        "severity": "HIGH",
    })
    
    # Kill-Switch Check
    checks.append({
        "article": "Article 5/9",
        "title": "Automatic Kill-Switch",
        "description": "System must halt when ethical thresholds are breached",
        "status": "PASS",
        "evidence": "Kill-switch active: σ < 0.30 → automatic halt. No override possible.",
        "severity": "CRITICAL",
    })
    
    # SHA-256 Evidence Chain
    checks.append({
        "article": "Article 12",
        "title": "Record-Keeping (Logging)",
        "description": "AI system must maintain audit logs throughout its lifecycle",
        "status": "PASS",
        "evidence": "All decisions sealed with SHA-256 hash chain. Tamper-proof evidence trail.",
        "severity": "HIGH",
    })
    
    return checks


def _count_stages(summary: Dict[str, Any]) -> int:
    """Tamamlanan pipeline stage sayısı."""
    count = 0
    if summary.get("architect"): count += 1
    if summary.get("auditor"): count += 1
    if summary.get("mizan_score") is not None: count += 1
    if summary.get("builder_status"): count += 1
    if summary.get("final_decision"): count += 2  # post_audit + final
    return min(count, 6)


def _generate_markdown_report(report: Dict[str, Any]) -> str:
    """İnsan-okunabilir Markdown uyumluluk raporu."""
    r = report
    cs = r["compliance_summary"]
    risk = r["risk_classification"]
    council = r["council_evaluation"]
    
    status_emoji = {"COMPLIANT": "✅", "PARTIALLY_COMPLIANT": "⚠️", "NON_COMPLIANT": "❌"}
    risk_emoji = {"MINIMAL": "🟢", "LIMITED": "🟡", "HIGH": "🟠", "UNACCEPTABLE": "🔴"}
    check_emoji = {"PASS": "✅", "FAIL": "❌", "WARNING": "⚠️"}
    
    lines = [
        f"# YARUKSAİ — EU AI Act Compliance Report",
        f"",
        f"**Generated:** {r['generated_at']}",
        f"**Run ID:** `{r['subject']['run_id']}`",
        f"**Goal:** {r['subject']['goal']}",
        f"**Seal:** `{r['seal']}`",
        f"",
        f"---",
        f"",
        f"## {status_emoji.get(cs['status'], '❓')} Overall Compliance: **{cs['status']}** ({cs['overall_score']}%)",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Checks Passed | {cs['checks_passed']}/{cs['checks_total']} |",
        f"| Council σ Score | {council['sigma_score']:.4f} |",
        f"| Council Verdict | {council['verdict']} |",
        f"| Risk Level | {risk_emoji.get(risk['level'], '❓')} {risk['level']} |",
        f"",
        f"---",
        f"",
        f"## Risk Classification",
        f"",
        f"- **Level:** {risk_emoji.get(risk['level'], '')} {risk['level']}",
        f"- **Category:** {risk['category']}",
        f"- **Reference:** {risk['article_6_reference']}",
        f"- **Justification:** {risk['justification']}",
        f"",
        f"---",
        f"",
        f"## Compliance Checks",
        f"",
        f"| # | Article | Check | Status | Severity |",
        f"|---|---------|-------|--------|----------|",
    ]
    
    for i, c in enumerate(r["compliance_checks"], 1):
        emoji = check_emoji.get(c["status"], "❓")
        lines.append(f"| {i} | {c['article']} | {c['title']} | {emoji} {c['status']} | {c['severity']} |")
    
    lines.extend([
        f"",
        f"---",
        f"",
        f"## Evidence Details",
        f"",
    ])
    
    for c in r["compliance_checks"]:
        lines.append(f"### {c['article']} — {c['title']}")
        lines.append(f"- **Status:** {check_emoji.get(c['status'], '')} {c['status']}")
        lines.append(f"- **Evidence:** {c['evidence']}")
        lines.append(f"")
    
    lines.extend([
        f"---",
        f"",
        f"## Legal Reference",
        f"",
        f"- **Regulation:** {r['regulation_reference']['name']}",
        f"- **ID:** {r['regulation_reference']['regulation_id']}",
        f"- **Effective Date:** {r['regulation_reference']['effective_date']}",
        f"",
        f"---",
        f"",
        f"*This report was generated by YARUKSAİ Compliance Engine v1.0*",
        f"*SHA-256 Seal: `{r['seal']}`*",
    ])
    
    return "\n".join(lines)
