"""AI assistant configuration (read once from the environment).

Effective-provider selection, in priority order:
  - AI_ENABLED=false                  -> "off"   (AI routes return 403)
  - AI_PROVIDER set explicitly        -> that provider ("mock"|"gemini"|"groq")
  - AI_API_KEY / GEMINI_API_KEY set   -> "gemini" (a key implies Gemini)
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
AI_MODEL = (os.environ.get("AI_MODEL") or "").strip()

# Agent-loop safety knobs.
AI_REQUEST_TIMEOUT = float(os.environ.get("AI_REQUEST_TIMEOUT") or "30")
AI_MAX_STEPS = int(os.environ.get("AI_MAX_STEPS") or "5")
AI_RATE_LIMIT_PER_HOUR = int(os.environ.get("AI_RATE_LIMIT_PER_HOUR") or "30")

# Default models per provider (used when AI_MODEL is not set).
# gemini-2.5-flash was retired for new accounts; the current free-tier
# flash model is what Google's API now recommends.
GEMINI_DEFAULT_MODEL = "gemini-3.6-flash"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
MOCK_MODEL = "mock-agent-v1"


def effective_provider() -> str:
    """Which LLM backend the assistant should talk to right now."""
    if not AI_ENABLED:
        return "off"
    if AI_PROVIDER in {"mock", "gemini", "groq"}:
        return AI_PROVIDER
    if AI_API_KEY:
        return "gemini"
    return "mock"


def default_model(provider: str) -> str:
    return {
        "gemini": GEMINI_DEFAULT_MODEL,
        "groq": GROQ_DEFAULT_MODEL,
        "mock": MOCK_MODEL,
    }.get(provider, MOCK_MODEL)
