"""
app/model_config.py — Hardcoded Model Assignments
═══════════════════════════════════════════════════════

Sprint 1 — Foundation Stability
CEO Spec §7: Model kararı geri alınamaz — hardcode, env ile değiştirilemez.

Opus 4.6  → sadece Codegen_Agent
Sonnet 4.6 → diğer tüm ajanlar

⚠  Değişiklik için CEO onayı gerekli. Pull request açılamaz.
"""

# ════════════════════════════════════════════════════════
#  CEO ONAYLI MODEL ATAMALARI — DEĞİŞTİRİLEMEZ
# ════════════════════════════════════════════════════════

# fmt: off
AGENT_MODELS = {
    # Sprint 1 ajanları
    "Architect_Agent":  "claude-sonnet-4-6",    # Spec üretimi — Sonnet yeterli
    "Review_Agent":     "claude-sonnet-4-6",    # Kural karşılaştırma — deterministik
    "Approval_Agent":   "claude-sonnet-4-6",    # Ledger formatı sabit — Opus israf
    "Codegen_Agent":    "claude-opus-4-6",      # Kod kalitesi kritik — Opus ZORUNLU

    # FEAM OS / Şura ajanları
    "Celali":           "claude-sonnet-4-6",    # Adalet perspektifi
    "Cemali":           "claude-sonnet-4-6",    # Merhamet perspektifi
    "Kemali":           "claude-sonnet-4-6",    # Bilgelik perspektifi
    "EmanetAgent":      "claude-sonnet-4-6",    # Otonom karar — Sonnet yeterli
}
# fmt: on

# ════════════════════════════════════════════════════════
#  IMMUTABLE ACCESS FUNCTION
# ════════════════════════════════════════════════════════

def get_model(agent_name: str) -> str:
    """
    Get the assigned model for an agent.

    This function enforces CEO-approved model assignments.
    Model strings are hardcoded and cannot be overridden via
    environment variables or configuration files.

    Raises AssertionError if agent not found — this is intentional,
    not a bug. New agents must be explicitly assigned.
    """
    assert agent_name in AGENT_MODELS, (
        f"Agent '{agent_name}' has no CEO-approved model assignment. "
        f"Known agents: {list(AGENT_MODELS.keys())}. "
        f"CEO onayı olmadan yeni ajan modeli atanamaz."
    )
    return AGENT_MODELS[agent_name]


def is_opus(agent_name: str) -> bool:
    """Check if agent uses Opus (premium model)."""
    return "opus" in get_model(agent_name).lower()


def get_all_assignments() -> dict:
    """Return all model assignments for health check / status."""
    return {
        name: {
            "model": model,
            "tier": "opus" if "opus" in model else "sonnet",
        }
        for name, model in AGENT_MODELS.items()
    }
