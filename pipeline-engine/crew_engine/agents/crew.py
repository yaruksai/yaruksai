from __future__ import annotations

# CrewAI Cloud, bu dosyayı "entry" gibi arıyor.
# Biz burada kendi orchestrator akışımızı çalıştırıyoruz.

from flows.orchestrator import run_six_stage_flow, load_docs_context_for_goal

def kickoff(inputs: dict | None = None) -> dict:
    """
    CrewAI Cloud/AMP kickoff entrypoint.
    inputs: platformdan gelebilecek opsiyonel parametreler.
    """
    docs_context = load_docs_context_for_goal()

    # İstersen platformdan goal override edebiliriz:
    user_goal = ""
    if isinstance(inputs, dict):
        user_goal = str(inputs.get("goal", "") or "").strip()

    demo_goal = (
        user_goal
        if user_goal
        else "Build a YARUKSAİ-governed multi-agent MVP that produces structured artifacts, audit outputs, and a final governance decision."
    ) + docs_context

    (
        _architect_output,
        _auditor_output_json,
        _mizan_output,
        _builder_output,
        _post_build_audit,
        final_gate_output,
    ) = run_six_stage_flow(demo_goal)

    return final_gate_output
