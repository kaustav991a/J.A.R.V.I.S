"""
llm_router.py — Local-First LLM Router (Phase 3)
================================================

Privacy-centric routing: J.A.R.V.I.S. defaults to the LOCAL Ollama cortex for all
standard work (conversational routing, intent classification, general reasoning) and
only escalates to the cloud (Groq / Anthropic via the heavy path) when:

  A) the local model times out or Ollama is unreachable, OR
  B) the caller flags the task as `complexity="heavy"` (complex coding, Figma→HTML,
     architecture design) where an 8B local model will struggle.

The Vision Cortex passes `model="llava:latest"` (or sets OLLAMA_VISION_MODEL) and is
pinned to local-only — visual data never leaves the machine.

External contract is unchanged: `universal_llm_call(...)` returns a string (stream=False)
or a generator of string chunks (stream=True). Two new optional kwargs were added:
`complexity` ("standard" | "heavy") and `model` (force a specific local model).
"""

import os
import json
import requests
from modules.groq_key_manager import run_with_key_rotation

# --- Configuration (all env-overridable) ------------------------------------
OLLAMA_URL        = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
LOCAL_MODEL       = os.getenv("OLLAMA_MODEL", "llama3:8b")          # primary reasoning
VISION_MODEL      = os.getenv("OLLAMA_VISION_MODEL", "llava:latest")  # vision cortex
GROQ_MODEL        = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
LOCAL_TIMEOUT     = float(os.getenv("JARVIS_LOCAL_TIMEOUT", "30"))  # seconds for a local call
# "local_first" (default, privacy-centric) or "cloud_first" (legacy behaviour)
LLM_MODE          = os.getenv("JARVIS_LLM_MODE", "local_first").strip().lower()


# ===========================================================================
# Routing decision
# ===========================================================================
def _route_order(complexity: str, model: str | None) -> list[str]:
    """
    Returns the ordered list of providers to attempt.
    - A llava/vision model is pinned local-only (privacy + the cloud has no llava).
    - 'heavy' tasks go cloud-first (extreme reasoning), local as a safety net.
    - Otherwise local-first (default) unless JARVIS_LLM_MODE=cloud_first.
    """
    if model and "llava" in model.lower():
        return ["ollama"]
    if complexity == "heavy":
        return ["groq", "ollama"]
    if LLM_MODE == "cloud_first":
        return ["groq", "ollama"]
    return ["ollama", "groq"]  # local-first default


# ===========================================================================
# Public entry point
# ===========================================================================
def universal_llm_call(
    messages: list,
    temperature: float = 0.6,
    max_tokens: int = 150,
    stream: bool = False,
    json_mode: bool = False,
    timeout: float = 45.0,
    complexity: str = "standard",
    model: str | None = None,
):
    """
    Route an LLM call through the configured provider chain (local-first by default).
    Falls through to the next provider on any failure; returns a graceful sentinel if
    every provider fails so callers never crash.
    """
    providers = _route_order(complexity, model)
    last_err: Exception | None = None

    for name in providers:
        try:
            if name == "ollama":
                # local streaming gets a slightly longer ceiling than a single sync call
                local_timeout = LOCAL_TIMEOUT if not stream else max(LOCAL_TIMEOUT, 60.0)
                return _call_ollama(
                    messages, temperature, max_tokens, stream, json_mode, model, local_timeout
                )
            else:  # groq
                return _call_groq(messages, temperature, max_tokens, stream, json_mode, timeout)
        except Exception as e:
            last_err = e
            print(
                f"[ROUTER] '{name}' route failed ({type(e).__name__}: {e}). "
                f"{'Escalating to next provider…' if providers[-1] != name else 'No providers left.'}",
                flush=True,
            )
            continue

    # Every provider failed — degrade gracefully.
    print(f"[ROUTER] FATAL: all providers exhausted (last error: {last_err}).", flush=True)
    if not stream:
        return '{"actions": []}' if json_mode else "Local AI cortex is offline, Sir."

    def _err_gen():
        yield "Local AI cortex is offline, Sir."
    return _err_gen()


# ===========================================================================
# Provider: LOCAL OLLAMA
# ===========================================================================
def _call_ollama(messages, temperature, max_tokens, stream, json_mode, model, timeout):
    msgs = messages
    # Never mutate the caller's list/dicts (they're shared with working memory).
    if json_mode and msgs and msgs[-1].get("role") == "user":
        msgs = msgs[:-1] + [{
            "role": "user",
            "content": msgs[-1].get("content", "") + "\n\nRespond ONLY with valid JSON. No other text.",
        }]

    payload = {
        "model": model or LOCAL_MODEL,
        "messages": msgs,
        "options": {"temperature": temperature, "num_predict": max_tokens},
        "stream": stream,
    }
    if json_mode:
        payload["format"] = "json"

    if not stream:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "").strip()

    # Streaming: eagerly pull the first chunk so a connection failure raises HERE
    # (inside the try in universal_llm_call) and triggers cloud escalation.
    def _gen():
        with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                content = data.get("message", {}).get("content", "")
                if content:
                    yield content

    g = _gen()
    first = next(g, "")

    def _safe():
        if first:
            yield first
        yield from g
    return _safe()


# ===========================================================================
# Provider: GROQ CLOUD (escalation / fallback)
# ===========================================================================
def _call_groq(messages, temperature, max_tokens, stream, json_mode, timeout):
    if not stream:
        kwargs = {
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        completion = run_with_key_rotation(lambda c: c.chat.completions.create(**kwargs))
        return completion.choices[0].message.content.strip()

    kwargs = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "timeout": timeout,
    }

    def _gen():
        completion_stream = run_with_key_rotation(lambda c: c.chat.completions.create(**kwargs))
        for chunk in completion_stream:
            yield (chunk.choices[0].delta.content or "") if chunk.choices else ""

    g = _gen()
    first = next(g, "")  # triggers the request; raises on auth/rate errors

    def _safe():
        if first:
            yield first
        yield from g
    return _safe()
