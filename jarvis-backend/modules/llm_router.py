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
import time
import requests
from modules.groq_key_manager import run_with_key_rotation

# --- Configuration (all env-overridable) ------------------------------------
OLLAMA_URL        = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
LOCAL_MODEL       = os.getenv("OLLAMA_MODEL", "llama3:8b")          # primary reasoning
VISION_MODEL      = os.getenv("OLLAMA_VISION_MODEL", "llava:latest")  # vision cortex
GROQ_MODEL        = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
# Gemini cloud fallback — separate org/quota from Groq, so a drained Groq daily
# bucket escalates here instead of dying. Skipped automatically if GEMINI_API_KEY
# is unset. Uses the legacy google-generativeai SDK (already a dependency).
GEMINI_MODEL      = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# Lowered from 30s: a healthy local 8B returns its first token well under 15s.
# If the local cortex can't meet this, the circuit breaker (below) routes to the
# cloud instead of making every single call wait out a long timeout.
LOCAL_TIMEOUT     = float(os.getenv("JARVIS_LOCAL_TIMEOUT", "15"))  # seconds for a local call
# "local_first" (default, privacy-centric) or "cloud_first" (legacy behaviour)
LLM_MODE          = os.getenv("JARVIS_LLM_MODE", "local_first").strip().lower()
# After the local route fails, skip it for this many seconds so we don't pay the
# timeout on every subsequent call while Ollama is down/slow/cold.
LOCAL_COOLDOWN    = float(os.getenv("JARVIS_LOCAL_COOLDOWN", "120"))


# ===========================================================================
# Circuit breaker for the local (Ollama) route
# ===========================================================================
# When Ollama times out or is unreachable, opening this breaker makes _route_order
# skip the local route for LOCAL_COOLDOWN seconds. That turns "every command waits
# 30s for a dead local model" into "one command pays the cost, the rest go straight
# to the fast cloud provider." A successful local call closes it again.
_ollama_down_until = 0.0  # time.monotonic() deadline; >now means breaker is OPEN


def _ollama_breaker_open() -> bool:
    return time.monotonic() < _ollama_down_until


def _trip_ollama_breaker(err: Exception) -> None:
    global _ollama_down_until
    _ollama_down_until = time.monotonic() + LOCAL_COOLDOWN
    print(
        f"[ROUTER] ⚡ Local (Ollama) circuit breaker OPEN for {LOCAL_COOLDOWN:.0f}s "
        f"({type(err).__name__}). Routing to cloud until it recovers.",
        flush=True,
    )


def _reset_ollama_breaker() -> None:
    global _ollama_down_until
    if _ollama_down_until:
        print("[ROUTER] ✅ Local (Ollama) route recovered — breaker closed.", flush=True)
    _ollama_down_until = 0.0


# ===========================================================================
# Routing decision
# ===========================================================================
def _gemini_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def _route_order(complexity: str, model: str | None) -> list[str]:
    """
    Returns the ordered list of providers to attempt.
    - A llava/vision model is pinned local-only (privacy + the cloud has no llava).
    - 'heavy' tasks go cloud-first (extreme reasoning), local as a safety net.
    - Otherwise local-first (default) unless JARVIS_LLM_MODE=cloud_first.
    - Gemini sits between Groq and the local route as a separate-quota cloud fallback:
      when Groq's daily bucket is drained it escalates to Gemini before the (slow,
      CPU-bound) local model. Dropped entirely if GEMINI_API_KEY is unset.
    - If the local circuit breaker is OPEN, the local route is dropped from text
      routes entirely (vision still tries local — there's no cloud llava).
    """
    if model and "llava" in model.lower():
        return ["ollama"]
    # --- TEMPORARY (user request): Groq-only for all text reasoning ---
    # "no gemini no local AI for now" — the local CPU model was slow and Gemini was
    # an extra dependency the user wanted out of the path. Text routes to Groq only;
    # vision (llava) still pins local above since there's no cloud llava.
    # To restore the full Groq → Gemini → Ollama chain, delete this line.
    return ["groq"]
    if complexity == "heavy" or LLM_MODE == "cloud_first":
        order = ["groq", "gemini", "ollama"]
    else:
        order = ["ollama", "groq", "gemini"]  # local-first default

    if not _gemini_configured():
        order = [p for p in order if p != "gemini"]
    if _ollama_breaker_open():
        order = [p for p in order if p != "ollama"] or ["groq"]
    return order


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
                result = _call_ollama(
                    messages, temperature, max_tokens, stream, json_mode, model, local_timeout
                )
                _reset_ollama_breaker()  # local answered — keep using it
                return result
            elif name == "gemini":
                return _call_gemini(messages, temperature, max_tokens, stream, json_mode, timeout)
            else:  # groq
                return _call_groq(messages, temperature, max_tokens, stream, json_mode, timeout)
        except Exception as e:
            last_err = e
            if name == "ollama":
                # Open the breaker so the NEXT calls skip the slow/dead local route.
                _trip_ollama_breaker(e)
            print(
                f"[ROUTER] '{name}' route failed ({type(e).__name__}: {e}). "
                f"{'Escalating to next provider…' if providers[-1] != name else 'No providers left.'}",
                flush=True,
            )
            continue

    # Every provider failed — degrade gracefully.
    print(f"[ROUTER] FATAL: all providers exhausted (last error: {last_err}).", flush=True)
    if not stream:
        # HONEST FAILURE: do NOT return '{"actions": []}' in json_mode. Downstream
        # that parses to "zero actions" and gets narrated as "Done, Sir" — a silent
        # false success on a hard provider outage (the classic "it said it did it
        # but nothing happened"). Return a plain, honest failure line in BOTH modes;
        # the parse spine finds no action in it and speaks it verbatim, and any
        # json.loads() caller falls through its own except to a safe default.
        return "My reasoning core is unreachable at the moment, Sir — every AI provider is offline."

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


# ===========================================================================
# Provider: GEMINI CLOUD (separate-quota fallback)
# ===========================================================================
_gemini_ready = False


def _ensure_gemini():
    """Configure the Gemini SDK once; raise if no key so the router escalates onward."""
    global _gemini_ready
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
    import google.generativeai as genai

    if not _gemini_ready:
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set")
        genai.configure(api_key=key)
        _gemini_ready = True
    return genai


def _split_messages_for_gemini(messages):
    """OpenAI-style messages → (system_instruction, Gemini contents).

    Gemini takes system text separately and uses roles 'user'/'model'.
    """
    system_parts, contents = [], []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "") or ""
        if role == "system":
            system_parts.append(content)
        elif role == "assistant":
            contents.append({"role": "model", "parts": [content]})
        else:  # user (and any unknown role) maps to user
            contents.append({"role": "user", "parts": [content]})
    system_instruction = "\n\n".join(p for p in system_parts if p) or None
    return system_instruction, contents


def _call_gemini(messages, temperature, max_tokens, stream, json_mode, timeout):
    genai = _ensure_gemini()
    system_instruction, contents = _split_messages_for_gemini(messages)

    gen_cfg = {"temperature": temperature, "max_output_tokens": max_tokens}
    if json_mode:
        gen_cfg["response_mime_type"] = "application/json"

    model_obj = genai.GenerativeModel(
        GEMINI_MODEL, system_instruction=system_instruction, generation_config=gen_cfg
    )
    req_opts = {"timeout": timeout}

    if not stream:
        resp = model_obj.generate_content(contents, request_options=req_opts)
        return (resp.text or "").strip()

    def _gen():
        for chunk in model_obj.generate_content(contents, stream=True, request_options=req_opts):
            yield getattr(chunk, "text", "") or ""

    g = _gen()
    first = next(g, "")  # triggers the request; raises on auth/quota errors → escalate

    def _safe():
        if first:
            yield first
        yield from g
    return _safe()
