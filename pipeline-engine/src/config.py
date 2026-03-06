"""
YARUKSAİ — LLM Konfigürasyonu (Pipeline Engine)
─────────────────────────────────────────────────────
Fallback zinciri: Ollama (kendi) → Groq (hızlı) → OpenAI (yedek)
Her şey YARUKSAİ'nin kendi beyni olacak.
"""
from __future__ import annotations

import os
import warnings
from typing import Dict, List, Optional

from dotenv import load_dotenv


# ─── Desteklenen LLM Provider'lar ─────────────────────────────

OLLAMA_MODELS = {
    "gemma3:1b": "ollama/gemma3:1b",
    "phi4-mini": "ollama/phi4-mini",
    "llama3.2:3b": "ollama/llama3.2:3b",
    "mistral:7b": "ollama/mistral:7b",
    "llama3.3:8b": "ollama/llama3.3:8b",
}

GROQ_MODELS = {
    "llama-3.3-70b-versatile": "groq/llama-3.3-70b-versatile",
    "mixtral-8x7b-32768": "groq/mixtral-8x7b-32768",
}


# ─── Config Loader ────────────────────────────────────────────

def load_environment() -> None:
    """Load variables from .env file into environment."""
    load_dotenv()


def get_ollama_base_url() -> str:
    """Ollama API URL (container içinden host'a erişim)."""
    return os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")


def get_ollama_model() -> str:
    """Ollama model adı."""
    return os.getenv("OLLAMA_MODEL", "gemma3:1b")


def get_groq_model() -> str:
    """Groq model adı."""
    return os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")


def get_fallback_chain() -> List[str]:
    """
    LLM fallback zinciri.
    Varsayılan: ollama → groq (kendi LLM'imiz önce!)
    """
    chain = os.getenv("LLM_FALLBACK_CHAIN", "ollama,groq")
    return [p.strip().lower() for p in chain.split(",") if p.strip()]


def get_active_provider() -> str:
    """İlk kullanılabilir provider'ı döndürür."""
    chain = get_fallback_chain()

    for provider in chain:
        if provider == "ollama":
            # Ollama her zaman varsayılan olarak mevcut
            return "ollama"
        elif provider == "groq":
            if os.getenv("GROQ_API_KEY", "").strip():
                return "groq"
        elif provider == "openai":
            if os.getenv("OPENAI_API_KEY", "").strip():
                return "openai"
        elif provider == "anthropic":
            if os.getenv("ANTHROPIC_API_KEY", "").strip():
                return "anthropic"

    # Son çare: ollama (kendi sunucumuz, her zaman var)
    return "ollama"


def get_crewai_llm_config() -> Dict:
    """
    CrewAI için LLM konfigürasyonu döndürür.
    YARUKSAİ'nin beyni — kendi LLM'imiz öncelikli.
    """
    provider = get_active_provider()

    if provider == "ollama":
        model = get_ollama_model()
        ollama_key = f"ollama/{model}" if not model.startswith("ollama/") else model
        return {
            "model": ollama_key,
            "base_url": get_ollama_base_url(),
            "provider": "ollama",
        }
    elif provider == "groq":
        model = get_groq_model()
        return {
            "model": f"groq/{model}" if not model.startswith("groq/") else model,
            "api_key": os.getenv("GROQ_API_KEY"),
            "provider": "groq",
        }
    elif provider == "openai":
        return {
            "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "api_key": os.getenv("OPENAI_API_KEY"),
            "provider": "openai",
        }
    elif provider == "anthropic":
        return {
            "model": os.getenv("ANTHROPIC_MODEL", "claude-3-haiku"),
            "api_key": os.getenv("ANTHROPIC_API_KEY"),
            "provider": "anthropic",
        }

    # Fallback
    return {
        "model": f"ollama/{get_ollama_model()}",
        "base_url": get_ollama_base_url(),
        "provider": "ollama",
    }


def get_hybrid_llm_configs() -> Dict[str, Dict]:
    """
    YARUKSAİ Bağımsız Motor — Local-First Mimari.

    Strateji (Savunma Doktrini):
      - BİRİNCİL: Tüm stage'ler → Ollama (yerel, bağımsız, sansürsüz)
      - TAKVİYE:  GROQ_BOOST=1 ise → ağır stage'lerde Groq kullan
      
    Fişi çekseler bile YARUKSAİ durmuyor.
    """
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    groq_boost = os.getenv("GROQ_BOOST", "0").strip() == "1"
    has_groq = bool(groq_key) and groq_boost

    # Ollama config (her zaman mevcut — ana güç)
    ollama_model = get_ollama_model()
    ollama_cfg = {
        "model": f"ollama/{ollama_model}" if not ollama_model.startswith("ollama/") else ollama_model,
        "base_url": get_ollama_base_url(),
        "provider": "ollama",
    }

    if has_groq:
        groq_model = get_groq_model()
        groq_cfg = {
            "model": f"groq/{groq_model}" if not groq_model.startswith("groq/") else groq_model,
            "api_key": groq_key,
            "provider": "groq",
        }
        # Takviye modu: Ağır → Groq, Hafif → Ollama
        print("[YARUKSAİ] ⚡ GROQ_BOOST aktif — ağır stage'ler Groq'ta")
        return {
            "architect": groq_cfg,
            "auditor": groq_cfg,
            "builder": groq_cfg,
            "mizan": ollama_cfg,
            "post_build_auditor": groq_cfg,
            "default": ollama_cfg,  # default artık Ollama
        }
    else:
        # Tam bağımsız mod — tüm güç yerelde
        print("[YARUKSAİ] 🛡️ LOCAL-FIRST aktif — tam bağımsız mod")
        return {
            "architect": ollama_cfg,
            "auditor": ollama_cfg,
            "builder": ollama_cfg,
            "mizan": ollama_cfg,
            "post_build_auditor": ollama_cfg,
            "default": ollama_cfg,
        }


# ─── Validation ───────────────────────────────────────────────

REQUIRED_ENV_KEYS = [
    # Artık hiçbir harici key zorunlu DEĞİL — Ollama kendi sunucumuz
]

OPTIONAL_ENV_KEYS = [
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
]


def validate_env_keys() -> None:
    """
    Opsiyonel key'ler eksikse uyarı verir.
    Ollama varsa hiçbir harici key ZORUNLU değil.
    """
    missing_optional: List[str] = []
    for key in OPTIONAL_ENV_KEYS:
        if not os.getenv(key, "").strip():
            missing_optional.append(key)

    if missing_optional:
        provider = get_active_provider()
        warnings.warn(
            f"[YARUKSAİ] Harici API anahtarları eksik: {', '.join(missing_optional)}. "
            f"Aktif provider: {provider}. "
            f"Kendi LLM'imiz (Ollama) kullanılacak.",
            stacklevel=2,
        )


def get_env_presence() -> Dict[str, bool]:
    """Return whether each env key exists and is non-empty."""
    result: Dict[str, bool] = {}
    for key in REQUIRED_ENV_KEYS + OPTIONAL_ENV_KEYS:
        value = os.getenv(key, "").strip()
        result[key] = bool(value)
    result["OLLAMA_AVAILABLE"] = True  # Kendi sunucumuz, her zaman var
    return result


def print_env_status() -> None:
    """Terminal debugging helper."""
    presence = get_env_presence()
    provider = get_active_provider()
    llm_config = get_crewai_llm_config()
    print("╔══ YARUKSAİ LLM STATUS ══╗")
    print(f"  Active Provider: {provider}")
    print(f"  Model: {llm_config['model']}")
    for key, ok in presence.items():
        status = "✅" if ok else "❌"
        print(f"  {status} {key}")
    print("╚═════════════════════════╝")
