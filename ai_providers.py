"""LLM provider adapters behind one function: chat().

Providers:
  - "mock"   — offline, deterministic assistant that parses simple phrases
               ("create task X in doing", "move X to done", "show my tasks",
               "delete task X", "confirm action 3"). Used for zero-config
               local development and by the whole pytest suite, so no test
               ever touches the network.
  - "gemini" — Google AI Studio REST API (generateContent) with native
               function calling. Free tier, no credit card.
  - "groq"   — OpenAI-compatible chat completions on Groq's free tier.
  - "mistral" — OpenAI-compatible chat completions on Mistral
               (api.mistral.ai). Same function-calling wire protocol as Groq,
               so both share the OpenAI-compatible adapter below.

Abstract conversation "turns" (provider-agnostic, built by routers/ai.py):
  {"role": "system",  "content": str}
  {"role": "user",    "content": str}
  {"role": "assistant", "text": str}
  {"role": "assistant_tool", "name": str, "args": dict}   (model's call)
  {"role": "tool", "name": str, "payload": dict}          (execution result)

Return shape: {"type": "text", "text": str}
          or {"type": "tool_call", "name": str, "args": dict}
Raises RuntimeError on any network/API failure (routers map it to 502).
"""

import json
import re

import httpx

import ai_tools

# ---- tool catalogue in each provider's wire format ---------------------- #


def _clean_schema(args_model) -> dict:
    schema = args_model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    return schema


def _gemini_tools() -> list[dict]:
    return [
        {
            "functionDeclarations": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": _clean_schema(t["args_model"]),
                }
                for t in ai_tools.TOOLS
            ]
        }
    ]


def _openai_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": _clean_schema(t["args_model"]),
            },
        }
        for t in ai_tools.TOOLS
    ]


def chat(provider: str, model: str, api_key: str, turns: list[dict]) -> dict:
    if provider == "mock":
        return _mock_chat(turns)
    if provider == "gemini":
        return _gemini_chat(model, api_key, turns)
    if provider == "groq":
        return _groq_chat(model, api_key, turns)
    if provider == "mistral":
        return _mistral_chat(model, api_key, turns)
    raise RuntimeError(f"Unknown AI provider '{provider}'")


# ------------------------------ gemini ----------------------------------- #

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _gemini_contents(turns: list[dict]) -> list[dict]:
    contents = []
    for turn in turns:
        if turn["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": turn["content"]}]})
        elif turn["role"] == "assistant":
            contents.append({"role": "model", "parts": [{"text": turn["text"]}]})
        elif turn["role"] == "assistant_tool":
            contents.append(
                {
                    "role": "model",
                    "parts": [{"functionCall": {"name": turn["name"], "args": turn["args"]}}],
                }
            )
        elif turn["role"] == "tool":
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": turn["name"],
                                "response": turn["payload"],
                            }
                        }
                    ],
                }
            )
    return contents


def _gemini_chat(model: str, api_key: str, turns: list[dict]) -> dict:
    import ai_config

    system = "\n\n".join(t["content"] for t in turns if t["role"] == "system")
    body: dict = {
        "contents": _gemini_contents([t for t in turns if t["role"] != "system"]),
        "tools": _gemini_tools(),
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    try:
        resp = httpx.post(
            _GEMINI_URL.format(model=model),
            params={"key": api_key},
            json=body,
            timeout=ai_config.AI_REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach the Gemini API: {exc}") from exc
    if resp.status_code == 429:
        raise RuntimeError("Gemini free-tier quota reached — try again later.")
    if resp.status_code in (401, 403):
        raise RuntimeError("Gemini rejected the API key — check AI_API_KEY.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Gemini API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Gemini response: {json.dumps(data)[:200]}") from exc

    for part in parts:
        call = part.get("functionCall")
        if call:
            return {"type": "tool_call", "name": call["name"], "args": call.get("args") or {}}
    text = "".join(p.get("text", "") for p in parts).strip()
    return {"type": "text", "text": text or "…"}


# ------------------- OpenAI-compatible (groq, mistral) -------------------- #

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"


def _openai_messages(turns: list[dict]) -> list[dict]:
    """Build chat-completions messages for any OpenAI-compatible provider
    (Groq, Mistral, …). Synthesises a tool_call id for each assistant_tool
    turn so the following tool turn can reference it — both APIs require the
    tool message's tool_call_id to match the call it answers."""
    messages: list[dict] = []
    pending_id: str | None = None
    n = 0
    for turn in turns:
        if turn["role"] == "system":
            messages.append({"role": "system", "content": turn["content"]})
        elif turn["role"] == "user":
            messages.append({"role": "user", "content": turn["content"]})
        elif turn["role"] == "assistant":
            messages.append({"role": "assistant", "content": turn["text"]})
        elif turn["role"] == "assistant_tool":
            pending_id = f"call_{n}"
            n += 1
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": pending_id,
                            "type": "function",
                            "function": {
                                "name": turn["name"],
                                "arguments": json.dumps(turn["args"]),
                            },
                        }
                    ],
                }
            )
        elif turn["role"] == "tool":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": pending_id or f"call_{n}",
                    "content": json.dumps(turn["payload"]),
                }
            )
    return messages


def _openai_chat(url: str, label: str, model: str, api_key: str, turns: list[dict]) -> dict:
    import ai_config

    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": _openai_messages(turns),
                "tools": _openai_tools(),
                "tool_choice": "auto",
                "temperature": 0.2,
                "max_tokens": 1024,
            },
            timeout=ai_config.AI_REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach the {label} API: {exc}") from exc
    if resp.status_code == 429:
        raise RuntimeError(f"{label} free-tier rate limit reached — try again later.")
    if resp.status_code in (401, 403):
        raise RuntimeError(f"{label} rejected the API key — check the configured key.")
    if resp.status_code >= 400:
        raise RuntimeError(f"{label} API error {resp.status_code}: {resp.text[:200]}")

    message = resp.json()["choices"][0]["message"]
    calls = message.get("tool_calls") or []
    if calls:
        fn = calls[0]["function"]
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        return {"type": "tool_call", "name": fn["name"], "args": args}
    return {"type": "text", "text": (message.get("content") or "…").strip()}


def _groq_chat(model: str, api_key: str, turns: list[dict]) -> dict:
    return _openai_chat(_GROQ_URL, "Groq", model, api_key, turns)


def _mistral_chat(model: str, api_key: str, turns: list[dict]) -> dict:
    return _openai_chat(_MISTRAL_URL, "Mistral", model, api_key, turns)


# ------------------------------- mock ------------------------------------ #

_STATUS_WORDS = r"(?:to-?do|backlog|doing|in-? ?progress|wip|ongoing|review|in-? ?review|done|complete|completed|finished)"
_TASK_RE = re.compile(
    rf"\b(?:create|add|new)\s+(?:a\s+)?task\s+(?P<title>.+?)(?:\s+(?:in|to|under)\s+(?P<status>{_STATUS_WORDS}))?\s*$",
    re.IGNORECASE,
)
_MOVE_RE = re.compile(
    rf"\b(?:move|switch|mark|set|put)\s+(?:the\s+)?task\s+(?P<title>.+?)\s+(?:to|into|as)\s+(?P<status>{_STATUS_WORDS})\s*$",
    re.IGNORECASE,
)
_CONFIRM_RE = re.compile(r"\bconfirm(?:\s+action)?\s*#?\s*(?P<id>\d+)", re.IGNORECASE)
_CANCEL_RE = re.compile(r"\bcancel(?:\s+action)?\s*#?\s*(?P<id>\d+)", re.IGNORECASE)


def _norm_status(raw: str | None) -> str | None:
    return ai_tools.normalize_status(raw) if raw else None


def _find_pending_proposal(turns: list[dict]) -> int | None:
    """Return the action_id of the most recent proposed delete (so plain
    "yes" / "no" follow-ups can confirm or cancel it)."""
    for turn in reversed(turns):
        payload = turn.get("payload") if isinstance(turn, dict) else None
        if isinstance(payload, dict) and payload.get("pending"):
            aid = payload.get("action_id")
            if isinstance(aid, int):
                return aid
    return None


def _mock_chat(turns: list[dict]) -> dict:
    """Deterministic scripted function calling for tests + offline demo."""
    last = turns[-1]

    if last["role"] == "tool":
        return {"type": "text", "text": _mock_summarize(last["name"], last["payload"])}

    if last["role"] != "user":
        return {"type": "text", "text": "I'm the offline mock assistant — ask me about your tasks or notes."}

    text = last["content"].strip()

    m = _CONFIRM_RE.search(text)
    if m:
        return {"type": "tool_call", "name": "confirm_action", "args": {"action_id": int(m.group("id"))}}

    m = _CANCEL_RE.search(text)
    if m:
        return {"type": "tool_call", "name": "cancel_action", "args": {"action_id": int(m.group("id"))}}

    # Plain follow-ups: "yes" confirms the pending delete proposal, "no" cancels it.
    low = text.lower()
    pending = _find_pending_proposal(turns)
    if pending is not None and low in {"yes", "yep", "yeah", "yup", "sure", "go ahead", "confirm", "ok", "okay", "do it", "please"}:
        return {"type": "tool_call", "name": "confirm_action", "args": {"action_id": pending}}
    if pending is not None and low in {"no", "nope", "nah", "cancel", "never mind", "dont", "don't", "stop"}:
        return {"type": "tool_call", "name": "cancel_action", "args": {"action_id": pending}}

    m = _TASK_RE.search(text)
    if m:
        status = _norm_status(m.group("status")) or "todo"
        return {"type": "tool_call", "name": "create_task", "args": {"title": m.group("title").strip().strip('"'), "status": status}}

    m = _MOVE_RE.search(text)
    if m:
        status = _norm_status(m.group("status"))
        if status:
            return {"type": "tool_call", "name": "update_task", "args": {"title_search": m.group("title").strip().strip('"'), "status": status}}

    delete_re = re.search(
        r"\b(delete|remove)\s+(?:the\s+)?(?P<kind>task|note)?\s*(?P<title>.+)",
        text,
        re.IGNORECASE,
    )
    if delete_re and delete_re.group("title"):
        title = delete_re.group("title").strip().strip('"')
        title = re.sub(
            r"^(?:the\s+)?(?:task|note)\s+(?:called\s+|named\s+)?",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip() or title
        if (delete_re.group("kind") or "").lower() == "note" or re.search(r"\bnote\b", delete_re.group(0), re.IGNORECASE):
            return {"type": "tool_call", "name": "delete_note", "args": {"title_search": title}}
        return {"type": "tool_call", "name": "delete_task", "args": {"title_search": title}}

    if re.search(r"\bnote", text, re.IGNORECASE):
        qmatch = re.search(
            r"(?:find|search|show|list|read|open)\s+(?:my\s+)?notes?\s+"
            r"(?:about|with|containing|named|called|for)?\s*(?P<q>.+)",
            text,
            re.IGNORECASE,
        )
        query = None
        if qmatch and qmatch.group("q").strip().lower() not in {"", "notes"}:
            query = qmatch.group("q").strip()
        return {"type": "tool_call", "name": "search_notes", "args": {"query": query}}

    if re.search(r"\b(event|upcoming)", text, re.IGNORECASE):
        return {"type": "tool_call", "name": "list_events", "args": {}}

    if re.search(r"\b(task|board|kanban|progress|todo|to-do)", text, re.IGNORECASE):
        status_word = re.search(rf"\b({_STATUS_WORDS})\b", text, re.IGNORECASE)
        args = {}
        if status_word:
            norm = _norm_status(status_word.group(1))
            if norm:
                args["status"] = norm
        return {"type": "tool_call", "name": "list_tasks", "args": args}

    return {
        "type": "text",
        "text": (
            "I can help you run your workspace — try one of these:\n"
            "• \"Show my tasks\"\n"
            "• \"Create task Write docs in doing\"\n"
            "• \"Move Write docs to done\"\n"
            "• \"What's upcoming?\"\n"
            "• \"Find notes about groceries\"\n"
            "• \"Delete task Old stuff\" (I'll ask you to confirm first 🙂)"
        ),
    }


def _mock_summarize(name: str, payload: dict) -> str:
    if not payload.get("ok"):
        return f"Sorry — {payload.get('error', 'that did not work.')} Try 'show my tasks' so I can see what's there."
    if name == "list_tasks":
        tasks = payload.get("tasks", [])
        if not tasks:
            return "Your board is clear — nothing here yet. Want me to create a task? 🚀"
        buckets: dict[str, list[str]] = {}
        for t in tasks:
            buckets.setdefault(t["status"], []).append(t["title"])
        lines = []
        for status in ("todo", "doing", "review", "done"):
            items = buckets.get(status, [])
            label = ai_tools.COLUMN_LABELS[status]
            if items:
                lines.append(f"• {label} ({len(items)}): " + ", ".join(items))
        return "Here's your board 📋\n" + "\n".join(lines)
    if name == "search_notes":
        notes = payload.get("notes", [])
        if not notes:
            return "No notes matched that search — try another word, or ask me to list them?"
        lines = "\n".join(f"• {n['title']} — {(n.get('snippet') or '')[:60]}" for n in notes)
        return f"Found these notes 📝\n{lines}"
    if name == "get_note":
        n = payload.get("note", {})
        return f"📝 {n.get('title')}\n\n{(n.get('content') or '')[:600]}"
    if name == "list_events":
        events = payload.get("events", [])
        if not events:
            return "No upcoming events — your calendar is clear 📅"
        lines = "\n".join(f"• {e['title']} — {e['date']}" for e in events)
        return f"Here's what's coming up 📅\n{lines}"
    if payload.get("target"):
        t = payload.get("target", {})
        label = "task" if name == "delete_task" else "note"
        return (
            f"I'd like to delete the {label} '{t.get('title')}' (id {t.get('id')}). "
            f"Hit Confirm below, or say \"confirm action {payload.get('action_id')}\" — "
            f"nothing happens until you do 🙂"
        )
    return payload.get("summary") or "Done — all set ✅"