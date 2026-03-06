# src/flows/mizan_engine.py

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


MIZAN_ENGINE_VERSION = "0.1.1"
MIZAN_AGENT_ID = "yaruksai_mizan_rule_engine"
MAX_REVIEW_LOOPS = 3


@dataclass
class ParsedIssue:
    severity: str
    category: str
    problem: str
    fix: str
    source_line: Optional[str] = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _normalize_severity(value: str) -> str:
    v = (value or "").strip().lower()
    mapping = {
        "high": "high",
        "yüksek": "high",
        "critical": "high",
        "orta": "medium",
        "medium": "medium",
        "med": "medium",
        "low": "low",
        "düşük": "low",
    }
    return mapping.get(v, "medium")


def _normalize_category(value: str) -> str:
    v = (value or "").strip().lower()

    # Türkçe / varyasyon map
    tr_map = {
        "mantık": "logic",
        "iletişim": "communication",
        "test": "test",
        "bakım": "maintainability",
        "pazar": "market",
        "maliyet": "cost",
        "hukuk": "legal",
        "uyumluluk": "compliance",
        "güvenlik": "security",
        "performans": "performance",
    }

    # İngilizce / slash'lı kategori yakalama
    contains_map = {
        "governance": "compliance",
        "requirement": "logic",
        "architecture": "logic",
        "coordination": "communication",
        "communication protocol": "communication",
        "protocol": "communication",
        "data standardization": "compliance",
        "data": "compliance",
        "testing": "test",
        "documentation": "maintainability",
        "process": "maintainability",
        "legal": "legal",
        "compliance": "compliance",
        "security": "security",
        "cost": "cost",
        "market": "market",
        "performance": "performance",
        "maintenance": "maintainability",
        "maintainability": "maintainability",
        "logic": "logic",
        "communication": "communication",
    }

    v = tr_map.get(v, v)

    # Slash / çoklu kategori ise parçalara ayır
    parts = [p.strip() for p in re.split(r"[/,|]+", v) if p.strip()]

    known = {
        "logic",
        "communication",
        "test",
        "maintainability",
        "market",
        "cost",
        "legal",
        "security",
        "performance",
        "compliance",
    }

    # Önce direkt eşleşme
    for p in parts or [v]:
        if p in known:
            return p
        if p == "maintenance":
            return "maintainability"
        if p in tr_map:
            mapped = tr_map[p]
            if mapped in known:
                return mapped

    # Sonra içerik bazlı eşleşme
    full = " ".join(parts) if parts else v
    for key, mapped in contains_map.items():
        if key in full:
            return mapped

    return "general"


def _extract_issues_from_json(auditor_data: Dict[str, Any]) -> List[ParsedIssue]:
    raw_issues = auditor_data.get("issues", [])
    parsed: List[ParsedIssue] = []

    if isinstance(raw_issues, list):
        for item in raw_issues:
            if isinstance(item, dict):
                parsed.append(
                    ParsedIssue(
                        severity=_normalize_severity(str(item.get("severity", "medium"))),
                        category=_normalize_category(str(item.get("category", "general"))),
                        problem=str(item.get("problem", "")).strip() or "Unspecified issue",
                        fix=str(item.get("fix", "")).strip() or "Provide concrete corrective action",
                        source_line=None,
                    )
                )
            elif isinstance(item, str):
                parsed.extend(_extract_issues_from_text(item))
    return parsed


def _extract_issues_from_text(auditor_text: str) -> List[ParsedIssue]:
    """
    Tolerant parser for:
    A) [HIGH][logic] Problem: ... | Fix: ...
    B) Severity: high | Category: test | Problem: ... | Fix: ...
    C) Yüksek - mantık - problem ... - çözüm ...
    D) Multiline YAML-ish blocks:
       - severity: High
         category: Testing
         problem: ...
         fix: ...
    """
    issues: List[ParsedIssue] = []
    lines = [ln.rstrip() for ln in auditor_text.splitlines() if ln.strip()]

    # Pattern A: [HIGH][logic] Problem: ... | Fix: ...
    p1 = re.compile(
        r"^\s*\[(?P<sev>[^\]]+)\]\s*\[(?P<cat>[^\]]+)\]\s*"
        r"(?:Problem|Sorun)\s*:\s*(?P<problem>.*?)\s*(?:\||$)\s*"
        r"(?:Fix|Çözüm)\s*:\s*(?P<fix>.+?)\s*$",
        re.IGNORECASE,
    )

    # Pattern B: Severity: high | Category: test | Problem: ... | Fix: ...
    p2 = re.compile(
        r"Severity\s*:\s*(?P<sev>.*?)\s*\|\s*Category\s*:\s*(?P<cat>.*?)\s*\|\s*"
        r"Problem\s*:\s*(?P<problem>.*?)\s*\|\s*Fix\s*:\s*(?P<fix>.+)$",
        re.IGNORECASE,
    )

    # Pattern C: Yüksek - mantık - problem ... - çözüm ...
    p3 = re.compile(
        r"^(?P<sev>yüksek|orta|düşük)\s*[-–]\s*(?P<cat>[^\-–]+)\s*[-–]\s*"
        r"(?P<problem>.*?)(?:\s*[-–]\s*(?P<fix>.+))?$",
        re.IGNORECASE,
    )

    # First pass: single-line formats
    for line in lines:
        m = p1.match(line) or p2.match(line) or p3.match(line)
        if not m:
            continue

        sev = _normalize_severity(m.group("sev"))
        cat = _normalize_category(m.group("cat"))
        problem = (m.group("problem") or "").strip()
        fix = (m.groupdict().get("fix") or "").strip() or "Provide concrete corrective action"

        if problem:
            issues.append(
                ParsedIssue(
                    severity=sev,
                    category=cat,
                    problem=problem,
                    fix=fix,
                    source_line=line,
                )
            )

    # Second pass: multiline YAML-ish issue blocks
    # Example:
    # issues:
    # - severity: High
    #   category: Testing
    #   problem: ...
    #   fix: ...
    block_issue: Dict[str, str] = {}

    def flush_block() -> None:
        nonlocal block_issue, issues
        if not block_issue:
            return

        sev = _normalize_severity(block_issue.get("severity", "medium"))
        cat = _normalize_category(block_issue.get("category", "general"))
        problem = block_issue.get("problem", "").strip()
        fix = block_issue.get("fix", "").strip() or "Provide concrete corrective action"

        if problem:
            issues.append(
                ParsedIssue(
                    severity=sev,
                    category=cat,
                    problem=problem,
                    fix=fix,
                    source_line=None,
                )
            )
        block_issue = {}

    inside_issues_section = False
    last_field: Optional[str] = None

    for raw_line in auditor_text.splitlines():
        line = raw_line.rstrip()

        # "issues:" bölümüne gir
        if re.match(r"^\s*issues\s*:\s*$", line, re.IGNORECASE):
            inside_issues_section = True
            flush_block()
            last_field = None
            continue

        # issues bölümünden çık (diğer ana başlıklara geçince)
        if inside_issues_section and re.match(
            r"^\s*(cost_efficiency_review|legal_compliance_review|market_viability_note|ready_for_build)\s*:\s*",
            line,
            re.IGNORECASE,
        ):
            flush_block()
            inside_issues_section = False
            last_field = None
            continue

        if not inside_issues_section:
            continue

        # Yeni issue başlangıcı
        m_sev = re.match(r"^\s*-\s*severity\s*:\s*(.+?)\s*$", line, re.IGNORECASE)
        if m_sev:
            flush_block()
            block_issue["severity"] = m_sev.group(1).strip()
            last_field = "severity"
            continue

        # Devam alanları
        m_cat = re.match(r"^\s*category\s*:\s*(.+?)\s*$", line, re.IGNORECASE)
        if m_cat:
            block_issue["category"] = m_cat.group(1).strip()
            last_field = "category"
            continue

        m_prob = re.match(r"^\s*problem\s*:\s*(.+?)\s*$", line, re.IGNORECASE)
        if m_prob:
            block_issue["problem"] = m_prob.group(1).strip()
            last_field = "problem"
            continue

        m_fix = re.match(r"^\s*fix\s*:\s*(.+?)\s*$", line, re.IGNORECASE)
        if m_fix:
            block_issue["fix"] = m_fix.group(1).strip()
            last_field = "fix"
            continue

        # Çok satırlı problem/fix devamı
        stripped = line.strip()
        if block_issue and stripped and not re.match(r"^\w+\s*:", stripped):
            if last_field == "fix":
                block_issue["fix"] = (block_issue.get("fix", "") + " " + stripped).strip()
            elif last_field == "problem":
                block_issue["problem"] = (block_issue.get("problem", "") + " " + stripped).strip()

    flush_block()

    # Duplicate temizliği
    deduped: List[ParsedIssue] = []
    seen = set()
    for i in issues:
        key = (i.severity, i.category, i.problem, i.fix)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(i)

    return deduped


def parse_auditor_output(auditor_output_text: str) -> Dict[str, Any]:
    """
    Returns a normalized dict:
    {
      "audit_summary": str,
      "ready_for_build": bool|None,
      "issues": [ParsedIssue...],
      "raw": {... or text}
    }
    """
    maybe_json = _safe_json_loads(auditor_output_text)

    if maybe_json:
        issues = _extract_issues_from_json(maybe_json)
        return {
            "audit_summary": str(maybe_json.get("audit_summary", "")).strip(),
            "ready_for_build": maybe_json.get("ready_for_build"),
            "issues": issues,
            "raw": maybe_json,
        }

    # Fallback text parse
    issues = _extract_issues_from_text(auditor_output_text)

    # audit_summary (çok satırlı olabilir)
    summary = ""
    summary_match = re.search(
        r"audit_summary\s*:\s*(.*?)(?:\n\s*issues\s*:|\Z)",
        auditor_output_text,
        re.IGNORECASE | re.DOTALL,
    )
    if summary_match:
        summary = summary_match.group(1).strip()

    # ready_for_build
    ready_for_build = None
    m = re.search(r"ready_for_build\s*[:=]\s*(true|false)", auditor_output_text, re.IGNORECASE)
    if m:
        ready_for_build = m.group(1).lower() == "true"

    return {
        "audit_summary": summary,
        "ready_for_build": ready_for_build,
        "issues": issues,
        "raw": auditor_output_text,
    }


def _score_issue(issue: ParsedIssue) -> int:
    severity_penalty = {"high": 15, "medium": 8, "low": 3}.get(issue.severity, 8)

    category_weight = {
        "legal": 5,
        "compliance": 5,
        "security": 5,
        "cost": 3,
        "logic": 3,
        "test": 2,
        "maintainability": 2,
        "market": 2,
        "communication": 1,
        "performance": 2,
        "general": 1,
    }.get(issue.category, 1)

    return severity_penalty + category_weight


def _compute_mizan_score(issues: List[ParsedIssue]) -> int:
    score = 100
    for issue in issues:
        score -= _score_issue(issue)
    return max(0, min(100, score))


def _decide_review(
    mizan_score: int,
    issues: List[ParsedIssue],
    ready_for_build_hint: Optional[bool],
    review_loop_count: int,
) -> Dict[str, Any]:
    high_count = sum(1 for i in issues if i.severity == "high")
    legal_or_compliance_high = any(
        i.severity == "high" and i.category in {"legal", "compliance", "security"} for i in issues
    )

    if review_loop_count >= MAX_REVIEW_LOOPS:
        return {
            "review_decision": "human_escalation",
            "reason": f"Review loop limit reached ({MAX_REVIEW_LOOPS})",
        }

    if legal_or_compliance_high:
        return {
            "review_decision": "revise_required",
            "reason": "High-severity legal/compliance/security issue present",
        }

    if high_count > 0:
        return {
            "review_decision": "revise_required",
            "reason": "One or more high-severity issues present",
        }

    if mizan_score < 80:
        return {
            "review_decision": "revise_required",
            "reason": f"Mizan score below threshold: {mizan_score}",
        }

    if ready_for_build_hint is False and mizan_score < 90:
        return {
            "review_decision": "revise_required",
            "reason": "Auditor marked not ready_for_build and score is not strong enough",
        }

    return {
        "review_decision": "approve_for_build",
        "reason": f"Mizan score acceptable: {mizan_score}",
    }


def _split_fixes(issues: List[ParsedIssue]) -> Dict[str, List[Dict[str, Any]]]:
    accepted_fixes: List[Dict[str, Any]] = []
    rejected_fixes: List[Dict[str, Any]] = []

    for idx, issue in enumerate(issues, start=1):
        fix_item = {
            "id": f"fix_{idx:03d}",
            "severity": issue.severity,
            "category": issue.category,
            "problem": issue.problem,
            "fix": issue.fix,
        }

        # Basit kalite politikası: çok kısa / anlamsız fix reddedilir
        if len(issue.fix.strip()) < 12 or issue.fix.lower() in {"fix it", "improve", "düzelt"}:
            fix_item["reason"] = "Fix proposal too vague / non-actionable"
            rejected_fixes.append(fix_item)
        else:
            accepted_fixes.append(fix_item)

    return {
        "accepted_fixes": accepted_fixes,
        "rejected_fixes": rejected_fixes,  # rejected item'larda reason zorunlu
    }


def _build_context_packet(
    architect_output: str,
    parsed_audit: Dict[str, Any],
    accepted_fixes: List[Dict[str, Any]],
    mizan_score: int,
) -> Dict[str, Any]:
    top_constraints: List[str] = []
    categories_seen = sorted({f["category"] for f in accepted_fixes})

    if "cost" in categories_seen:
        top_constraints.append("Optimize token/tool cost without reducing auditability")
    if "legal" in categories_seen or "compliance" in categories_seen:
        top_constraints.append("Respect legal/compliance constraints before feature expansion")
    if "test" in categories_seen:
        top_constraints.append("Add validation/tests before build finalization")
    if "communication" in categories_seen:
        top_constraints.append("Clarify agent coordination and protocol choices before implementation")
    if "logic" in categories_seen:
        top_constraints.append("Resolve governance scope and decision rules before coding core flows")

    architect_summary = architect_output.strip()
    if len(architect_summary) > 1200:
        architect_summary = architect_summary[:1200] + "..."

    return {
        "version": 1,
        "timestamp": _utc_now_iso(),
        "agent_id": MIZAN_AGENT_ID,
        "mizan_score": mizan_score,
        "architect_summary": architect_summary,
        "audit_summary": parsed_audit.get("audit_summary", ""),
        "required_fixes": accepted_fixes,
        "top_constraints": top_constraints,
        "policy_flags": {
            "post_build_audit_required": True,
            "cost_logging_required": True,
            "metadata_required": True,
            "max_review_loops": MAX_REVIEW_LOOPS,
        },
    }


def _build_builder_instructions(
    accepted_fixes: List[Dict[str, Any]],
    context_packet: Dict[str, Any],
) -> str:
    lines: List[str] = []
    lines.append("You are the Builder stage. Apply the accepted fixes only.")
    lines.append("Do not ignore cost-efficiency, legal/compliance, and maintainability concerns.")
    lines.append("Return structured output with metadata (version, timestamp, agent_id).")
    lines.append("")
    lines.append("Accepted fixes:")

    if not accepted_fixes:
        lines.append("- No accepted fixes. Proceed conservatively and preserve architecture.")
    else:
        for f in accepted_fixes:
            lines.append(
                f"- [{f['severity'].upper()}][{f['category']}] Problem: {f['problem']} | Fix: {f['fix']}"
            )

    lines.append("")
    lines.append("Context constraints:")
    constraints = context_packet.get("top_constraints", [])
    if constraints:
        for c in constraints:
            lines.append(f"- {c}")
    else:
        lines.append("- None")

    return "\n".join(lines)


def run_mizan_engine(
    architect_output_text: str,
    auditor_output_text: str,
    review_loop_count: int = 0,
) -> Dict[str, Any]:
    parsed_audit = parse_auditor_output(auditor_output_text)
    issues: List[ParsedIssue] = parsed_audit["issues"]

    mizan_score = _compute_mizan_score(issues)
    decision = _decide_review(
        mizan_score=mizan_score,
        issues=issues,
        ready_for_build_hint=parsed_audit.get("ready_for_build"),
        review_loop_count=review_loop_count,
    )

    fixes_split = _split_fixes(issues)

    context_packet = _build_context_packet(
        architect_output=architect_output_text,
        parsed_audit=parsed_audit,
        accepted_fixes=fixes_split["accepted_fixes"],
        mizan_score=mizan_score,
    )

    builder_instructions = _build_builder_instructions(
        accepted_fixes=fixes_split["accepted_fixes"],
        context_packet=context_packet,
    )

    result = {
        "version": MIZAN_ENGINE_VERSION,
        "timestamp": _utc_now_iso(),
        "agent_id": MIZAN_AGENT_ID,
        "review_loop_count": review_loop_count,
        "max_review_loops": MAX_REVIEW_LOOPS,
        "mizan_score": mizan_score,
        "review_decision": decision["review_decision"],
        "decision_reason": decision["reason"],
        "issue_count": len(issues),
        "issues": [asdict(i) for i in issues],
        "accepted_fixes": fixes_split["accepted_fixes"],
        "rejected_fixes": fixes_split["rejected_fixes"],
        "yaruksai_context_packet": context_packet,
        "builder_instructions": builder_instructions,
    }

    return result
