import os

# LLM Provider ayarları
# "ollama" = ücretsiz local model, "openai" = ücretli OpenAI API
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
LLM_MODEL = os.getenv("LLM_MODEL", "ollama/llama3.1" if LLM_PROVIDER == "ollama" else "openai/gpt-4o").strip()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()


def get_llm():
    """
    CrewAI Agent'ları için LLM nesnesi döndürür.
    Desteklenen provider'lar: ollama, openai, anthropic
    """
    from crewai import LLM

    if LLM_PROVIDER == "ollama":
        return LLM(
            model=LLM_MODEL,
            base_url=OLLAMA_BASE_URL,
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


# Roller
ROLES = ["Owner", "Admin", "Reviewer", "Operator", "Viewer"]

# Karar Tipleri
DECISION_TYPES = {
    "AUTO": "AUTO",
    "REVIEW_REQUIRED": "REVIEW_REQUIRED",
    "REVIEW_OPTIONAL": "REVIEW_OPTIONAL",
}

# Timeout politikaları saniye cinsinden, environment variable ile konfigüre edilebilir
# Yoksa default kullanılır
TIMEOUTS = {
    "approval_wait": int(os.getenv("APPROVAL_WAIT_TIMEOUT", "300")),  # 5 dakika default
    "review_optional_timeout": int(os.getenv("REVIEW_OPTIONAL_TIMEOUT", "180")),  # 3 dakika default
}

# CLI concurrency lock ayarı
CLI_LOCK_FILE = os.getenv("CLI_LOCK_FILE", "/tmp/yaruksai_cli_lock.lock")


def load_environment(env_path: str = ".env") -> None:
    """
    .env dosyasını varsa yükler (python-dotenv kuruluysa).
    Kurulu değilse sessizce geçer.
    """
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(env_path, override=False)
    except Exception:
        return


def validate_env_keys(required_keys=None) -> None:
    """
    Gerekli ENV anahtarlarını kontrol eder; yoksa RuntimeError atar.
    Ollama kullanılıyorsa API key gerekmez.
    """
    if LLM_PROVIDER == "ollama":
        return  # Ollama local çalışır, API key gerekmez

    if required_keys is None:
        if LLM_PROVIDER == "anthropic":
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
