"""
YARUKSAİ — 6-Stage Pipeline Orchestrator (Server-Adapted)
─────────────────────────────────────────────────────────────
Adaptations from CLI version:
  - ARTIFACTS_DIR is per-run (passed as parameter)
  - event_callback for SSE stage notifications
  - Optional context dict (council_verdict etc.)
  - save_text/save_json use the per-run directory
  - Builder never executes commands (prompt-only output)
"""
import sys
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple
import time

# src klasörünü Python path'e ekle
SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from crewai import Agent, Task, Crew, Process, LLM

from config import load_environment, validate_env_keys, get_crewai_llm_config, get_hybrid_llm_configs, print_env_status
from agents.prompts import (
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
    POST_BUILD_AUDITOR_ROLE,
    POST_BUILD_AUDITOR_GOAL,
    POST_BUILD_AUDITOR_BACKSTORY,
    POST_BUILD_AUDITOR_TASK_TEMPLATE,
    FINAL_MIZAN_GATE_TEMPLATE,
)
from flows.mizan_engine import run_mizan_engine
from flows.context_memory import ContextMemory, IntegrityError

# Type alias
EventCallback = Optional[Callable[[Dict[str, Any]], None]]


def parse_json_safe(raw_text: str, stage_name: str = "unknown") -> dict:
    """
    LLM/Crew çıktısından güvenli şekilde JSON parse eder.
    Tolerates: markdown code blocks, surrounding text, whitespace.
    """
    if raw_text is None:
        raise ValueError(f"[{stage_name}] Boş çıktı (None) geldi.")

    text = str(raw_text).strip()

    # 1) Direkt dene
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2) ```json ... ``` kod bloğundan çıkar
    codeblock_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if codeblock_match:
        candidate = codeblock_match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 3) Metin içinde ilk { ile son } arasını al
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace:last_brace + 1].strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            preview = candidate[:500].replace("\n", "\\n")
            raise ValueError(
                f"[{stage_name}] JSON bulundu ama parse edilemedi: {e}. "
                f"Candidate preview: {preview}"
            ) from e

    preview = text[:500].replace("\n", "\\n")
    raise ValueError(f"[{stage_name}] Çıktı içinde JSON bulunamadı. Preview: {preview}")


def extract_result_text(result) -> str:
    """CrewAI result objesinden mümkün olan en temiz metni çıkarır."""
    return (
        getattr(result, "raw", None)
        or getattr(result, "output", None)
        or str(result)
    )


def save_text(artifacts_dir: Path, filename: str, content: str) -> None:
    (artifacts_dir / filename).write_text(content, encoding="utf-8")


def save_json(artifacts_dir: Path, filename: str, data: dict) -> None:
    (artifacts_dir / filename).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _emit(callback: EventCallback, event: Dict[str, Any]) -> None:
    """Fire event callback if provided (best-effort, never raises)."""
    if callback:
        try:
            callback(event)
        except Exception:
            pass


def _kickoff_with_retry(crew: "Crew", max_retries: int = 3, base_wait: float = 2.0, fallback_llm=None):
    """
    Crew.kickoff() with rate limit retry + Ollama fallback.
    
    1. İlk deneme: mevcut LLM ile (Groq)
    2. Rate limit → bekle + tekrar dene  
    3. Tüm denemeler başarısız → fallback_llm (Ollama) ile tekrar dene
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            return crew.kickoff()
        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            if "rate" in err_str and "limit" in err_str:
                wait_time = base_wait * (attempt + 1)
                print(f"[YARUKSAİ] Rate limit — {wait_time}s bekleniyor (deneme {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
            else:
                raise
    
    # Tüm Groq denemeleri başarısız → Ollama fallback
    if fallback_llm is not None:
        print(f"[YARUKSAİ] Groq rate limit aşıldı → Ollama fallback aktif")
        for agent in crew.agents:
            agent.llm = fallback_llm
        try:
            return crew.kickoff()
        except Exception as e2:
            print(f"[YARUKSAİ] Ollama fallback da başarısız: {e2}")
            raise
    
    raise last_error


def mizan_guard(output: str, stage_name: str, min_length: int = 20) -> str:
    """
    Mizan Guard — LLM çıktı doğrulayıcı.
    
    Her stage çıktısını kontrol eder:
    1. Boş/None çıktı → hata
    2. Çok kısa çıktı → uyarı + devam
    3. Encoding bozukluğu → temizle
    4. Tekrarlayan halüsinasyon → hata
    
    "Acımasız Gerçeklik" — çöp girdi kabul edilmez.
    """
    if output is None:
        print(f"[MİZAN GUARD] ❌ {stage_name}: NULL çıktı — stage başarısız")
        raise ValueError(f"Mizan Guard: {stage_name} returned None")
    
    text = str(output).strip()
    
    if len(text) == 0:
        print(f"[MİZAN GUARD] ❌ {stage_name}: Boş çıktı")
        raise ValueError(f"Mizan Guard: {stage_name} returned empty output")
    
    if len(text) < min_length:
        print(f"[MİZAN GUARD] ⚠️ {stage_name}: Kısa çıktı ({len(text)} karakter)")
        # Kısa ama boş değil — devam et, uyar
    
    # Tekrarlayan halüsinasyon tespiti (aynı kelime 10+ kez tekrar)
    words = text.split()
    if len(words) > 20:
        from collections import Counter
        word_counts = Counter(words)
        most_common_word, most_common_count = word_counts.most_common(1)[0]
        ratio = most_common_count / len(words)
        if ratio > 0.4 and len(most_common_word) > 3:
            print(f"[MİZAN GUARD] ⚠️ {stage_name}: Olası halüsinasyon — '{most_common_word}' %{ratio*100:.0f} tekrar")
    
    # Encoding temizliği
    text = text.replace("\x00", "").replace("\ufffd", "")
    
    print(f"[MİZAN GUARD] ✅ {stage_name}: {len(text)} karakter — geçti")
    return text


def build_revised_project_goal(base_goal: str, mizan_output: dict, loop_index: int) -> str:
    """Revize turu için project goal metnini güçlendirir."""
    accepted = mizan_output.get("accepted_fixes", []) or []
    if not accepted:
        return base_goal

    fix_lines = []
    for i, item in enumerate(accepted, start=1):
        sev = str(item.get("severity", "")).upper()
        cat = str(item.get("category", ""))
        fix = str(item.get("fix", ""))
        fix_lines.append(f"{i}. [{sev}][{cat}] {fix}")

    return (
        f"{base_goal}\n\n"
        f"REVISION LOOP: {loop_index}\n"
        "Aşağıdaki Mizan düzeltmelerini bu turda özellikle uygula:\n"
        + "\n".join(fix_lines)
    )


def _enrich_goal_with_context(goal: str, context: Optional[Dict[str, Any]]) -> str:
    """Council verdict ve diğer context bilgilerini goal'a enjekte eder."""
    if not context:
        context = {}

    parts = [goal]

    # Kolektif Hafıza — benzer geçmiş kararları getir
    try:
        from memory import recall_similar, format_memories_for_prompt
        memories = recall_similar(goal, top_k=3)
        if memories:
            mem_text = format_memories_for_prompt(memories)
            parts.append(f"\n\n{mem_text}")
            print(f"[HAFIZA] 🧠 {len(memories)} benzer geçmiş karar bulundu")
    except Exception:
        pass  # Hafıza yoksa sessizce devam

    council = context.get("council_verdict")
    if council:
        sigma = council.get("sigma", "N/A")
        parts.append(
            f"\n\nCOUNCIL VERDICT (from YARUKSAİ 7AI Shūrā):\n"
            f"- Sigma Score: {sigma}\n"
            f"- Full verdict: {json.dumps(council, ensure_ascii=False)}"
        )

    user_id = context.get("user_id")
    if user_id:
        parts.append(f"\nUSER_ID: {user_id}")

    source = context.get("source")
    if source:
        parts.append(f"\nSOURCE: {source}")

    return "\n".join(parts)


# ─── Crew Builders ────────────────────────────────────────────

def build_architect_crew(project_goal: str, llm: LLM = None) -> Crew:
    architect_agent = Agent(
        role=CHATGPT_ARCHITECT_ROLE,
        goal=CHATGPT_ARCHITECT_GOAL,
        backstory=CHATGPT_ARCHITECT_BACKSTORY,
        verbose=True,
        allow_delegation=False,
        llm=llm,
    )
    architect_task = Task(
        description=CHATGPT_ARCHITECT_TASK_TEMPLATE.format(project_goal=project_goal),
        expected_output=(
            "A clear project draft with sections: project name, goal, assumptions, "
            "architecture, file tree, implementation plan, risks, and open questions."
        ),
        agent=architect_agent,
    )
    return Crew(agents=[architect_agent], tasks=[architect_task], process=Process.sequential, verbose=True)


def build_auditor_crew(architect_output: str, llm: LLM = None) -> Crew:
    auditor_agent = Agent(
        role=GEMINI_AUDITOR_ROLE,
        goal=GEMINI_AUDITOR_GOAL,
        backstory=GEMINI_AUDITOR_BACKSTORY,
        verbose=True,
        allow_delegation=False,
        llm=llm,
    )
    auditor_task = Task(
        description=GEMINI_AUDITOR_TASK_TEMPLATE.format(architect_output=architect_output),
        expected_output=(
            "Structured audit feedback with: audit_summary, issues, "
            "cost_efficiency_review, legal_compliance_review, market_viability_note, "
            "ready_for_build."
        ),
        agent=auditor_agent,
    )
    return Crew(agents=[auditor_agent], tasks=[auditor_task], process=Process.sequential, verbose=True)


def build_builder_crew(project_goal: str, architect_output: str, auditor_json: dict, mizan_output: dict, llm: LLM = None) -> Crew:
    """Builder stage — SADECE metin/kod çıktısı üretir, ASLA komut çalıştırmaz."""
    builder_agent = Agent(
        role=CLAUDE_BUILDER_ROLE,
        goal=CLAUDE_BUILDER_GOAL,
        backstory=CLAUDE_BUILDER_BACKSTORY,
        verbose=True,
        allow_delegation=False,
        llm=llm,
    )

    context_packet = mizan_output.get("yaruksai_context_packet", {})
    accepted_fixes = mizan_output.get("accepted_fixes", [])
    builder_instructions = mizan_output.get("builder_instructions", "")

    task_description = f"""{CLAUDE_BUILDER_TASK_TEMPLATE}

PROJECT GOAL
{project_goal}

ARCHITECT OUTPUT
{architect_output}

AUDITOR OUTPUT (JSON)
{json.dumps(auditor_json, ensure_ascii=False, indent=2)}

MIZAN CONTEXT PACKET (JSON)
{json.dumps(context_packet, ensure_ascii=False, indent=2)}

MERGED SPEC (GENERATED FOR BUILDER)
{json.dumps({
    "project_goal": project_goal,
    "architect_output": architect_output,
    "accepted_fixes": accepted_fixes,
    "constraints": context_packet.get("top_constraints", []),
}, ensure_ascii=False, indent=2)}

ACCEPTED FIXES (JSON)
{json.dumps(accepted_fixes, ensure_ascii=False, indent=2)}

BUILDER INSTRUCTIONS (FROM MIZAN)
{builder_instructions}

KRİTİK GÜVENLİK KURALLARI
- ASLA subprocess, os.system, exec, eval veya benzeri komut çalıştırma.
- Sadece Mizan tarafından kabul edilmiş düzeltmeleri uygula.
- Çıktı YALNIZCA metin/kod olmalı, çalıştırılabilir komut DEĞİL.
- Cost-efficiency, legal/compliance, maintainability konularını göz ardı etme.
- Çıktında metadata bulunsun: version, timestamp, agent_id.
"""

    builder_task = Task(
        description=task_description,
        expected_output=(
            "JSON builder output with build_summary, files_created_or_updated, "
            "code_notes, tests_added, known_limits, next_steps."
        ),
        agent=builder_agent,
    )
    return Crew(agents=[builder_agent], tasks=[builder_task], process=Process.sequential, verbose=True)


def build_post_build_auditor_crew(build_output_json: dict, llm: LLM = None) -> Crew:
    post_auditor_agent = Agent(
        role=POST_BUILD_AUDITOR_ROLE,
        goal=POST_BUILD_AUDITOR_GOAL,
        backstory=POST_BUILD_AUDITOR_BACKSTORY,
        verbose=True,
        allow_delegation=False,
        llm=llm,
    )
    post_auditor_task = Task(
        description=POST_BUILD_AUDITOR_TASK_TEMPLATE.format(
            build_output=json.dumps(build_output_json, ensure_ascii=False, indent=2)
        ),
        expected_output="JSON with audit_summary, issues, ready_for_build",
        agent=post_auditor_agent,
    )
    return Crew(agents=[post_auditor_agent], tasks=[post_auditor_task], process=Process.sequential, verbose=True)


def final_mizan_gate(mizan_output: dict, build_output: dict, post_build_audit: dict, loop_index: int = 0) -> dict:
    """Final karar katmanı (Python rule-based). Deterministic."""
    max_loops = int(
        mizan_output.get("max_review_loops")
        or mizan_output.get("yaruksai_context_packet", {}).get("policy_flags", {}).get("max_review_loops", 3)
        or 3
    )

    meta = {
        "version": "0.1.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "agent_id": "final_mizan_gate_python",
    }

    if loop_index >= max_loops:
        return {"decision": "human_escalation", "reason": f"Loop limit reached ({loop_index}/{max_loops})",
                "next_action": "Hand over to human reviewer with full artifacts", "metadata": meta}

    if build_output.get("status") == "skipped":
        return {"decision": "revise", "reason": "Builder stage skipped because Mizan did not approve build",
                "next_action": "Apply Mizan required fixes and rerun pipeline", "metadata": meta}

    issues = post_build_audit.get("issues", []) or []
    severities = [str(x.get("severity", "")).strip().lower() for x in issues]

    if "high" in severities:
        return {"decision": "revise", "reason": "Post-build audit contains high severity issue(s)",
                "next_action": "Fix high severity issues and rerun post-build validation", "metadata": meta}

    if post_build_audit.get("ready_for_build") is False:
        return {"decision": "revise", "reason": "Post-build audit marked output as not ready",
                "next_action": "Apply post-build audit fixes and rerun", "metadata": meta}

    return {"decision": "complete", "reason": "No blocking issues found; output is ready",
            "next_action": "Proceed to packaging / handoff", "metadata": meta}


# ─── Main Entry Point ─────────────────────────────────────────

def run_six_stage_flow(
    project_goal: str,
    *,
    artifacts_dir: Optional[Path] = None,
    context: Optional[Dict[str, Any]] = None,
    event_callback: EventCallback = None,
) -> Tuple[str, str, dict, dict, dict, dict]:
    """
    6-stage pipeline. Server mode adaptations:
    - artifacts_dir: per-run output directory
    - context: optional dict with council_verdict, user_id, source
    - event_callback: called with stage events for SSE streaming
    """
    load_environment()
    validate_env_keys()

    # YARUKSAİ Hibrit Motor — stage bazlı LLM seçimi
    hybrid_configs = get_hybrid_llm_configs()
    default_cfg = hybrid_configs.get("default", get_crewai_llm_config())
    print(f"\n[YARUKSAİ] Hibrit Motor Aktif")
    for stage_name, cfg in hybrid_configs.items():
        if stage_name != "default":
            print(f"  {stage_name:20s} → {cfg['provider']:6s} | {cfg['model']}")
    print_env_status()

    # LLM factory — config dict'ten CrewAI LLM nesnesi üretir
    def make_llm(cfg: dict) -> LLM:
        if cfg["provider"] == "ollama":
            return LLM(model=cfg["model"], base_url=cfg["base_url"])
        elif cfg.get("api_key"):
            return LLM(model=cfg["model"], api_key=cfg["api_key"])
        else:
            return LLM(model=cfg["model"])

    # Stage-specific LLM instances
    llm_architect = make_llm(hybrid_configs.get("architect", default_cfg))
    llm_auditor = make_llm(hybrid_configs.get("auditor", default_cfg))
    llm_builder = make_llm(hybrid_configs.get("builder", default_cfg))
    llm_post_audit = make_llm(hybrid_configs.get("post_build_auditor", default_cfg))

    # Ollama fallback — Groq rate limit'te otomatik geçiş
    ollama_cfg = hybrid_configs.get("mizan", default_cfg)  # mizan her zaman Ollama
    llm_fallback = make_llm(ollama_cfg)
    print(f"  {'fallback':20s} → {ollama_cfg['provider']:6s} | {ollama_cfg['model']}")

    # Per-run artifacts directory
    if artifacts_dir is None:
        artifacts_dir = Path(__file__).resolve().parent.parent.parent / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # ── CHECKPOINT: Kara Kutu Sistemi ───────────────────────────
    checkpoint_path = artifacts_dir / "checkpoint.json"

    def _save_checkpoint(stage: str, data: dict):
        """Stage tamamlandığında durumu diske mühürle."""
        existing = _load_checkpoint()
        existing["completed_stages"].append(stage)
        existing["last_completed_stage"] = stage
        existing["stage_data"][stage] = data
        existing["timestamp"] = time.time()
        existing["status"] = "running"
        checkpoint_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[CHECKPOINT] 💾 {stage} mühürlendi")

    def _load_checkpoint() -> dict:
        """Varsa checkpoint oku, yoksa boş yapı döndür."""
        if checkpoint_path.exists():
            try:
                return json.loads(checkpoint_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "completed_stages": [],
            "last_completed_stage": None,
            "stage_data": {},
            "timestamp": time.time(),
            "status": "starting",
        }

    def _stage_done(stage: str) -> bool:
        """Bu stage daha önce tamamlanmış mı?"""
        cp = _load_checkpoint()
        return stage in cp.get("completed_stages", [])

    def _restore_stage(stage: str):
        """Tamamlanmış stage'in çıktısını diskten oku."""
        cp = _load_checkpoint()
        return cp.get("stage_data", {}).get(stage, {})
    # ────────────────────────────────────────────────────────────

    # ── CONTEXT MEMORY: SHA-256 Verified State Store ────────────
    ctx = ContextMemory(artifacts_dir)
    print(f"[YARUKSAİ] 🛡️ ContextMemory aktif — SHA-256 handoff doğrulama")

    # Enrich goal with council verdict and other context
    enriched_goal = _enrich_goal_with_context(project_goal, context)

    # Revision loop
    current_goal = enriched_goal
    max_review_loops = 1
    architect_text = ""
    auditor_text = ""
    auditor_json: dict = {}
    mizan_output: dict = {}
    loop_index = 0

    for loop_index in range(max_review_loops + 1):
        _emit(event_callback, {"type": "stage_started", "stage": "review_loop", "loop_index": loop_index})

        # STAGE 1 — Architect
        if _stage_done(f"architect_loop{loop_index}"):
            restored = _restore_stage(f"architect_loop{loop_index}")
            architect_text = restored.get("text", "")
            print(f"[CHECKPOINT] ⏩ ARCHITECT (loop {loop_index}) — diskten yüklendi")
            _emit(event_callback, {"type": "stage_completed", "stage": "architect", "loop_index": loop_index})
        else:
            _emit(event_callback, {"type": "stage_started", "stage": "architect", "loop_index": loop_index})
            architect_crew = build_architect_crew(current_goal, llm=llm_architect)
            architect_result = _kickoff_with_retry(architect_crew, fallback_llm=llm_fallback)
            architect_text = extract_result_text(architect_result)
            architect_text = mizan_guard(architect_text, "ARCHITECT")
            save_text(artifacts_dir, "architect_stage_output.txt", architect_text)
            _save_checkpoint(f"architect_loop{loop_index}", {"text": architect_text})
            ctx.store(f"architect_loop{loop_index}", architect_text)
            _emit(event_callback, {"type": "stage_completed", "stage": "architect", "loop_index": loop_index})

        # STAGE 2 — Auditor
        if _stage_done(f"auditor_loop{loop_index}"):
            restored = _restore_stage(f"auditor_loop{loop_index}")
            auditor_text = restored.get("text", "")
            auditor_json = restored.get("json", {})
            print(f"[CHECKPOINT] ⏩ AUDITOR (loop {loop_index}) — diskten yüklendi")
            _emit(event_callback, {"type": "stage_completed", "stage": "auditor", "loop_index": loop_index})
        else:
            _emit(event_callback, {"type": "stage_started", "stage": "auditor", "loop_index": loop_index})
            auditor_crew = build_auditor_crew(architect_text, llm=llm_auditor)
            auditor_result = _kickoff_with_retry(auditor_crew, fallback_llm=llm_fallback)
            auditor_text = extract_result_text(auditor_result)
            auditor_text = mizan_guard(auditor_text, "AUDITOR")
            save_text(artifacts_dir, "auditor_stage_output.txt", auditor_text)
            auditor_json = parse_json_safe(auditor_text, "auditor")
            save_json(artifacts_dir, "auditor_stage_output_parsed.json", auditor_json)
            _save_checkpoint(f"auditor_loop{loop_index}", {"text": auditor_text, "json": auditor_json})
            ctx.store(f"auditor_loop{loop_index}", {"text": auditor_text, "json": auditor_json})
            _emit(event_callback, {"type": "stage_completed", "stage": "auditor", "loop_index": loop_index})

        # STAGE 3 — Mizan 
        if _stage_done(f"mizan_loop{loop_index}"):
            restored = _restore_stage(f"mizan_loop{loop_index}")
            mizan_output = restored.get("output", {})
            print(f"[CHECKPOINT] ⏩ MIZAN (loop {loop_index}) — diskten yüklendi")
            _emit(event_callback, {
                "type": "stage_completed", "stage": "mizan", "loop_index": loop_index,
                "review_decision": str(mizan_output.get("review_decision", "")),
                "mizan_score": mizan_output.get("mizan_score"),
            })
        else:
            _emit(event_callback, {"type": "stage_started", "stage": "mizan", "loop_index": loop_index})
            mizan_output = run_mizan_engine(
                architect_output_text=architect_text,
                auditor_output_text=json.dumps(auditor_json, ensure_ascii=False, indent=2),
                review_loop_count=loop_index,
            )
            save_json(artifacts_dir, "mizan_stage_output.json", mizan_output)
            _save_checkpoint(f"mizan_loop{loop_index}", {"output": mizan_output})
            ctx.store(f"mizan_loop{loop_index}", mizan_output)
            _emit(event_callback, {
                "type": "stage_completed", "stage": "mizan", "loop_index": loop_index,
                "review_decision": str(mizan_output.get("review_decision", "")),
                "mizan_score": mizan_output.get("mizan_score"),
            })

        review_decision = str(mizan_output.get("review_decision", "")).strip().lower()
        if review_decision == "approve_for_build":
            break
        if loop_index >= max_review_loops:
            break

        current_goal = build_revised_project_goal(
            base_goal=enriched_goal, mizan_output=mizan_output, loop_index=loop_index + 1,
        )

    # STAGE 4 — Builder
    if _stage_done("builder"):
        restored = _restore_stage("builder")
        builder_output = restored.get("output", {})
        print(f"[CHECKPOINT] ⏩ BUILDER — diskten yüklendi")
        _emit(event_callback, {"type": "stage_completed", "stage": "builder", "status": builder_output.get("status")})
    else:
        _emit(event_callback, {"type": "stage_started", "stage": "builder"})
        review_decision = str(mizan_output.get("review_decision", "")).strip().lower()

        if review_decision != "approve_for_build":
            builder_output: dict = {
                "version": "0.1.0", "timestamp": datetime.utcnow().isoformat() + "Z",
                "agent_id": "orchestrator", "status": "skipped",
                "reason": f"Mizan decision is '{review_decision}'",
                "next_action": "Apply required fixes and rerun architect/auditor/mizan loop",
            }
            save_json(artifacts_dir, "builder_stage_output.json", builder_output)
            save_text(artifacts_dir, "builder_stage_output.txt",
                      f"[BUILDER SKIPPED]\nMizan decision: {review_decision}\n")
        else:
            builder_crew = build_builder_crew(current_goal, architect_text, auditor_json, mizan_output, llm=llm_builder)
            builder_result = _kickoff_with_retry(builder_crew, fallback_llm=llm_fallback)
            builder_text = extract_result_text(builder_result)
            builder_text = mizan_guard(builder_text, "BUILDER")
            save_text(artifacts_dir, "builder_stage_output.txt", builder_text)
            builder_json = parse_json_safe(builder_text, "builder")
            builder_output = {
                "version": "0.1.0", "timestamp": datetime.utcnow().isoformat() + "Z",
                "agent_id": "builder_stage", "status": "completed", "output": builder_json,
            }
            save_json(artifacts_dir, "builder_stage_output.json", builder_output)

        _save_checkpoint("builder", {"output": builder_output})
        ctx.store("builder", builder_output)
        _emit(event_callback, {"type": "stage_completed", "stage": "builder", "status": builder_output.get("status")})

    # STAGE 5 — Post-build Auditor
    if _stage_done("post_build_auditor"):
        restored = _restore_stage("post_build_auditor")
        post_build_audit = restored.get("output", {})
        print(f"[CHECKPOINT] ⏩ POST-AUDIT — diskten yüklendi")
        _emit(event_callback, {"type": "stage_completed", "stage": "post_build_auditor"})
    else:
        _emit(event_callback, {"type": "stage_started", "stage": "post_build_auditor"})
        if builder_output.get("status") != "completed":
            post_build_audit: dict = {
                "audit_summary": "Builder skipped olduğu için post-build audit fallback çıktısı üretildi.",
                "issues": [{"severity": "High", "category": "Process",
                            "problem": "Builder stage skipped; build artifact not produced.",
                            "fix": "Apply Mizan required fixes and rerun the pipeline."}],
                "ready_for_build": False,
                "metadata": {"version": "0.1.0", "timestamp": datetime.utcnow().isoformat() + "Z",
                              "agent_id": "post_build_audit_fallback"},
            }
        else:
            post_build_crew = build_post_build_auditor_crew(builder_output["output"], llm=llm_post_audit)
            post_build_result = _kickoff_with_retry(post_build_crew, fallback_llm=llm_fallback)
            post_build_text = extract_result_text(post_build_result)
            save_text(artifacts_dir, "post_build_audit_output.txt", post_build_text)
            post_build_audit = parse_json_safe(post_build_text, "post_build_audit")

        save_json(artifacts_dir, "post_build_audit_output.json", post_build_audit)
        _save_checkpoint("post_build_auditor", {"output": post_build_audit})
        ctx.store("post_build_auditor", post_build_audit)
        _emit(event_callback, {"type": "stage_completed", "stage": "post_build_auditor"})

    # STAGE 6 — Final Mizan Gate
    _emit(event_callback, {"type": "stage_started", "stage": "final_gate"})
    final_gate_output = final_mizan_gate(
        mizan_output=mizan_output, build_output=builder_output,
        post_build_audit=post_build_audit, loop_index=min(loop_index, max_review_loops),
    )
    save_json(artifacts_dir, "final_gate_output.json", final_gate_output)
    
    # Final checkpoint — pipeline tamam
    _save_checkpoint("final_gate", {"output": final_gate_output})
    # Checkpoint'i "completed" olarak mühürle
    final_cp = _load_checkpoint()
    final_cp["status"] = "completed"
    checkpoint_path.write_text(
        json.dumps(final_cp, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[CHECKPOINT] 🏁 Pipeline tamamlandı — checkpoint mühürlendi")
    
    # ── CONTEXT MEMORY: Zincir Doğrulaması ──────────────────────
    try:
        chain_report = ctx.verify_chain()
        save_json(artifacts_dir, "context_chain_report.json", chain_report)
        print(f"[YARUKSAİ] ✅ Context chain integrity VERIFIED — {chain_report['stages']} stages")
    except IntegrityError as e:
        print(f"[YARUKSAİ] ❌ CONTEXT CHAIN INTEGRITY FAILURE: {e}")
        # Ledger'a yaz ama pipeline'u durdurma (veri zaten kaydedildi)

    _emit(event_callback, {
        "type": "stage_completed", "stage": "final_gate",
        "decision": final_gate_output.get("decision"),
    })

    return architect_text, auditor_text, mizan_output, builder_output, post_build_audit, final_gate_output
