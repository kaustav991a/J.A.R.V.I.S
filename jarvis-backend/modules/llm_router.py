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
# "gemini-flash-latest" is Google's evergreen alias for the current flash model
# — pinned ids (gemini-2.5-flash) get retired for new accounts and start 404ing.
GEMINI_MODEL      = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
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
def _gemini_keys() -> list[str]:
    """All configured Gemini keys, rotation-ready. Merges GEMINI_API_KEYS
    (comma-separated, one key per Google project/account — the free-tier quota
    is per-PROJECT, so separate projects multiply headroom) with the legacy
    single GEMINI_API_KEY. Order preserved, duplicates dropped."""
    raw = (os.getenv("GEMINI_API_KEYS", "") + "," + os.getenv("GEMINI_API_KEY", ""))
    seen: list[str] = []
    for k in raw.split(","):
        k = k.strip()
        if k and k not in seen:
            seen.append(k)
    return seen


def _gemini_configured() -> bool:
    return bool(_gemini_keys())


def _openrouter_configured() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip())


def _route_order(complexity: str, model: str | None) -> list[str]:
    """
    Returns the ordered list of providers to attempt.

    Phase 5 (2026-07-11, Kaustav-approved — reverses the earlier Groq-only
    request now that the Gemini/OpenRouter keys are in hand): full free-tier
    cascade so J.A.R.V.I.S. never goes dead when one provider's quota drains.

    - A llava/vision model is pinned local-only (there's no cloud llava; the
      Gemini-first VISION cascade lives in universal_vision_call instead).
    - 'heavy' / cloud_first: groq → gemini → openrouter, local as last resort.
      Groq stays PRIMARY (unbeatable latency for real-time voice); Gemini is
      the best free reasoning fallback; OpenRouter :free is the aggregator
      safety net (daily-capped, variable quality → last cloud stop).
    - local_first (privacy default): ollama first, then the same cloud chain.
    - Unconfigured providers are dropped; an OPEN local breaker drops ollama
      from text routes.
    """
    if model and "llava" in model.lower():
        return ["ollama"]
    if complexity == "heavy" or LLM_MODE == "cloud_first":
        order = ["groq", "gemini", "openrouter", "ollama"]
    else:
        order = ["ollama", "groq", "gemini", "openrouter"]  # local-first default

    if not _gemini_configured():
        order = [p for p in order if p != "gemini"]
    if not _openrouter_configured():
        order = [p for p in order if p != "openrouter"]
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
            elif name == "openrouter":
                return _call_openrouter(messages, temperature, max_tokens, stream, json_mode, timeout)
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
# Provider: GEMINI CLOUD (separate-quota fallback, key rotation)
# ===========================================================================
# Phase 5: keys live in separate Google projects, so rotating on quota/auth
# errors multiplies free-tier headroom (mirrors groq_key_manager's approach).
_gemini_key_idx = 0


def _import_genai():
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
    import google.generativeai as genai
    return genai


def _run_with_gemini_rotation(fn):
    """Run fn(genai) trying each configured Gemini key, starting from the last
    one that worked. Rotates on ANY provider error (quota, auth, transient);
    raises the last error only when every key failed so the router escalates."""
    global _gemini_key_idx
    keys = _gemini_keys()
    if not keys:
        raise RuntimeError("No Gemini keys configured (GEMINI_API_KEYS / GEMINI_API_KEY)")
    genai = _import_genai()
    last_err: Exception | None = None
    for offset in range(len(keys)):
        idx = (_gemini_key_idx + offset) % len(keys)
        genai.configure(api_key=keys[idx])
        try:
            result = fn(genai)
            _gemini_key_idx = idx  # sticky: keep using the key that worked
            return result
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[ROUTER] Gemini key #{idx + 1}/{len(keys)} failed "
                  f"({type(e).__name__}) — rotating.", flush=True)
    raise last_err  # type: ignore[misc]


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
    system_instruction, contents = _split_messages_for_gemini(messages)

    gen_cfg = {"temperature": temperature, "max_output_tokens": max_tokens}
    if json_mode:
        gen_cfg["response_mime_type"] = "application/json"

    req_opts = {"timeout": timeout}

    if not stream:
        def _once(genai):
            model_obj = genai.GenerativeModel(
                GEMINI_MODEL, system_instruction=system_instruction, generation_config=gen_cfg
            )
            resp = model_obj.generate_content(contents, request_options=req_opts)
            return (resp.text or "").strip()
        return _run_with_gemini_rotation(_once)

    def _start(genai):
        model_obj = genai.GenerativeModel(
            GEMINI_MODEL, system_instruction=system_instruction, generation_config=gen_cfg
        )
        it = iter(model_obj.generate_content(contents, stream=True, request_options=req_opts))
        # Pull the first chunk INSIDE the rotation so auth/quota errors rotate keys.
        first = next(it, None)
        return first, it

    first_chunk, rest = _run_with_gemini_rotation(_start)

    def _safe():
        if first_chunk is not None:
            yield getattr(first_chunk, "text", "") or ""
        for chunk in rest:
            yield getattr(chunk, "text", "") or ""
    return _safe()


# ===========================================================================
# Provider: OPENROUTER (aggregator safety net — :free models, daily cap)
# ===========================================================================
OPENROUTER_URL = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
# Individual :free models get rate-limited upstream or retired without notice,
# so the provider walks this list (comma-separated, env-overridable) in order.
# Verified available 2026-07-11.
OPENROUTER_MODELS = [m.strip() for m in os.getenv(
    "OPENROUTER_MODELS",
    "openai/gpt-oss-120b:free,"
    "qwen/qwen3-next-80b-a3b-instruct:free,"
    "meta-llama/llama-3.3-70b-instruct:free,"
    "nvidia/nemotron-nano-9b-v2:free",  # small but reliably uncongested tail
).split(",") if m.strip()]


def _call_openrouter(messages, temperature, max_tokens, stream, json_mode, timeout):
    """OpenAI-compatible HTTP call to OpenRouter (no SDK dependency), trying
    each configured :free model until one answers."""
    last_err: Exception | None = None
    for m in OPENROUTER_MODELS:
        try:
            return _call_openrouter_model(m, messages, temperature, max_tokens,
                                          stream, json_mode, timeout)
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[ROUTER] OpenRouter model '{m}' failed "
                  f"({type(e).__name__}) — trying next free model.", flush=True)
    raise last_err or RuntimeError("no OpenRouter models configured")


def _call_openrouter_model(model, messages, temperature, max_tokens, stream, json_mode, timeout):
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    msgs = messages
    if json_mode and msgs and msgs[-1].get("role") == "user":
        # Not every :free model honours response_format — belt and braces.
        msgs = msgs[:-1] + [{
            "role": "user",
            "content": msgs[-1].get("content", "") + "\n\nRespond ONLY with valid JSON. No other text.",
        }]

    payload = {
        "model": model,
        "messages": msgs,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # Optional attribution headers OpenRouter recommends.
        "X-Title": "JARVIS",
    }

    if not stream:
        resp = requests.post(OPENROUTER_URL, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"OpenRouter error: {data['error']}")
        return (data["choices"][0]["message"]["content"] or "").strip()

    def _gen():
        with requests.post(OPENROUTER_URL, json=payload, headers=headers,
                           stream=True, timeout=timeout) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                text = line.decode("utf-8", errors="replace")
                if not text.startswith("data: "):
                    continue
                chunk = text[6:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    delta = json.loads(chunk)["choices"][0]["delta"].get("content") or ""
                except Exception:
                    continue
                if delta:
                    yield delta

    g = _gen()
    first = next(g, "")  # triggers the request; raises on auth/limit errors → escalate

    def _safe():
        if first:
            yield first
        yield from g
    return _safe()


# ===========================================================================
# Vision cascade (Phase 5): Gemini flash first, local llava as offline fallback
# ===========================================================================
def universal_vision_call(prompt: str, img_b64: str,
                          temperature: float = 0.2, max_tokens: int = 1024,
                          timeout: float = 60.0) -> str:
    """Describe/reason over one JPEG (base64) with the free-vision cascade.

    Gemini flash is a big quality upgrade over local llava on this CPU-only
    box, so it goes first; llava stays as the offline/no-key fallback. Returns
    the model text, or raises if EVERY vision provider failed (callers keep
    their own honest-failure handling).
    """
    last_err: Exception | None = None

    if _gemini_configured():
        try:
            def _once(genai):
                model_obj = genai.GenerativeModel(
                    GEMINI_MODEL,
                    generation_config={"temperature": temperature,
                                       "max_output_tokens": max_tokens},
                )
                resp = model_obj.generate_content(
                    [{"mime_type": "image/jpeg", "data": img_b64}, prompt],
                    request_options={"timeout": timeout},
                )
                return (resp.text or "").strip()
            out = _run_with_gemini_rotation(_once)
            if out:
                return out
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[ROUTER] Gemini vision failed ({type(e).__name__}: {e}) — "
                  f"falling back to local llava.", flush=True)

    # Offline fallback: local llava via Ollama /api/generate.
    try:
        resp = requests.post(
            OLLAMA_URL.replace("/api/chat", "/api/generate"),
            json={"model": VISION_MODEL, "prompt": prompt,
                  "images": [img_b64], "stream": False},
            timeout=max(timeout, 120.0),
        )
        resp.raise_for_status()
        out = (resp.json().get("response") or "").strip()
        if out:
            return out
        raise RuntimeError("llava returned empty response")
    except Exception as e:  # noqa: BLE001
        raise last_err or e
