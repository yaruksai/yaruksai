from __future__ import annotations

import sys
import json
import re
import hashlib
from pathlib import Path
from datetime import datetime, timezone

# src klasörünü Python path'e ekle
CREWAI_DIR = Path(__file__).resolve().parent
if str(CREWAI_DIR) not in sys.path:
    sys.path.insert(0, str(CREWAI_DIR))

from crewai import Agent, Task, Crew, Process

from crew_engine.config import load_environment, validate_env_keys, get_llm, LLM_PROVIDER, OLLAMA_BASE_URL

import os
# Ollama kullanılıyorsa CrewAI'ın environment variable'larını ayarla
if LLM_PROVIDER == "ollama":
    os.environ.setdefault("OPENAI_API_BASE", OLLAMA_BASE_URL)
    os.environ.setdefault("OPENAI_API_KEY", "ollama")  # CrewAI bunu istiyor ama kullanmıyor

# Global status callback (web UI SSE için)
_status_callback = None

def set_status_callback(callback):
    global _status_callback
    _status_callback = callback

def _emit_status(stage: int, name: str, status: str, detail: str = ""):
    """Pipeline durumunu bildiren yardımcı fonksiyon."""
    msg = {"stage": stage, "name": name, "status": status, "detail": detail}
    print(f"\n[ORCHESTRATOR] {name}: {status} {detail}")
    if _status_callback:
        _status_callback(msg)
from crew_engine.prompts import (
    CHATGPT_ARCHITECT_ROLE,
    CHATGPT_ARCHITECT_GOAL,
    CHATGPT_ARCHITECT_BACKSTORY,
    CHATGPT_ARCHITECT_TASK_TEMPLATE,
    GEMINI_AUDITOR_ROLE,
    GEMINI_AUDITOR_GOAL,
    GEMINI_AUDITOR_BACKSTORY,
    GEMINI_AUDITOR_TASK_TEMPLATE,
    CLAUDE_BUILDER_ROLE,
    CLAUDE_BUILDER_GOAL,
    CLAUDE_BUILDER_BACKSTORY,
    CLAUDE_BUILDER_TASK_TEMPLATE,
    POST_BUILD_AUDITOR_TASK_TEMPLATE,
)
from crew_engine.mizan_engine import run_mizan_engine
from mizan_engine.witness_chain import WitnessChain

ROOT_DIR = CREWAI_DIR.parent
ARTIFACTS_DIR = ROOT_DIR / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# YARDIMCI FONKSİYONLAR
# =============================================================================

def load_docs_context_for_goal(max_chars: int = 2500) -> str:
    """
    docs/ altındaki kritik kuralları başlangıç hedefine enjekte etmek için özet üretir.
    İlk turdan itibaren Architect bu kısıtlamaları görsün diye demo_goal'a eklenir.
    """
    docs_dir = ROOT_DIR / "docs"
    if not docs_dir.exists():
        return ""

    names = [
        "human_approval_rules.md",
        "protocol_security_perf_evaluation.md",
        "test_plan.md",
        "ownership_and_versioning.md",
    ]

    chunks = []
    for name in names:
        p = docs_dir / name
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore").strip()
        if not txt:
            continue
        chunks.append(f"## {name}\n{txt}\n")

    if not chunks:
        return ""

    joined = "\n".join(chunks)
    joined = joined[:max_chars]
    return "\n\n=== DOCS (MUST FOLLOW) ===\n" + joined + "\n=== END DOCS ===\n"


def parse_json_safe(raw_text: str, stage_name: str = "unknown") -> dict:
    """
    LLM/Crew çıktısından güvenli şekilde JSON parse eder.
    """
    if raw_text is None:
        raise ValueError(f"[{stage_name}] Boş çıktı (None) geldi.")

    text = str(raw_text).strip()
    if not text:
        raise ValueError(f"[{stage_name}] Boş çıktı (empty string) geldi.")

    def _clean_trailing_commas(s: str) -> str:
        s = re.sub(r",\s*([}\]])", r"\1", s)
        s = re.sub(r",\s*$", "", s.strip())
        return s

    def _try_load(s: str):
        return json.loads(_clean_trailing_commas(s))

    # 1) Direkt parse
    try:
        return _try_load(text)
    except Exception:
        pass

    # 2) ```json ... ```
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        candidate = fence.group(1).strip()
        try:
            return _try_load(candidate)
        except Exception:
            fb = candidate.find("{")
            lb = candidate.rfind("}")
            if fb != -1 and lb != -1 and lb > fb:
                try:
                    return _try_load(candidate[fb:lb + 1].strip())
                except Exception:
                    pass

    # 3) İlk { ... son }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace:last_brace + 1].strip()
        try:
            return _try_load(candidate)
        except Exception:
            pass

    # 4) Wrap
    stripped = text.strip()
    if not stripped.startswith("{"):
        candidate = "{\n" + stripped + "\n}"
        try:
            return _try_load(candidate)
        except Exception:
            pass

    preview = text[:800].replace("\n", "\\n")
    raise ValueError(f"[{stage_name}] Çıktı içinde JSON bulunamadı veya parse edilemedi. Preview: {preview}")


def extract_result_text(result) -> str:
    return (
        getattr(result, "raw", None)
        or getattr(result, "output", None)
        or str(result)
    )


def save_text(filename: str, content: str) -> None:
    (ARTIFACTS_DIR / filename).write_text(content, encoding="utf-8")


def save_json(filename: str, data: dict) -> None:
    (ARTIFACTS_DIR / filename).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_optional_approval_decision() -> dict | None:
    """
    Human approval file:
      artifacts/approval_decision.json
    Minimal schema:
      {"decision": "ALLOW" | "DENY", "reason": "...", "timestamp": "...", "reviewer": "..."}
    """
    p = ARTIFACTS_DIR / "approval_decision.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _human_allow_active(review_decision: str) -> bool:
    """
    Human ALLOW sadece Mizan onay vermediyse (approve_for_build değilse) devreye girsin.
    Böylece dosya unutulsa bile normal akışı bozmaz.
    """
    if review_decision == "approve_for_build":
        return False
    ad = load_optional_approval_decision()
    return bool(ad and str(ad.get("decision", "")).strip().upper() == "ALLOW")


def build_revised_project_goal(base_goal: str, mizan_output: dict, loop_index: int) -> str:
    """
    Revize turu için project goal metnini güçlendirir.
    """
    accepted = mizan_output.get("accepted_fixes", []) or []
    docs_context = load_docs_context_for_goal()

    if not accepted:
        return f"{base_goal}{docs_context}"

    fix_lines = []
    for i, item in enumerate(accepted, start=1):
        sev = str(item.get("severity", "")).upper()
        cat = str(item.get("category", ""))
        fix = str(item.get("fix", ""))
        fix_lines.append(f"{i}. [{sev}][{cat}] {fix}")

    revised_goal = (
        f"{base_goal}\n\n"
        f"REVISION LOOP: {loop_index}\n"
        "Aşağıdaki Mizan düzeltmelerini bu turda özellikle uygula:\n"
        + "\n".join(fix_lines)
        + docs_context
    )
    return revised_goal


# =============================================================================
# CREW BUILDER FONKSİYONLARI
# =============================================================================

def build_architect_crew(project_goal: str) -> Crew:
    architect_agent = Agent(
        role=CHATGPT_ARCHITECT_ROLE,
        goal=CHATGPT_ARCHITECT_GOAL,
        backstory=CHATGPT_ARCHITECT_BACKSTORY,
        verbose=True,
        allow_delegation=False,
        llm=get_llm(),
    )

    architect_task = Task(
        description=CHATGPT_ARCHITECT_TASK_TEMPLATE.format(project_goal=project_goal),
        expected_output=(
            "A clear project draft with sections: project name, goal, assumptions, "
            "architecture, file tree, implementation plan, risks, and open questions."
        ),
        agent=architect_agent,
    )

    return Crew(
        agents=[architect_agent],
        tasks=[architect_task],
        process=Process.sequential,
        verbose=True,
    )


def build_auditor_crew(architect_output: str) -> Crew:
    auditor_agent = Agent(
        role=GEMINI_AUDITOR_ROLE,
        goal=GEMINI_AUDITOR_GOAL,
        backstory=GEMINI_AUDITOR_BACKSTORY,
        verbose=True,
        allow_delegation=False,
        llm=get_llm(),
    )

    auditor_task = Task(
        description=GEMINI_AUDITOR_TASK_TEMPLATE.format(architect_output=architect_output),
        expected_output=(
            "JSON audit feedback with: audit_summary, issues, cost_efficiency_review, "
            "legal_compliance_review, market_viability_note, ready_for_build."
        ),
        agent=auditor_agent,
    )

    return Crew(
        agents=[auditor_agent],
        tasks=[auditor_task],
        process=Process.sequential,
        verbose=True,
    )


def build_builder_crew(
    project_goal: str,
    architect_output: str,
    auditor_json: dict,
    mizan_output: dict,
) -> Crew:
    """
    Builder stage: Mizan tarafından kabul edilen düzeltmelere göre uygulama çıktısı üretir.
    """
    builder_agent = Agent(
        role=CLAUDE_BUILDER_ROLE,
        goal=CLAUDE_BUILDER_GOAL,
        backstory=CLAUDE_BUILDER_BACKSTORY,
        verbose=True,
        allow_delegation=False,
        llm=get_llm(),
    )

    context_packet = mizan_output.get("yaruksai_context_packet", {})
    builder_instructions = mizan_output.get("builder_instructions", "")

    merged_spec = {
        "project_goal": project_goal,
        "architect_output": architect_output,
        "auditor_output": auditor_json,
        "mizan_output": mizan_output,
        "constraints": context_packet.get("top_constraints", []),
    }

    task_description = CLAUDE_BUILDER_TASK_TEMPLATE.format(
        merged_spec=json.dumps(merged_spec, ensure_ascii=False, indent=2),
        yaruksai_context_packet=json.dumps(context_packet, ensure_ascii=False, indent=2),
        builder_instructions=builder_instructions,
    )

    builder_task = Task(
        description=task_description,
        expected_output=(
            "JSON builder output with build_summary, files_created_or_updated, code_notes, "
            "tests_added, known_limits, next_steps."
        ),
        agent=builder_agent,
    )

    return Crew(
        agents=[builder_agent],
        tasks=[builder_task],
        process=Process.sequential,
        verbose=True,
    )


def build_post_build_auditor_crew(build_output_json: dict) -> Crew:
    """
    Build çıktısını tekrar denetler (post-build audit).
    """
    post_auditor_agent = Agent(
        role="Post-build Technical Auditor",
        goal="Builder çıktısını denetlemek ve riskleri/eksikleri JSON formatında raporlamak.",
        backstory=(
            "Sen post-build denetçisin. Builder'ın ürettiği çıktıyı test, bakım, güvenlik ve "
            "uyum açısından kontrol eder; net düzeltme önerileri verirsin."
        ),
        verbose=True,
        allow_delegation=False,
        llm=get_llm(),
    )

    post_auditor_task = Task(
        description=POST_BUILD_AUDITOR_TASK_TEMPLATE.format(
            build_output=json.dumps(build_output_json, ensure_ascii=False, indent=2)
        ),
        expected_output="JSON with audit_summary, issues, ready_for_build",
        agent=post_auditor_agent,
    )

    return Crew(
        agents=[post_auditor_agent],
        tasks=[post_auditor_task],
        process=Process.sequential,
        verbose=True,
    )


# =============================================================================
# FINAL MIZAN GATE (Python rule-based — LLM çağrısı yok)
# =============================================================================

def final_mizan_gate(
    mizan_output: dict,
    build_output: dict,
    post_build_audit: dict,
    loop_index: int = 0,
) -> dict:
    """
    Final karar katmanı (Python rule-based).
    """
    max_loops = int(
        mizan_output.get("max_review_loops")
        or mizan_output.get("yaruksai_context_packet", {})
            .get("policy_flags", {})
            .get("max_review_loops", 3)
        or 3
    )

    def _meta() -> dict:
        return {
            "version": "0.1.0",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "agent_id": "final_mizan_gate_python",
        }

    # 1) Builder skip
    if build_output.get("status") == "skipped":
        return {
            "decision": "revise",
            "reason": "Builder stage skipped because Mizan did not approve build",
            "next_action": "Apply Mizan required fixes and rerun pipeline",
            "metadata": _meta(),
        }

    # 2) Post-build HIGH issue
    issues = post_build_audit.get("issues", []) or []
    severities = [str(x.get("severity", "")).strip().lower() for x in issues]
    if "high" in severities:
        return {
            "decision": "revise",
            "reason": "Post-build audit contains high severity issue(s)",
            "next_action": "Fix high severity issues and rerun post-build validation",
            "metadata": _meta(),
        }

    # 3) ready_for_build False
    if post_build_audit.get("ready_for_build") is False:
        return {
            "decision": "revise",
            "reason": "Post-build audit marked output as not ready",
            "next_action": "Apply post-build audit fixes and rerun",
            "metadata": _meta(),
        }

    # 4) Loop limit aşıldı => human escalation
    if loop_index >= max_loops:
        return {
            "decision": "human_escalation",
            "reason": f"Loop limit reached ({loop_index}/{max_loops})",
            "next_action": "Hand over to human reviewer with full artifacts",
            "metadata": _meta(),
        }

    # 5) Temiz => complete
    return {
        "decision": "complete",
        "reason": "No blocking issues found; output is ready",
        "next_action": "Proceed to packaging / handoff",
        "metadata": _meta(),
    }


# =============================================================================
# ANA AKIŞ
# =============================================================================

def run_six_stage_flow(project_goal: str) -> tuple[str, dict, dict, dict, dict, dict]:
    load_environment()
    validate_env_keys()

    # ─── WitnessChain: immutable SHA-256 evidence trail ───
    chain = WitnessChain()
    chain.add("crewai_orchestrator", "PIPELINE_START", {
        "goal": project_goal[:500],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "engine": "crew_engine",
    })

    current_goal = project_goal
    max_review_loops = 3

    architect_text = ""
    auditor_text = ""
    auditor_json: dict = {}
    mizan_output: dict = {}

    builder_output: dict = {
        "version": "0.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        "agent_id": "builder_stage",
        "status": "skipped",
        "reason": "Builder has not run yet",
        "next_action": "Run pipeline",
    }

    loop_index = 0

    # REVIEW LOOP (max 3 tur)
    for loop_index in range(max_review_loops):
        print(f"\n[ORCHESTRATOR] Review Loop {loop_index + 1}/{max_review_loops}\n")

        # STAGE 1 — Architect
        _emit_status(1, "Architect", "running", "Proje taslağı üretiliyor...")
        architect_crew = build_architect_crew(current_goal)
        architect_result = architect_crew.kickoff()
        architect_text = extract_result_text(architect_result)
        save_text("architect_stage_output.txt", architect_text)
        chain.add("architect_agent", "EVALUATE", {
            "stage": 1,
            "output_length": len(architect_text),
            "output_hash": hashlib.sha256(architect_text.encode()).hexdigest(),
            "loop_index": loop_index,
        })
        _emit_status(1, "Architect", "done", "Taslak hazır.")

        # STAGE 2 — Auditor
        _emit_status(2, "Auditor", "running", "Taslak denetleniyor...")
        auditor_crew = build_auditor_crew(architect_text)
        auditor_result = auditor_crew.kickoff()
        auditor_text = extract_result_text(auditor_result)
        save_text("auditor_stage_output.txt", auditor_text)
        chain.add("auditor_agent", "EVALUATE", {
            "stage": 2,
            "output_length": len(auditor_text),
            "output_hash": hashlib.sha256(auditor_text.encode()).hexdigest(),
            "loop_index": loop_index,
        })
        _emit_status(2, "Auditor", "done", "Denetim tamamlandı.")

        auditor_json = parse_json_safe(auditor_text, "auditor")
        save_json("auditor_stage_output_parsed.json", auditor_json)

        # STAGE 3 — Mizan
        _emit_status(3, "Mizan", "running", "Karar motoru çalışıyor...")
        mizan_output = run_mizan_engine(
            architect_output_text=architect_text,
            auditor_output_text=json.dumps(auditor_json, ensure_ascii=False, indent=2),
            review_loop_count=loop_index + 1,  # 1..3
        )
        save_json("mizan_stage_output.json", mizan_output)
        chain.add("mizan_engine", "JUDGE", {
            "stage": 3,
            "mizan_score": mizan_output.get("mizan_score"),
            "review_decision": mizan_output.get("review_decision"),
            "issue_count": mizan_output.get("issue_count"),
            "accepted_fixes": len(mizan_output.get("accepted_fixes", [])),
            "loop_index": loop_index,
        })
        _emit_status(3, "Mizan", "done", f"Karar: {mizan_output.get('review_decision', '')}")

        review_decision = str(mizan_output.get("review_decision", "")).strip().lower()

        if review_decision == "approve_for_build":
            print(f"\n[ORCHESTRATOR] Mizan approved build at loop {loop_index + 1}.")
            break

        # FAIL-CLOSED: Loop doldu ama approve yok => human approval ara.
        if (loop_index + 1) >= max_review_loops:
            approval_decision = load_optional_approval_decision()

            if approval_decision:
                d = str(approval_decision.get("decision", "")).strip().upper()

                if d == "ALLOW":
                    print("\n[ORCHESTRATOR] Human approval: ALLOW. Continuing to build.")
                    break

                if d == "DENY":
                    print("\n[ORCHESTRATOR] Human approval: DENY. Stopping.")
                    save_json(
                        "final_gate_output.json",
                        {
                            "decision": "revise",
                            "reason": "Human reviewer denied build",
                            "next_action": "Apply required fixes and rerun pipeline",
                            "metadata": {
                                "version": "0.1.0",
                                "timestamp": datetime.utcnow().isoformat() + "Z",
                                "agent_id": "final_mizan_gate_human_deny",
                            },
                        },
                    )
                    return (
                        architect_text,
                        auditor_json,
                        mizan_output,
                        builder_output,
                        {
                            "audit_summary": "Stopped due to human DENY decision.",
                            "issues": [
                                {
                                    "severity": "High",
                                    "category": "Governance",
                                    "problem": "Human reviewer denied build.",
                                    "fix": "Apply required fixes and rerun pipeline.",
                                }
                            ],
                            "ready_for_build": False,
                            "metadata": {
                                "version": "0.1.0",
                                "timestamp": datetime.utcnow().isoformat() + "Z",
                                "agent_id": "post_build_audit_human_deny",
                            },
                        },
                        json.loads((ARTIFACTS_DIR / "final_gate_output.json").read_text(encoding="utf-8")),
                    )

            # Approval dosyası yoksa => request üret ve dur
            approval_request = {
                "decision": "REVIEW_REQUIRED",
                "reason": f"Mizan did not approve build after {max_review_loops} review loops",
                "review_decision": review_decision,
                "loop_index": loop_index + 1,
                "max_review_loops": max_review_loops,
                "required_fixes": mizan_output.get("required_fixes", []),
                "accepted_fixes": mizan_output.get("accepted_fixes", []),
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            save_json("approval_request.json", approval_request)

            print("\n[ORCHESTRATOR] FAIL-CLOSED: Human approval required.")
            print("[ORCHESTRATOR] Saved: artifacts/approval_request.json")
            return (
                architect_text,
                auditor_json,
                mizan_output,
                builder_output,
                {
                    "audit_summary": "Pipeline stopped (fail-closed) waiting for human approval.",
                    "issues": [
                        {
                            "severity": "High",
                            "category": "Governance",
                            "problem": "Mizan did not approve build and review loop limit was reached.",
                            "fix": "Provide approval_decision.json and rerun, or apply fixes and rerun.",
                        }
                    ],
                    "ready_for_build": False,
                    "metadata": {
                        "version": "0.1.0",
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "agent_id": "post_build_audit_fail_closed",
                    },
                },
                {
                    "decision": "human_escalation",
                    "reason": "Fail-closed triggered: waiting for human approval",
                    "next_action": "Review artifacts/approval_request.json and provide approval_decision.json",
                    "metadata": {
                        "version": "0.1.0",
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "agent_id": "final_mizan_gate_fail_closed",
                    },
                },
            )

        # Sonraki tura hazırlık: accepted_fixes enjeksiyonu + docs context
        current_goal = build_revised_project_goal(
            base_goal=project_goal,
            mizan_output=mizan_output,
            loop_index=loop_index + 2,
        )
        print("\n[ORCHESTRATOR] Revising project goal for next Architect loop...\n")

    # STAGE 4 — Builder
    _emit_status(4, "Builder", "running", "Uygulama çıktısı üretiliyor...")
    review_decision = str(mizan_output.get("review_decision", "")).strip().lower()

    # Human ALLOW ile builder'a izin ver (sadece approve yoksa)
    if _human_allow_active(review_decision):
        review_decision = "approve_for_build"

    if review_decision != "approve_for_build":
        builder_output = {
            "version": "0.1.0",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "agent_id": "builder_stage",
            "status": "skipped",
            "reason": f"Mizan decision is '{review_decision}'",
            "next_action": "Apply required fixes and rerun architect/auditor/mizan loop",
        }
        save_json("builder_stage_output.json", builder_output)
        save_text(
            "builder_stage_output.txt",
            "[BUILDER SKIPPED]\n"
            f"Mizan decision: {review_decision}\n"
            "Builder çalıştırılmadı çünkü Mizan build onayı vermedi.\n",
        )
    else:
        builder_crew = build_builder_crew(
            project_goal=current_goal,
            architect_output=architect_text,
            auditor_json=auditor_json,
            mizan_output=mizan_output,
        )
        builder_result = builder_crew.kickoff()
        builder_text = extract_result_text(builder_result)
        save_text("builder_stage_output.txt", builder_text)

        builder_json = parse_json_safe(builder_text, "builder")
        builder_output = {
            "version": "0.1.0",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "agent_id": "builder_stage",
            "status": "completed",
            "output": builder_json,
        }
        save_json("builder_stage_output.json", builder_output)
    chain.add("builder_agent", "BUILD", {
        "stage": 4,
        "status": builder_output.get("status", "unknown"),
        "output_hash": hashlib.sha256(
            json.dumps(builder_output, sort_keys=True).encode()
        ).hexdigest(),
    })
    _emit_status(4, "Builder", "done", "Build tamamlandı.")

    # STAGE 5 — Post-build Auditor
    _emit_status(5, "Post-Build Auditor", "running", "Build çıktısı denetleniyor...")
    if builder_output.get("status") != "completed":
        post_build_audit: dict = {
            "audit_summary": "Builder skipped olduğu için post-build audit fallback çıktısı üretildi.",
            "issues": [
                {
                    "severity": "High",
                    "category": "Process",
                    "problem": "Builder stage skipped; build artifact not produced.",
                    "fix": "Apply Mizan required fixes and rerun the pipeline.",
                }
            ],
            "ready_for_build": False,
            "metadata": {
                "version": "0.1.0",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "agent_id": "post_build_audit_fallback",
            },
        }
        save_json("post_build_audit_output.json", post_build_audit)
        save_text(
            "post_build_audit_output.txt",
            json.dumps(post_build_audit, ensure_ascii=False, indent=2),
        )
    else:
        post_build_crew = build_post_build_auditor_crew(builder_output["output"])
        post_build_result = post_build_crew.kickoff()
        post_build_text = extract_result_text(post_build_result)
        save_text("post_build_audit_output.txt", post_build_text)

        post_build_audit = parse_json_safe(post_build_text, "post_build_audit")
        save_json("post_build_audit_output.json", post_build_audit)

    chain.add("post_build_auditor", "EVALUATE", {
        "stage": 5,
        "ready_for_build": post_build_audit.get("ready_for_build"),
        "issue_count": len(post_build_audit.get("issues", [])),
        "output_hash": hashlib.sha256(
            json.dumps(post_build_audit, sort_keys=True).encode()
        ).hexdigest(),
    })
    _emit_status(5, "Post-Build Auditor", "done", "Denetim tamamlandı.")

    # STAGE 6 — Final Mizan Gate
    _emit_status(6, "Final Gate", "running", "Final karar veriliyor...")

    # ✅ Human ALLOW aktifse escalation tetiklenmesin
    loops_completed = 0 if _human_allow_active(str(mizan_output.get("review_decision", "")).strip().lower()) else min(loop_index + 1, max_review_loops)

    final_gate_output = final_mizan_gate(
        mizan_output=mizan_output,
        build_output=builder_output,
        post_build_audit=post_build_audit,
        loop_index=loops_completed,
    )

    # ─── Final WitnessChain seal ───
    chain.add("final_gate", "SEAL", {
        "stage": 6,
        "decision": final_gate_output.get("decision"),
        "reason": final_gate_output.get("reason"),
    })

    # Inject chain_hash + ledger_seal into final gate output
    final_gate_output["witness_chain"] = {
        "chain_hash": chain.chain_hash,
        "entries": chain.count,
        "verified": chain.verify(),
        "ledger_seal": hashlib.sha256(
            (chain.chain_hash + "|" + final_gate_output.get("decision", "")).encode()
        ).hexdigest(),
    }

    save_json("final_gate_output.json", final_gate_output)

    # Save full witness chain artifact
    witness_chain_data = {
        "schema_version": "1.0.0",
        "engine": "crew_engine",
        "chain_hash": chain.chain_hash,
        "entries_count": chain.count,
        "verified": chain.verify(),
        "entries": chain.to_list(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    save_json("witness_chain_output.json", witness_chain_data)

    _emit_status(6, "Final Gate", "done", f"Karar: {final_gate_output.get('decision', '')}")

    return (
        architect_text,
        auditor_json,
        mizan_output,
        builder_output,
        post_build_audit,
        final_gate_output,
    )


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    docs_context = load_docs_context_for_goal()

    # Goal'ü terminalden verebilmek için:
    # python src/flows/orchestrator.py "my goal here"
    cli_goal = " ".join(sys.argv[1:]).strip()
    demo_goal = (
        cli_goal
        if cli_goal
        else (
            "Build a YARUKSAİ-governed multi-agent MVP that produces structured artifacts, "
            "audit outputs, and a final governance decision."
        )
    ) + docs_context

    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[ORCHESTRATOR] Run started at: {started_at}")

    try:
        (
            architect_output,
            auditor_output_json,
            mizan_output,
            builder_output,
            post_build_audit,
            final_gate_output,
        ) = run_six_stage_flow(demo_goal)
    except Exception as e:
        import traceback
        print("\n[ORCHESTRATOR ERROR]")
        print(str(e))
        print("\nİpucu: quota/rate limit görürsen billing/usage kontrol et.")
        traceback.print_exc()
        raise SystemExit(1)

    # Çıktıları terminale yaz
    print("\n" + "=" * 60)
    print("STAGE 1 — ARCHITECT OUTPUT")
    print("=" * 60)
    print(architect_output)

    print("\n" + "=" * 60)
    print("STAGE 2 — AUDITOR OUTPUT (PARSED JSON)")
    print("=" * 60)
    print(json.dumps(auditor_output_json, ensure_ascii=False, indent=2))

    print("\n" + "=" * 60)
    print("STAGE 3 — MIZAN OUTPUT (JSON)")
    print("=" * 60)
    print(json.dumps(mizan_output, ensure_ascii=False, indent=2))

    print("\n" + "=" * 60)
    print("STAGE 4 — BUILDER OUTPUT")
    print("=" * 60)
    print(json.dumps(builder_output, ensure_ascii=False, indent=2))

    print("\n" + "=" * 60)
    print("STAGE 5 — POST-BUILD AUDIT OUTPUT")
    print("=" * 60)
    print(json.dumps(post_build_audit, ensure_ascii=False, indent=2))

    print("\n" + "=" * 60)
    print("STAGE 6 — FINAL MIZAN GATE OUTPUT")
    print("=" * 60)
    print(json.dumps(final_gate_output, ensure_ascii=False, indent=2))

    print("\n" + "=" * 60)
    print("FILES SAVED")
    print("=" * 60)
    for fname in [
        "architect_stage_output.txt",
        "auditor_stage_output.txt",
        "auditor_stage_output_parsed.json",
        "mizan_stage_output.json",
        "builder_stage_output.txt",
        "builder_stage_output.json",
        "post_build_audit_output.txt",
        "post_build_audit_output.json",
        "final_gate_output.json",
        "approval_request.json",
        "approval_decision.json",
    ]:
        p = ARTIFACTS_DIR / fname
        if p.exists():
            print(p)
    print("=" * 60)
