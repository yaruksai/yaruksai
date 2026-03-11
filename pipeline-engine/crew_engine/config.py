import os

# ─── LLM Provider Configuration ─────────────────────────────────
# Supported: "groq", "ollama", "openai", "anthropic"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()

# Resolve LLM_MODEL based on provider
if not LLM_MODEL:
    if LLM_PROVIDER == "groq":
        LLM_MODEL = f"groq/{GROQ_MODEL}"
    elif LLM_PROVIDER == "ollama":
        LLM_MODEL = os.getenv("OLLAMA_MODEL", "ollama/gemma3:1b")
    elif LLM_PROVIDER == "anthropic":
        LLM_MODEL = "anthropic/claude-3-5-sonnet-20241022"
    else:
        LLM_MODEL = "openai/gpt-4o"


def get_llm():
    """
    Return a CrewAI LLM instance for the configured provider.
    Supported: groq, ollama, openai, anthropic
    """
    from crewai import LLM

    if LLM_PROVIDER == "ollama":
        return LLM(
            model=LLM_MODEL,
            base_url=OLLAMA_BASE_URL,
        )
    elif LLM_PROVIDER == "groq":
        return LLM(
            model=LLM_MODEL,
            api_key=os.getenv("GROQ_API_KEY", ""),
        )
    elif LLM_PROVIDER == "anthropic":
        return LLM(
            model=LLM_MODEL,
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        )
    else:
        return LLM(
            model=LLM_MODEL,
            api_key=os.getenv("OPENAI_API_KEY", ""),
        )


# Roles
ROLES = ["Owner", "Admin", "Reviewer", "Operator", "Viewer"]

# Decision Types
DECISION_TYPES = {
    "AUTO": "AUTO",
    "REVIEW_REQUIRED": "REVIEW_REQUIRED",
    "REVIEW_OPTIONAL": "REVIEW_OPTIONAL",
}

# Timeouts (seconds), configurable via env
TIMEOUTS = {
    "approval_wait": int(os.getenv("APPROVAL_WAIT_TIMEOUT", "300")),
    "review_optional_timeout": int(os.getenv("REVIEW_OPTIONAL_TIMEOUT", "180")),
}

CLI_LOCK_FILE = os.getenv("CLI_LOCK_FILE", "/tmp/yaruksai_cli_lock.lock")


def load_environment(env_path: str = ".env") -> None:
    """Load .env file if python-dotenv is available."""
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
    except Exception:
        return


def validate_env_keys(required_keys=None) -> None:
    """Validate required API keys based on provider."""
    if LLM_PROVIDER == "ollama":
        return

    if required_keys is None:
        if LLM_PROVIDER == "groq":
            required_keys = ["GROQ_API_KEY"]
        elif LLM_PROVIDER == "anthropic":
            required_keys = ["ANTHROPIC_API_KEY"]
        else:
            required_keys = ["OPENAI_API_KEY"]

    missing = [k for k in required_keys if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Set them in your shell or in a .env file."
        )

