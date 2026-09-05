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
from modules.groq_key_manager import groq_model, run_with_key_rotation

# --- Configuration (all env-overridable) ------------------------------------
OLLAMA_URL        = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
LOCAL_MODEL       = os.getenv("OLLAMA_MODEL", "llama3:8b")          # primary reasoning
VISION_MODEL      = os.getenv("OLLAMA_VISION_MODEL", "llava:latest")  # vision cortex
GROQ_MODEL        = groq_model()
# Agentic core: tool turns need a strong tool-use model. The 8B instant model
# above is right for cheap classification and wrong for a multi-step tool loop —
# it invents tool names, drops required args and loops. Kept as a SEPARATE env so
# making the agent smarter never makes every routing turn more expensive.
# `llama-3.3-70b-versatile` until 2026-08-15 — Groq decommissioned it on the
# 16th, and this default is what every TOOL turn ran on: `TOOL_PROVIDERS` puts
# groq first, so the whole §6.8 agent layer went through this one id. Groq's
# recommended replacements are `openai/gpt-oss-120b` and `qwen/qwen3.6-27b`;
# both were confirmed present in the live model list and both emit correct
# tool_calls. gpt-oss-120b is chosen because it returned the argument
# VERBATIM ("notepad") where qwen title-cased it ("Notepad") — tool arguments
# feed target matching, so a model that does not rewrite them is worth having.
GROQ_TOOL_MODEL   = os.getenv("GROQ_TOOL_MODEL", "openai/gpt-oss-120b")
# Gemini cloud fallback — separate org/quota from Groq, so a drained Groq daily
# bucket escalates here instead of dying. Skipped automatically if GEMINI_API_KEY
# is unset. Uses the legacy google-generativeai SDK (already a dependency).
# "gemini-flash-latest" is Google's evergreen alias for the current flash model
# — pinned ids (gemini-2.5-flash) get retired for new accounts and start 404ing.
GEMINI_MODEL      = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_TOOL_MODEL = os.getenv("GEMINI_TOOL_MODEL", GEMINI_MODEL)
# Tool turns reach Gemini through Google's OpenAI-COMPATIBILITY endpoint rather
# than the google-generativeai SDK: the SDK wants FunctionDeclaration objects,
# and translating both directions is a second dialect to keep correct for no
# gain. This way all three tool providers share one request/response shape.
GEMINI_OPENAI_URL = os.getenv(
    "GEMINI_OPENAI_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
)
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
# The same breaker, for the CLOUD legs
# ===========================================================================
# The local route has had one since the day a cold Ollama made every command wait
# out a 30-second timeout. A cloud provider can be in exactly that state and was
# not covered — **measured on the desk on 2026-08-29**, during the A11 gate rows:
#
#   [ROUTER] Gemini key #1/5 failed (DeadlineExceeded) — rotating.
#   [ROUTER] Gemini key #2/5 failed (DeadlineExceeded) — rotating.
#   [ROUTER] Gemini key #3/5 failed (DeadlineExceeded) — rotating.
#   [ROUTER] Gemini key #4/5 failed (DeadlineExceeded) — rotating.
#   [ROUTER] 'gemini' route failed — Escalating to next provider…
#
# ...on EVERY leg of EVERY turn. A turn has two or three legs (classify, act,
# compose), so "what's on my calendar today?" took **409 seconds** while Groq
# behind it was answering in two. The provider was not down and not out of quota:
# a direct probe from the same machine returned one word in 34.2s on one key and
# timed out on the next. Slow is the hardest failure to route around, because
# every individual call looks like it might still succeed.
#
# So: rotate the keys once, and if the whole rotation fails in a way that says
# THE PROVIDER rather than THIS REQUEST, skip that provider for a cooldown. One
# turn pays the cost instead of all of them.
CLOUD_COOLDOWN = float(os.getenv("JARVIS_CLOUD_COOLDOWN", "180"))

# A DAILY quota is not a blip, and 180 seconds is the wrong answer to it.
#
# Measured 2026-09-05. Every Gemini key was out of its daily free-tier
# allowance. The breaker opened for 180s, expired, and the next turn paid the
# whole five-key rotation again - each key timing out or 429-ing - before
# escalating. One turn ("what did we discuss earlier?") took so long that the
# verifier's 900-second window expired and recorded NO ANSWER for a desk that
# was merely queueing behind a provider it already knew was finished for the
# day. The answer did arrive, minutes later, and was correct.
#
# So the cooldown is matched to the failure: a per-minute limit clears in
# minutes, a per-DAY limit does not clear before tomorrow. Capped at an hour
# rather than set to midnight, because a rolling-window quota would otherwise
# stay shut out far longer than it needs to be.
DAILY_COOLDOWN = float(os.getenv("JARVIS_DAILY_COOLDOWN", "3600"))

# What in an error text means "not until tomorrow" rather than "not this minute".
_DAILY_SIGNS = ("per day", "perday", "daily", "tokens per day", " tpd",
                "requests per day", " rpd", "free_tier_requests",
                "generaterequestsperdayperprojectpermodel",
                "exceeded your current quota")

# provider name -> monotonic deadline; >now means the breaker is OPEN
_cloud_down_until: dict = {}

# What trips it. Deliberately narrow: a 400 is a malformed request of ours and
# says nothing about the provider's health, so retrying it elsewhere is right but
# blacklisting the provider for three minutes is not.
_BREAKER_SIGNS = ("deadlineexceeded", "timeout", "timed out", "504",
                  "resource_exhausted", "429", "quota", "503",
                  "service unavailable")


def _cloud_breaker_open(name: str) -> bool:
    return time.monotonic() < _cloud_down_until.get(name, 0.0)


def _should_trip(err: Exception) -> bool:
    text = f"{type(err).__name__} {err}".lower()
    return any(sign in text for sign in _BREAKER_SIGNS)


def _is_daily_limit(err: Exception) -> bool:
    """Is this "out until tomorrow", rather than "busy right now"?"""
    return any(sign in f"{type(err).__name__} {err}".lower()
               for sign in _DAILY_SIGNS)


def _trip_cloud_breaker(name: str, err: Exception) -> None:
    if not _should_trip(err):
        return
    daily = _is_daily_limit(err)
    cooldown = DAILY_COOLDOWN if daily else CLOUD_COOLDOWN
    _cloud_down_until[name] = time.monotonic() + cooldown
    why = ("its DAILY allowance is gone — re-trying every few minutes just "
           "pays the key rotation again for a provider that is finished until "
           "the quota resets" if daily else
           "later calls skip it instead of paying the whole key rotation again")
    print(f"[ROUTER] ⚡ '{name}' circuit breaker OPEN for {cooldown:.0f}s "
          f"({type(err).__name__}) — {why}.", flush=True)


def _reject_empty(name: str, out, stream: bool):
    """An empty 200 is a FAILURE, and must escalate rather than end the cascade.

    F-79, measured on the desk 2026-08-29. With Gemini out of quota and Groq
    returning 413, the log read:

        [ROUTER] 'groq' route failed (413 ...). Escalating to next provider...
        <nothing>
        INFO: "POST /api/backdoor HTTP/1.1" 200 OK

    No OpenRouter attempt, no NVIDIA attempt, no `FATAL: all providers
    exhausted`. A cloud leg had answered 200 with EMPTY content, the router
    counted that as success and returned "" - so the two remaining legs,
    including the NVIDIA one added that same afternoon precisely as a backstop,
    were never tried. The turn reached him as "I didn't get an answer together on
    that one", which is honest and was nevertheless avoidable.

    **`_call_ollama` has raised on this since G5.7.** The same defect was fixed in
    one leg of five and left in the other four - root cause #4, which is why this
    lives in the dispatch rather than becoming a fifth copy.

    Streams are checked by their FIRST CHUNK, which the HTTP legs already pull
    eagerly to surface auth errors; a generator that yields nothing at all is the
    same empty answer arriving more slowly.
    """
    if stream:
        return out
    if not str(out or "").strip():
        raise RuntimeError(f"{name} returned an empty 200 response")
    return out


def _reset_cloud_breaker(name: str) -> None:
    if _cloud_down_until.pop(name, None):
        print(f"[ROUTER] ✅ '{name}' recovered — breaker closed.", flush=True)


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


def _nvidia_configured() -> bool:
    return bool(os.getenv("NVIDIA_API_KEY", "").strip())


# Cloud providers, most-preferred first. Env-driven rather than hardcoded so a
# provider switch is a config change instead of an edit — the same discipline the
# cloud gateway's LLM_PROVIDER_* switches follow.
#
# GEMINI IS PRIMARY (2026-08-15, Kaustav's call). This reverses the 2026-07-11
# ordering, and the reason is answer quality in the language he actually writes:
# Gemini handles code-switched romanised Bengali/English markedly better than
# llama-3.1-8b, and a fluent reply in the wrong register is the failure that
# matters more often than a slow one. The cost is real and worth stating: Groq's
# latency is unbeatable for real-time voice, so the spoken path pays for this.
# Put groq back in front here to undo it — no code change, no deploy.
# NVIDIA's own NIM endpoint is appended rather than inserted, and that is the
# whole of the risk assessment: it enters the cascade behind every leg that is
# already measured, so a gateway that has never seen it behaves exactly as it did
# before. Move it up with JARVIS_CLOUD_ORDER once it has been benchmarked the way
# the OpenRouter tool list was - correctness first, on real desk-shaped turns.
_CLOUD_CHAIN_DEFAULT = "gemini,groq,openrouter,nvidia"
_KNOWN_CLOUD = ("gemini", "groq", "openrouter", "nvidia")


def _cloud_chain() -> list[str]:
    """The cloud half of the cascade, in preference order.

    Unknown names are dropped rather than attempted, and an order that names
    nothing valid falls back to every known provider — a typo in `.env` must not
    be able to silence the cloud, which would look exactly like every provider
    being down.
    """
    raw = os.getenv("JARVIS_CLOUD_ORDER") or _CLOUD_CHAIN_DEFAULT
    seen: set[str] = set()
    out: list[str] = []
    for name in (s.strip().lower() for s in raw.split(",")):
        if name in _KNOWN_CLOUD and name not in seen:
            seen.add(name)
            out.append(name)
    return out or list(_KNOWN_CLOUD)


def _route_order(complexity: str, model: str | None) -> list[str]:
    """
    Returns the ordered list of providers to attempt.

    Phase 5 (2026-07-11, Kaustav-approved — reverses the earlier Groq-only
    request now that the Gemini/OpenRouter keys are in hand): full free-tier
    cascade so J.A.R.V.I.S. never goes dead when one provider's quota drains.

    - A llava/vision model is pinned local-only (there's no cloud llava; the
      Gemini-first VISION cascade lives in universal_vision_call instead).
    - 'heavy' / cloud_first: the cloud chain (see `_cloud_chain`, Gemini first by
      default), with local as the last resort.
    - local_first (privacy default): ollama first, then that same cloud chain.
    - Unconfigured providers are dropped; an OPEN local breaker drops ollama
      from text routes.
    """
    if model and "llava" in model.lower():
        return ["ollama"]
    if complexity == "heavy" or LLM_MODE == "cloud_first":
        order = [*_cloud_chain(), "ollama"]
    else:
        order = ["ollama", *_cloud_chain()]  # local-first default

    if not _gemini_configured():
        order = [p for p in order if p != "gemini"]
    if not _openrouter_configured():
        order = [p for p in order if p != "openrouter"]
    if not _nvidia_configured():
        order = [p for p in order if p != "nvidia"]
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

    # Skip cloud providers whose breaker is open — but NEVER return an empty
    # chain. If every provider is tripped, the least-bad move is to try the first
    # one anyway: a slow answer beats the sentinel, and a provider that recovered
    # inside its cooldown is only discovered by asking.
    usable = [n for n in providers if n == "ollama" or not _cloud_breaker_open(n)]
    if usable and usable != providers:
        skipped = [n for n in providers if n not in usable]
        print(f"[ROUTER] skipping {', '.join(skipped)} — breaker open.", flush=True)
    providers = usable or providers

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
                out = _reject_empty(name, _call_gemini(
                    messages, temperature, max_tokens, stream, json_mode,
                    timeout), stream)
                _reset_cloud_breaker(name)
                return out
            elif name == "openrouter":
                out = _reject_empty(name, _call_openrouter(
                    messages, temperature, max_tokens, stream, json_mode,
                    timeout), stream)
                _reset_cloud_breaker(name)
                return out
            elif name == "nvidia":
                out = _reject_empty(name, _call_nvidia(
                    messages, temperature, max_tokens, stream, json_mode,
                    timeout), stream)
                _reset_cloud_breaker(name)
                return out
            else:  # groq
                out = _reject_empty(name, _call_groq(
                    messages, temperature, max_tokens, stream, json_mode,
                    timeout), stream)
                _reset_cloud_breaker(name)
                return out
        except Exception as e:
            last_err = e
            if name == "ollama":
                # Open the breaker so the NEXT calls skip the slow/dead local route.
                _trip_ollama_breaker(e)
            else:
                _trip_cloud_breaker(name, e)
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
        content = resp.json().get("message", {}).get("content", "").strip()
        if not content:
            # Ollama can answer HTTP 200 with an EMPTY message (model still
            # loading, num_predict starvation, an OOM-killed generation). Handing
            # "" back reads downstream as "no answer / zero actions" and gets
            # narrated as a false "Done, Sir" — raise so universal_llm_call trips
            # the breaker and escalates to a cloud provider instead of succeeding
            # silently with nothing.
            raise RuntimeError("ollama returned an empty 200 response")
        return content

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
    if not first:
        # empty 200 stream (same failure modes as the non-stream path) — escalate
        # rather than hand back a silently-empty generator. This raises inside
        # universal_llm_call's try, so the breaker trips and cloud takes over.
        raise RuntimeError("ollama returned an empty 200 stream")

    def _safe():
        yield first
        yield from g
    return _safe()


# ===========================================================================
# Provider: GROQ CLOUD (escalation / fallback)
# ===========================================================================
#: Groq accepts `reasoning_format` only on models that reason. Measured session 4:
#: `allam-2-7b` answers 400 "`reasoning_format` is not supported with this model",
#: so this is sent per-model rather than always. `hidden` drops the reasoning from
#: the response entirely — identical content, and 0.6s instead of 1.0s on
#: gpt-oss-20b, because none of it is serialised back.
_GROQ_REASONING_FAMILIES = ("openai/gpt-oss",)


def _groq_reasoning_kwargs(model: str) -> dict:
    return ({"reasoning_format": "hidden"}
            if model.startswith(_GROQ_REASONING_FAMILIES) else {})


def _call_groq(messages, temperature, max_tokens, stream, json_mode, timeout):
    if not stream:
        kwargs = {
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout,
            **_groq_reasoning_kwargs(GROQ_MODEL),
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
        **_groq_reasoning_kwargs(GROQ_MODEL),
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


# Keys the provider has told us are not keys at all. A revoked key never
# recovers inside one process, so re-offering it every call buys a guaranteed
# round-trip of nothing. Live-gate F-36: key #5 of 5 was revoked and was still
# being tried on every single call.
_gemini_dead_keys: set[int] = set()

# When every live key reports quota, the leg is out until the window resets.
# Live-gate F-36: the four working keys share ONE bucket — their retry-after
# values counted down in step from a single reset instant, which is what a
# shared quota looks like from outside. The docstring above says rotation
# "multiplies free-tier headroom"; measured, it multiplies latency. Until the
# window turns over, escalate on the FIRST call instead of paying five
# round-trips to learn the same thing again.
_gemini_cooldown_until: float = 0.0
_GEMINI_COOLDOWN_DEFAULT_S = float(os.getenv("GEMINI_QUOTA_COOLDOWN_S", "60"))


def _is_quota_error(err: Exception) -> bool:
    name = type(err).__name__
    text = str(err).lower()
    return name == "ResourceExhausted" or "429" in text or "quota" in text


def _is_dead_key_error(err: Exception) -> bool:
    """True when the provider rejected the CREDENTIAL, not the request."""
    text = str(err).lower()
    return ("api_key_invalid" in text
            or "api key not valid" in text
            or type(err).__name__ in ("Unauthenticated", "PermissionDenied"))


def _quota_retry_seconds(err: Exception) -> float:
    """Seconds Google asked us to wait, when it says so."""
    import re as _re
    m = _re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)", str(err), _re.IGNORECASE)
    if m:
        try:
            return min(float(m.group(1)) + 1.0, 300.0)
        except ValueError:
            pass
    return _GEMINI_COOLDOWN_DEFAULT_S


def _run_with_gemini_rotation(fn):
    """Run fn(genai) trying each configured Gemini key, starting from the last
    one that worked. Rotates on PROVIDER errors (quota, auth, transient);
    raises the last error only when every key failed so the router escalates.

    Two things it deliberately does NOT rotate on (live-gate F-17):
    a deterministic client-side error, which every key will reproduce
    identically, and a key the provider has already called invalid.
    """
    global _gemini_key_idx, _gemini_cooldown_until
    keys = _gemini_keys()
    if not keys:
        raise RuntimeError("No Gemini keys configured (GEMINI_API_KEYS / GEMINI_API_KEY)")

    now = time.monotonic()
    if now < _gemini_cooldown_until:
        raise RuntimeError(
            f"Gemini quota cooldown — every key reported 429; "
            f"{_gemini_cooldown_until - now:.0f}s left in the window"
        )

    genai = _import_genai()
    live = [i for i in range(len(keys)) if i not in _gemini_dead_keys]
    if not live:
        raise RuntimeError(
            f"All {len(keys)} Gemini key(s) were rejected as invalid by the provider"
        )

    last_err: Exception | None = None
    quota_failures = 0
    tried = 0
    # Start from the last key that worked, if it is still live.
    start = live.index(_gemini_key_idx) if _gemini_key_idx in live else 0
    for offset in range(len(live)):
        idx = live[(start + offset) % len(live)]
        tried += 1
        genai.configure(api_key=keys[idx])
        try:
            result = fn(genai)
            _gemini_key_idx = idx  # sticky: keep using the key that worked
            return result
        except (TypeError, ValueError) as e:
            # F-17: a client-side bug. Five keys produced five identical
            # "contents must not be empty" failures and the log read as five
            # dead keys — the payload was wrong and no key could have helped.
            print(f"[ROUTER] Gemini call is malformed ({type(e).__name__}: {e}) "
                  f"— not a key problem, not rotating.", flush=True)
            raise
        except Exception as e:  # noqa: BLE001
            last_err = e
            if _is_dead_key_error(e):
                _gemini_dead_keys.add(idx)
                print(f"[ROUTER] Gemini key #{idx + 1}/{len(keys)} REVOKED by the "
                      f"provider — dropped for this process.", flush=True)
                continue
            if _is_quota_error(e):
                quota_failures += 1
            print(f"[ROUTER] Gemini key #{idx + 1}/{len(keys)} failed "
                  f"({type(e).__name__}) — rotating.", flush=True)

    if quota_failures and quota_failures == tried:
        wait = _quota_retry_seconds(last_err) if last_err else _GEMINI_COOLDOWN_DEFAULT_S
        _gemini_cooldown_until = time.monotonic() + wait
        print(f"[ROUTER] Every live Gemini key is quota-limited — the leg is "
              f"shared-bucket, not per-key. Cooling down {wait:.0f}s.", flush=True)

    raise last_err  # type: ignore[misc]


#: What to send when a caller's whole prompt was system text. Gemini rejects an
#: empty `contents` outright, so SOMETHING has to occupy the user turn; this
#: says only "do the thing you were just told to do" and adds no instruction of
#: its own, because anything with content in it would silently edit prompts
#: written for four different call sites.
_GEMINI_SYSTEM_ONLY_NUDGE = "Proceed."


def _split_messages_for_gemini(messages):
    """OpenAI-style messages → (system_instruction, Gemini contents).

    Gemini takes system text separately and uses roles 'user'/'model'.

    Live-gate finding F-17. Hoisting system text out is correct — and when the
    caller passed NOTHING ELSE, it left `contents` empty and the SDK raised
    "contents must not be empty" on every key in the pool. Four call sites do
    exactly that (`brain.py` synthesis ×2, the briefing, and episodic-memory
    summarisation), so the separate-quota fallback was permanently dead on the
    four paths that most needed a second provider — invisibly, because the
    cascade just answered from the next one down.
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
    if not contents and system_instruction:
        contents.append({"role": "user", "parts": [_GEMINI_SYSTEM_ONLY_NUDGE]})
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
#
# RE-VERIFIED 2026-08-15 against the live catalogue, and THREE OF THE FOUR WERE
# GONE: `openai/gpt-oss-120b:free`, `qwen/qwen3-next-80b-a3b-instruct:free` and
# `meta-llama/llama-3.3-70b-instruct:free` no longer exist as `:free` variants —
# the paid base ids do, which is why a casual look says they are fine. Only the
# nemotron tail survived. This is the LAST leg of the cascade, so a dead list
# here is invisible until Groq and Gemini have both already failed.
#
# Every id below was confirmed present AND answered a real request on this
# account today. The `:free` suffix is mandatory: the account is free-tier, so a
# paid id fails at request time rather than at start-up.
OPENROUTER_MODELS = [m.strip() for m in os.getenv(
    "OPENROUTER_MODELS",
    "nvidia/nemotron-3.5-lightning:free,"      # 1M context, quick
    "openai/gpt-oss-20b:free,"                 # different vendor, familiar family
    "google/gemma-4-26b-a4b-it:free,"
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
        # Session 4: this is the leg that spoke a monologue out loud in the room.
        # OpenRouter's own parameter, measured on nemotron-3.5-lightning: the
        # reasoning field comes back empty, the content is identical, and the call
        # took 15s instead of 45s because thousands of thinking characters are no
        # longer generated for a caller that discards them.
        "reasoning": {"exclude": True},
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
    if not first.strip():
        # F-79: an empty stream is an empty answer arriving slowly. Ollama has
        # raised here since G5.7; this leg returned nothing and ended the cascade.
        raise RuntimeError("openrouter returned an empty 200 stream")

    def _safe():
        if first:
            yield first
        yield from g
    return _safe()


# ===========================================================================
# NVIDIA NIM (build.nvidia.com) — added 2026-08-29
# ===========================================================================
# OpenAI-compatible, so this is the OpenRouter shape with a different URL, key
# and model id. Three things about it are NOT the same, and each one is a defect
# this project has already paid for once:
#
# **1. It thinks by default.** NVIDIA's own sample passes
# `chat_template_kwargs={"enable_thinking": True}` and reads a `reasoning_content`
# stream. F-48 and F-49 are what that costs here: reasoning shares the output
# budget with the answer, so the desk spoke prefixes ("It is", "System load is"),
# and one leg read a model's private monologue out loud in the room. So thinking
# is **off** by default and `JARVIS_NVIDIA_THINKING=1` turns it back on for
# someone who wants it deliberately.
#
# **2. It is a free tier with its own quota**, which is the actual argument for
# adding it: OpenRouter's free cap is shared across every `:free` id, and Gemini's
# is 20 requests a DAY per project (F-70). A fourth independent bucket is worth
# more here than a faster model on a bucket that is already empty.
#
# **3. MEASURED on 2026-08-29, the same way the OpenRouter tool list was** -
# real requests, a six-tool shelf with close neighbours, from this machine:
#
#   nemotron-3-ultra-550b-a55b   4/4   1.1-15s, highly variable
#   nemotron-3-nano-30b-a3b      3/4   median 0.6s
#
# Ultra got every case right, including the two that matter most: it passed
# "VS Code" through VERBATIM (the OpenRouter lightning model invented "code" for
# the same sentence) and it called **nothing at all** for "tell me a joke". Nano
# is seven times faster and searched the web for the joke - the invent-an-action
# failure, which is why both lists here are ordered by CORRECTNESS, NOT SPEED.
#
# **The free tier returns intermittent HTTP 500s** - one in three on a repeated
# identical request, answering correctly on the retries. That is what the model
# walk below is for, and it is worth knowing before this leg is trusted with
# anything time-critical.
#
# **The "5x faster than Claude and ChatGPT" figure that prompted this is an
# Instagram post** and appears nowhere on NVIDIA's page, which makes no latency
# claim at all. A plain chat turn measured 1.1s here, which is genuinely quick -
# and 15s on another call of the identical request.
NVIDIA_URL = os.getenv(
    "NVIDIA_URL", "https://integrate.api.nvidia.com/v1/chat/completions")

# Walked in order, like OPENROUTER_MODELS. Ultra first because it is the one the
# catalogue calls agentic; nano is a small, quick tail for when Ultra is busy.
# Both ids were CHECKED against the live catalogue on 2026-08-29, and the first
# draft of this list was wrong: `nvidia/nemotron-nano-9b-v2` is an OpenRouter id
# and does not exist on NIM at all. An id assumed to transfer between providers
# is precisely the rot ladder item 0.3 exists to catch, and the boot preflight
# caught it within a minute of the key being set.
NVIDIA_MODELS = [m.strip() for m in os.getenv(
    "NVIDIA_MODELS",
    "nvidia/nemotron-3-ultra-550b-a55b,"       # 561B MoE, 1M context, 4/4 tools
    "nvidia/nemotron-3-nano-30b-a3b",          # 30B MoE, ~0.6s, quick tail
).split(",") if m.strip()]

# Off by default. See point 1 above - this is F-49's guard at the request, before
# a monologue is ever generated, rather than a strip after it arrives.
NVIDIA_THINKING = os.getenv("JARVIS_NVIDIA_THINKING", "0").strip() == "1"

# Tool-capable NIM ids, kept SEPARATE from the chat list for the same reason the
# OpenRouter pair are separate: plenty of good chat models reject `tools`
# outright, and discovering that inside an agent loop is a failure halfway
# through a task rather than at the door.
# Ultra FIRST despite being the slower of the two, and that is the same trade the
# OpenRouter list already records: this leg only ever runs once Groq and Gemini
# have both failed, so it is reached in a degraded state where a wrong action
# costs more than a slow one. Nano is kept as a tail rather than dropped - the
# free tier 500s intermittently, and one model with no fallback is not a cascade.
NVIDIA_TOOL_MODELS = [m.strip() for m in os.getenv(
    "NVIDIA_TOOL_MODELS",
    "nvidia/nemotron-3-ultra-550b-a55b,"       # 4/4 - verbatim args, and called
                                               # nothing when nothing fitted
    "nvidia/nemotron-3-nano-30b-a3b",          # 3/4 - searched the web for
                                               # "tell me a joke"; fast last resort
).split(",") if m.strip()]


def _nvidia_payload(model, messages, temperature, max_tokens, stream, json_mode):
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
        # NVIDIA's own knob. Sent explicitly either way rather than omitted: the
        # server-side default is ON, and relying on a default we do not control
        # is how the budget findings happened in the first place.
        "chat_template_kwargs": {"enable_thinking": NVIDIA_THINKING},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _call_nvidia(messages, temperature, max_tokens, stream, json_mode, timeout):
    """Try each configured NIM model until one answers."""
    last_err: Exception | None = None
    for m in NVIDIA_MODELS:
        try:
            return _call_nvidia_model(m, messages, temperature, max_tokens,
                                      stream, json_mode, timeout)
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[ROUTER] NVIDIA model '{m}' failed "
                  f"({type(e).__name__}) — trying next.", flush=True)
    raise last_err or RuntimeError("no NVIDIA models configured")


def _call_nvidia_model(model, messages, temperature, max_tokens, stream,
                       json_mode, timeout):
    key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY not set")

    msgs = messages
    if json_mode and msgs and msgs[-1].get("role") == "user":
        # Same belt-and-braces as OpenRouter: `response_format` is honoured
        # unevenly across NIM models, and a JSON caller that gets prose parses to
        # "zero actions", which is narrated as success.
        msgs = msgs[:-1] + [{
            "role": "user",
            "content": msgs[-1].get("content", "") + "\n\nRespond ONLY with valid JSON. No other text.",
        }]

    payload = _nvidia_payload(model, msgs, temperature, max_tokens, stream, json_mode)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    if not stream:
        resp = requests.post(NVIDIA_URL, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"NVIDIA error: {data['error']}")
        return (data["choices"][0]["message"]["content"] or "").strip()

    def _gen():
        with requests.post(NVIDIA_URL, json=payload, headers=headers,
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
                    delta = json.loads(chunk)["choices"][0]["delta"]
                except Exception:
                    continue
                # `reasoning_content` is read and DISCARDED on purpose. With
                # thinking off it should never arrive; if a model ignores the
                # flag, the monologue still must not reach `content` and be
                # spoken. F-49 is why this is explicit rather than incidental.
                piece = delta.get("content") or ""
                if piece:
                    yield piece

    g = _gen()
    first = next(g, "")  # triggers the request; raises on auth/limit errors
    if not first.strip():
        raise RuntimeError("nvidia returned an empty 200 stream")  # F-79

    def _safe():
        if first:
            yield first
        yield from g
    return _safe()


# ===========================================================================
# TOOL CALLING (agentic core, phase 1) — roadmap §5 Tier C #12
# ===========================================================================
# `universal_llm_call` is text-only: `_call_groq` posts `messages` with no
# `tools`, so there was no way to run an agent loop through the router at all.
# This is its sibling. Differences that matter:
#
#   * Ollama is EXCLUDED. Tool-calling on the 17GB CPU box is slow and
#     unreliable, and a hallucinated tool call is worse than a slow sentence.
#   * No streaming. A tool call is only actionable once complete.
#   * Failure is EXPLICIT (`ToolTurn.ok is False`), never an empty success — the
#     same rule the ollama empty-200 fix established.
#   * Every provider answer goes through modules/tool_calls.normalise_*, so the
#     agent loop never sees a provider-shaped object.

# Free tool-capable models on OpenRouter, walked in order like OPENROUTER_MODELS.
# Kept SEPARATE because plenty of good free chat models reject `tools` outright.
#
# This list was WHOLLY DEAD when re-checked on 2026-08-15 — all three `:free`
# variants had been withdrawn, so the tool cascade's last leg could not have
# answered anything. Nothing surfaced it because it only runs once Groq and
# Gemini have both failed.
#
# Each id below was sent real tool requests today against a six-tool shelf with
# close neighbours (close_app vs native_app_launcher vs tv_launch_app) and had to
# pick correctly, pass the argument through verbatim, AND call nothing when
# nothing fitted. Measured, not assumed:
#
#   nemotron-3-super-120b-a12b:free   4/4   median 3.8s
#   nemotron-nano-9b-v2:free          4/4   median 5.0s
#   openai/gpt-oss-20b:free           4/4   median 12.8s
#   nemotron-3.5-lightning:free       3/4   median 1.3s   <- fastest, but see below
#
# ORDERED BY CORRECTNESS, NOT SPEED, and that is deliberate. Lightning is three
# times quicker and would be the obvious head of the list — it also answered
# "shut down VS Code on the pc" with `close_app(app_name="code")`, inventing a
# target that was never said. This leg only ever runs when Groq AND Gemini have
# both already failed, so it is reached in a degraded state where a wrong action
# costs more than a slow one. Latency is Groq's job; this list's job is to be right.
OPENROUTER_TOOL_MODELS = [m.strip() for m in os.getenv(
    "OPENROUTER_TOOL_MODELS",
    "nvidia/nemotron-3-super-120b-a12b:free,"  # 4/4, 262k context
    "nvidia/nemotron-nano-9b-v2:free,"         # 4/4, the one survivor of the old list
    "openai/gpt-oss-20b:free,"                 # 4/4, different vendor, familiar family
    "nvidia/nemotron-3.5-lightning:free",      # 3/4 but 1.3s — a fast last resort
).split(",") if m.strip()]


def _tool_route_order() -> list[str]:
    """Tool-capable providers only, in preference order, minus unconfigured ones.

    Note there is no local tail here on purpose: if every cloud provider is out,
    an agent task must fail honestly rather than be handed to a model that will
    invent tool calls.
    """
    from modules.tool_calls import TOOL_PROVIDERS

    order = list(TOOL_PROVIDERS)
    if not _gemini_configured():
        order = [p for p in order if p != "gemini"]
    if not _openrouter_configured():
        order = [p for p in order if p != "openrouter"]
    if not _nvidia_configured():
        order = [p for p in order if p != "nvidia"]
    return order


def universal_tool_call(
    messages: list,
    tools: list,
    tool_choice: str = "auto",
    temperature: float = 0.2,
    max_tokens: int = 1024,
    timeout: float = 60.0,
    provider: str | None = None,
):
    """One tool-calling turn, normalised. Returns a `tool_calls.ToolTurn`.

    `messages` is standard OpenAI history INCLUDING prior `assistant` turns with
    `tool_calls` and their `tool` results. `tools` is a list of OpenAI function
    definitions — validated here, because a malformed registry should fail at the
    door rather than as a provider 400 halfway through an agent task.

    `provider` pins a single provider (used by tests and by an operator debugging
    one route); by default the cascade is walked.

    Temperature defaults LOW: tool selection is a decision, not prose.
    """
    from modules.tool_calls import ToolTurn, to_openai_tools, validate_tool_defs

    problems = validate_tool_defs(tools)
    if problems:
        return ToolTurn.failed("invalid tool definitions: " + "; ".join(problems))
    # The registry authors tools in the Anthropic dialect; every provider we can
    # reach today speaks OpenAI function-calling. Translate once, here.
    tools = to_openai_tools(tools)

    providers = [provider] if provider else _tool_route_order()
    if not providers:
        return ToolTurn.failed("no tool-capable provider is configured")

    last_err: str | None = None
    for name in providers:
        try:
            if name == "groq":
                turn = _tool_call_groq(messages, tools, tool_choice,
                                       temperature, max_tokens, timeout)
            elif name == "gemini":
                turn = _tool_call_gemini(messages, tools, tool_choice,
                                         temperature, max_tokens, timeout)
            elif name == "openrouter":
                turn = _tool_call_openrouter(messages, tools, tool_choice,
                                             temperature, max_tokens, timeout)
            elif name == "nvidia":
                turn = _tool_call_nvidia(messages, tools, tool_choice,
                                         temperature, max_tokens, timeout)
            else:
                last_err = f"'{name}' cannot serve tool calls"
                print(f"[ROUTER] {last_err}.", flush=True)
                continue
            if turn.ok:
                return turn
            # A normalised-but-empty answer counts as a failure worth escalating:
            # the next provider may well produce a real tool call.
            last_err = turn.error
            print(f"[ROUTER] tool route '{name}' unusable ({turn.error}) — escalating.",
                  flush=True)
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            print(f"[ROUTER] tool route '{name}' failed ({last_err}) — escalating.",
                  flush=True)

    print(f"[ROUTER] FATAL: no tool provider answered (last: {last_err}).", flush=True)
    return ToolTurn.failed(f"all tool providers failed (last error: {last_err})")


def _tool_call_groq(messages, tools, tool_choice, temperature, max_tokens, timeout):
    from modules.tool_calls import normalise_openai_response

    kwargs = {
        "model": GROQ_TOOL_MODEL,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout,
    }
    completion = run_with_key_rotation(lambda c: c.chat.completions.create(**kwargs))
    return normalise_openai_response(completion, provider="groq", model=GROQ_TOOL_MODEL)


def _tool_call_gemini(messages, tools, tool_choice, temperature, max_tokens, timeout):
    """Gemini via its OpenAI-compatibility endpoint, reusing the key rotation."""
    from modules.tool_calls import ToolTurn

    def _attempt(key):
        return _openai_tool_http(
            GEMINI_OPENAI_URL, key, GEMINI_TOOL_MODEL, messages, tools,
            tool_choice, temperature, max_tokens, timeout, provider="gemini")

    keys = _gemini_keys()
    if not keys:
        return ToolTurn.failed("no Gemini key configured", "gemini")
    last: Exception | None = None
    for key in keys:
        try:
            return _attempt(key)
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"[ROUTER] Gemini tool key failed ({type(e).__name__}) — rotating.",
                  flush=True)
    raise last or RuntimeError("gemini tool call failed")


def _tool_call_openrouter(messages, tools, tool_choice, temperature, max_tokens, timeout):
    """Walk the free tool-capable models until one actually answers."""
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    last: Exception | None = None
    for m in OPENROUTER_TOOL_MODELS:
        try:
            turn = _openai_tool_http(OPENROUTER_URL, key, m, messages, tools,
                                     tool_choice, temperature, max_tokens, timeout,
                                     provider="openrouter")
            if turn.ok:
                return turn
            last = RuntimeError(turn.error or "empty turn")
        except Exception as e:  # noqa: BLE001
            last = e
        print(f"[ROUTER] OpenRouter tool model '{m}' unusable "
              f"({type(last).__name__}) — trying next.", flush=True)
    raise last or RuntimeError("no OpenRouter tool models configured")


def _tool_call_nvidia(messages, tools, tool_choice, temperature, max_tokens, timeout):
    """Walk the configured NIM models until one answers with a usable turn.

    `_openai_tool_http` already carries the shape - NIM is OpenAI-compatible, and
    that shared helper is why adding this provider is a URL, a key and a model id
    rather than a fourth copy of the same request.
    """
    key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY not set")
    last: Exception | None = None
    for m in NVIDIA_TOOL_MODELS:
        try:
            turn = _openai_tool_http(NVIDIA_URL, key, m, messages, tools,
                                     tool_choice, temperature, max_tokens, timeout,
                                     provider="nvidia")
            if turn.ok:
                return turn
            last = RuntimeError(turn.error or "empty turn")
        except Exception as e:  # noqa: BLE001
            last = e
        print(f"[ROUTER] NVIDIA tool model '{m}' unusable "
              f"({type(last).__name__}) — trying next.", flush=True)
    raise last or RuntimeError("no NVIDIA tool models configured")


def _openai_tool_http(url, key, model, messages, tools, tool_choice,
                      temperature, max_tokens, timeout, provider):
    """One OpenAI-compatible tool request over plain HTTP.

    Shared by Gemini's compatibility endpoint and OpenRouter — the only
    differences between them are the URL, the key and the model id.
    """
    from modules.tool_calls import normalise_openai_response

    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "X-Title": "JARVIS",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return normalise_openai_response(resp.json(), provider=provider, model=model)


# ===========================================================================
# Vision cascade (Phase 5): Gemini flash first, local llava as offline fallback
# ===========================================================================
#: How long Gemini gets as the FIRST leg of the vision cascade before the next
#: cloud provider is tried. Not the caller's timeout: a fast leg behind it makes
#: patience here expensive rather than safe.
_GEMINI_FIRST_LEG_S = float(os.getenv("JARVIS_GEMINI_VISION_TIMEOUT_S", "20"))


def universal_vision_call(prompt: str, img_b64: str,
                          temperature: float = 0.2, max_tokens: int = 1024,
                          timeout: float = 60.0) -> str:
    """Describe/reason over one JPEG (base64) with the free-vision cascade.

    Three legs, in this order, and the middle one was missing until 2026-08-22:

        Gemini flash   a real quality upgrade over llava on this CPU-only box
        Groq vision    cloud, ~2 s, needs no local RAM at all
        local llava    offline / no-key fallback, 4.4 GB and 30-90 s

    Why the middle leg matters more than it looks. Gemini's free tier is a small
    DAILY quota, so it is routinely exhausted -- measured on 2026-08-22 with all
    four keys valid and all four returning 429. Without a cloud leg behind it,
    every "what is on my screen?" for the rest of that day fell straight to
    llava: 4.4 GB loaded on a 16 GB box, about 30 s warm and **91.9 s measured
    under memory pressure**. Groq answered the same question in **2.3 s** with
    five working keys, and had been sitting there unused because this function
    never asked it. `screen_reader` already had a Groq leg; this one did not,
    which is root cause #4 across two files again.

    Returns the model text, or raises if EVERY vision provider failed (callers
    keep their own honest-failure handling).
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
                # A SHORTER deadline than the caller asked for, deliberately.
                # There is a cloud leg behind this one now that answers in about
                # two seconds, so spending the full 60 s on the SDK's internal
                # retries is 60 s of pure loss -- measured exactly that on
                # 2026-08-22, then Groq answered in 2.4 s. If Gemini cannot
                # answer in 20 s it is not the fast path any more.
                resp = model_obj.generate_content(
                    [{"mime_type": "image/jpeg", "data": img_b64}, prompt],
                    request_options={"timeout": min(timeout, _GEMINI_FIRST_LEG_S)},
                )
                return (resp.text or "").strip()
            out = _run_with_gemini_rotation(_once)
            if out:
                return out
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[ROUTER] Gemini vision failed ({type(e).__name__}: {e}) — "
                  f"falling back to local llava.", flush=True)

    # Cloud leg two: Groq vision. Costs no local RAM, so it is strictly better
    # than llava whenever a key is live -- and on this box "no local RAM" is the
    # whole argument, because llava's 4.4 GB is what turns a 30 s answer into a
    # 92 s one. Groq's vision model emits a <think> block, which is why the
    # reasoning guard is applied here exactly as screen_reader applies it.
    try:
        from modules.groq_key_manager import (groq_vision_model,
                                              run_with_key_rotation)
        from modules import reasoning_guard

        def _groq_vision(client):
            resp = client.chat.completions.create(
                model=groq_vision_model(),
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": "data:image/jpeg;base64," + img_b64}},
                ]}],
                max_tokens=(max(max_tokens, 1024)
                            + reasoning_guard.THINKING_HEADROOM),
                temperature=temperature,
            )
            return reasoning_guard.strip_reasoning(
                resp.choices[0].message.content or "").strip()

        out = run_with_key_rotation(_groq_vision)
        if out:
            return out
    except Exception as e:                              # noqa: BLE001
        last_err = last_err or e
        print(f"[ROUTER] Groq vision failed ({type(e).__name__}: {e}) - "
              f"falling back to local llava.", flush=True)

    # Offline fallback: local llava via Ollama /api/generate.
    #
    # Tier 3.2, and the measurement overturned the obvious design. llava is
    # 4.4 GB on a box that usually has 4-6 GB free, so the first instinct was to
    # refuse the load. Measured instead: with 2.56 GB free it loaded and answered
    # correctly in 91.9 s -- three times a warm call, but not a failure. Refusing
    # would have deleted a working feature, and there is nothing left to escalate
    # to here anyway: Gemini above has already failed by the time we arrive.
    #
    # What a tight load DOES need is a longer deadline (a 120 s ceiling over a
    # 92 s call is how a working answer gets reported as "vision offline") and a
    # short keep_alive, because nothing ever set one and a single screen read was
    # parking 4.4 GB for ollama's default five minutes.
    from modules import ram_budget
    try:
        payload = {"model": VISION_MODEL, "prompt": prompt,
                   "images": [img_b64], "stream": False}
        deadline = ram_budget.apply(VISION_MODEL, payload,
                                    max(timeout, 120.0), "router vision")
        resp = requests.post(
            OLLAMA_URL.replace("/api/chat", "/api/generate"),
            json=payload, timeout=deadline,
        )
        resp.raise_for_status()
        out = (resp.json().get("response") or "").strip()
        if out:
            return out
        raise RuntimeError("llava returned empty response")
    except Exception as e:  # noqa: BLE001
        raise last_err or e
