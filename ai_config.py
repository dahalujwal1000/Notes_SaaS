"""AI assistant configuration (read once from the environment).

Effective-provider selection, in priority order:
  - AI_ENABLED=false                  -> "off"   (AI routes return 403)
  - AI_PROVIDER set explicitly        -> that provider ("mock"|"gemini"|"groq"|"mistral")
  - MISTRAL_API_KEY / API_KEY set     -> "mistral" (a Mistral key implies Mistral)
  - AI_API_KEY / GEMINI_API_KEY set   -> "gemini" (a Google key implies Gemini)
  - otherwise                         -> "mock"  (offline deterministic
      assistant, so the whole feature works with zero configuration and
      the pytest suite never touches the network)

Every value is read at import time; tests override attributes on this
module (monkeypatch) to exercise rate limits and the off switch.
"""

import os


def _flag(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


AI_ENABLED = _flag("AI_ENABLED", True)
AI_PROVIDER = (os.environ.get("AI_PROVIDER") or "").strip().lower()
AI_API_KEY = os.environ.get("AI_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY") or os.environ.get("API_KEY") or ""
AI_MODEL = (os.environ.get("AI_MODEL") or "").strip()

# Agent-loop safety knobs.
AI_REQUEST_TIMEOUT = float(os.environ.get("AI_REQUEST_TIMEOUT") or "30")
AI_MAX_STEPS = int(os.environ.get("AI_MAX_STEPS") or "5")
AI_RATE_LIMIT_PER_HOUR = int(os.environ.get("AI_RATE_LIMIT_PER_HOUR") or "30")

# Default models per provider (used when AI_MODEL is not set).
# Gemini: "gemini-2.5-flash" is the current stable free-tier flash model
# (check Google's docs if you want a newer one). Mistral's free tier uses
# "mistral-small-latest"; Groq's free tier serves Llama 3.3 70B.
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"
MISTRAL_DEFAULT_MODEL = "mistral-small-latest"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
MOCK_MODEL = "mock-agent-v1"


def effective_provider() -> str:
    """Which LLM backend the assistant should talk to right now."""
    if not AI_ENABLED:
        return "off"
    if AI_PROVIDER in {"mock", "gemini", "groq", "mistral"}:
        return AI_PROVIDER
    if MISTRAL_API_KEY:
        return "mistral"
    if AI_API_KEY:
        return "gemini"
    return "mock"


def default_model(provider: str) -> str:
    return {
        "gemini": GEMINI_DEFAULT_MODEL,
        "mistral": MISTRAL_DEFAULT_MODEL,
        "groq": GROQ_DEFAULT_MODEL,
        "mock": MOCK_MODEL,
    }.get(provider, MOCK_MODEL)


def api_key_for(provider: str) -> str:
    """Which credential to send for the active provider.

    Mistral keeps its own key slot (MISTRAL_API_KEY, with plain API_KEY as a
    fallback) instead of forcing everything through the generic AI_API_KEY;
    every other provider shares AI_API_KEY.
    """
    if provider == "mistral":
        return MISTRAL_API_KEY
    return AI_API_KEY
