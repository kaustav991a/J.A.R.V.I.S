r"""
boot_preflight.py — G5.7 startup config preflight ("what's missing")
====================================================================

JARVIS pulls keys, models, and files from many places (.env, models/). When one
is absent the failure shows up much later as a confusing runtime error — a dead
LLM route, a face gate that never arms, gestures that don't start. This runs
once at boot and says plainly, up front, what is missing and what it disables.

Pure and injectable (env dict + an exists() callable), so test_boot_preflight.py
exercises every branch with no real environment. Nothing here raises or blocks
boot — it only reports; a missing REQUIRED item flips `ok` to False so the caller
can log it loudly (JARVIS still starts and degrades honestly).
"""

from __future__ import annotations

import os
import sys

# At least ONE name in each group must be set for that capability to work.
REQUIRED_ANY: dict[str, list[str]] = {
    "LLM reasoning (primary — Groq)": ["GROQ_API_KEYS", "GROQ_API_KEY"],
}

# Nice-to-have: absence degrades a feature but JARVIS still runs.
RECOMMENDED: dict[str, str] = {
    "GEMINI_API_KEYS": "cloud reasoning + vision fallback (llava-only without it)",
    "OPENROUTER_API_KEY": "extra free LLM fallback tier",
    "TELEGRAM_BOT_TOKEN": "phone reach (owner_notify / gateway)",
    "TELEGRAM_USER_ID": "phone reach — owner chat id",
    "TAVILY_API_KEY": "live web info (search degrades to blocked DuckDuckGo without it)",
    "WATCHDOG_TOKEN": "stable shutdown token across restarts (auto-generated otherwise)",
}

# Model files gestures / face-gate cannot run without.
CRITICAL_FILES: dict[str, str] = {
    os.path.join("models", "hand_landmarker.task"): "hand gesture control (G1–G5)",
    os.path.join("models", "face_detection_yunet_2023mar.onnx"): "owner face gate (YuNet)",
    os.path.join("models", "face_recognition_sface_2021dec.onnx"): "owner face recognition (SFace)",
}

# Files whose absence degrades but doesn't break a subsystem.
RECOMMENDED_FILES: dict[str, str] = {
    os.path.join("models", "owner_embeddings.npz"): "enrolled owner face — run enroll_face.py",
    ".env": "config/secrets file",
}


def preflight(env=None, exists=None) -> dict:
    """Scan config + files and return a structured report. `env` defaults to
    os.environ, `exists` to os.path.exists — both injectable for tests."""
    env = os.environ if env is None else env
    exists = os.path.exists if exists is None else exists

    def has(name: str) -> bool:
        return bool((env.get(name) or "").strip())

    missing_required = [(cap, names) for cap, names in REQUIRED_ANY.items()
                        if not any(has(n) for n in names)]
    missing_recommended = [(name, why) for name, why in RECOMMENDED.items() if not has(name)]
    files_missing = [(p, why) for p, why in CRITICAL_FILES.items() if not exists(p)]
    files_recommended_missing = [(p, why) for p, why in RECOMMENDED_FILES.items()
                                 if not exists(p)]
    return {
        "ok": not missing_required and not files_missing,
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        "files_missing": files_missing,
        "files_recommended_missing": files_recommended_missing,
    }


def format_report(rep: dict) -> str:
    """Human-readable multi-line summary of a preflight() report."""
    lines = ["[PREFLIGHT] Boot configuration check:"]
    if rep["ok"] and not rep["missing_recommended"] and not rep["files_recommended_missing"]:
        lines.append("  ✅ All required and recommended config present.")
        return "\n".join(lines)
    if rep["ok"]:
        lines.append("  ✅ All REQUIRED config present.")
    for cap, names in rep["missing_required"]:
        lines.append(f"  ❌ REQUIRED missing: {cap} — set one of: {', '.join(names)}")
    for path, why in rep["files_missing"]:
        lines.append(f"  ❌ REQUIRED file missing: {path} — {why}")
    for name, why in rep["missing_recommended"]:
        lines.append(f"  ⚠️  optional {name} not set — {why}")
    for path, why in rep["files_recommended_missing"]:
        lines.append(f"  ⚠️  optional file {path} missing — {why}")
    return "\n".join(lines)


def _safe_print(text: str) -> None:
    """Print a report that may contain ✅/❌/⚠️ on a console that cannot encode them.

    `sys.stdout.encoding` is cp1252 on this machine, and a print of one of those
    glyphs raises UnicodeEncodeError *inside the thing that was reporting*. Session
    4 found 48 files exposed to that; `main.py` hardens its own stdout, which is
    why the existing report has been fine there — but this module is also called
    from harnesses, `run_evals` and one-off scripts, where it is not. Reporting a
    dead model must never be the thing that raises.
    """
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        enc = (getattr(sys.stdout, "encoding", None) or "ascii")
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"),
              flush=True)


def log_preflight() -> dict:
    """Run the preflight against the live environment and print the report.
    Returns the report dict (so a caller can react to `ok`)."""
    rep = preflight()
    _safe_print(format_report(rep))
    return rep


# ═══════════════════════════════════════════════════════════════════════════
# MODEL LIVENESS — the half that presence cannot cover
# ═══════════════════════════════════════════════════════════════════════════
# Everything above asks "is the key set?". Nothing asked "is the model id still a
# model?", and that is where the expensive failures lived:
#
#   F-46, 2026-08-22  `llama-3.1-8b-instant` had been decommissioned by Groq. It
#                     was the desk chat default AND hardcoded in five files, so
#                     memory extraction, episodic summaries and the GUI parser
#                     answered 404 on EVERY turn — for weeks, silently, because
#                     all three swallow their own errors by design.
#   F-67, same day    `llama-3.2-90b-vision-preview` was hardcoded in
#                     screen_reader.py. Dead too, and invisible because the Groq
#                     vision leg only runs after Gemini has already failed.
#   2026-08-16        `llama-3.3-70b-versatile` retired; the code default moved
#                     and `render.yaml` still declared the dead id.
#   2026-08-15        OpenRouter withdrew 3 of the 4 `:free` ids the router walks,
#                     leaving the tool cascade's last leg WHOLLY dead.
#
# Four incidents, one shape: a model id rots on someone else's schedule, and the
# subsystem that notices first is the one nobody is watching. `test_model_ids.py`
# pins ids already known to be dead; it cannot know what a provider retired this
# morning. This can, by asking.
#
# Design constraints, because a preflight that hurts is a preflight that gets
# switched off:
#   * CATALOGUES, not completions — one GET per provider, zero tokens spent.
#   * never blocks boot: the caller runs it on a thread and it logs when it lands.
#   * never raises, and an unreachable provider is UNKNOWN, not dead — a laptop on
#     a train must not be told its models are gone.
#   * `JARVIS_MODEL_PREFLIGHT=0` turns it off entirely.
#   * `fetch` is injectable, so the harness needs no network.

#: Providers that run ON THIS MACHINE. For these, "unreachable" is not ambiguous
#: -- the daemon is not running, and saying "not necessarily dead" about a local
#: socket would be the same silence that let session 4 run for hours with no
#: vision at all.
_LOCAL_PROVIDERS = {"ollama"}

#: Where each provider lists what it has. Ollama is local; the rest need a key.
_CATALOGUE_URLS = {
    "groq": "https://api.groq.com/openai/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
    "ollama": "http://localhost:11434/api/tags",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
}


def configured_models(env=None) -> list:
    """Every model id this process will actually try to use, with its provider.

    Read the same way the runtime reads them, so a `.env` override is what gets
    checked rather than a default nothing reaches.
    """
    env = os.environ if env is None else env

    def _first(name: str, default: str) -> str:
        return (env.get(name) or "").strip() or default

    out = [
        ("groq", _first("GROQ_MODEL", "openai/gpt-oss-120b"), "desk chat + memory"),
        ("groq", _first("GROQ_TOOL_MODEL", "openai/gpt-oss-120b"), "agent tool loop"),
        ("groq", _first("GROQ_VISION_MODEL", "qwen/qwen3.6-27b"), "Groq vision leg"),
        ("gemini", _first("GEMINI_MODEL", "gemini-flash-latest"), "cloud reasoning"),
        ("ollama", _first("OLLAMA_MODEL", "llama3.2:3b"), "local reasoning fallback"),
        ("ollama", _first("OLLAMA_VISION_MODEL", "llava"), "local vision"),
    ]
    # The OpenRouter legs are read from the ROUTER, not from the env var, because
    # the env var is normally unset and the router still walks its own default
    # list. That list was WHOLLY DEAD on 2026-08-15 — three withdrawn `:free`
    # variants — and checking only the env var would have seen nothing to check.
    or_lists = []
    try:
        from modules import llm_router as _lr
        or_lists = [(getattr(_lr, "OPENROUTER_MODELS", []), "OpenRouter chat fallback"),
                    (getattr(_lr, "OPENROUTER_TOOL_MODELS", []), "OpenRouter tool fallback")]
    except Exception:                                   # noqa: BLE001
        raw = (env.get("OPENROUTER_MODELS") or "").strip()
        if raw:
            or_lists = [([x.strip() for x in raw.split(",") if x.strip()],
                         "OpenRouter fallback")]
    seen = set()
    for models, why in or_lists:
        for m in models:
            if m and m not in seen:
                seen.add(m)
                out.append(("openrouter", m, why))
    return out


def _default_fetch(url: str, env=None) -> list:
    """Return the provider list of model ids, or raise."""
    import json
    import urllib.request

    env = os.environ if env is None else env
    headers = {"User-Agent": "jarvis-preflight"}
    if "groq.com" in url:
        # The SDK, not urllib. A raw urllib request to api.groq.com gets
        # Cloudflare error 1010 — a bot-fingerprint ban — on every key and every
        # model, INCLUDING unauthenticated, and it looks exactly like a revoked
        # account. That lesson is already recorded in this project; the first
        # draft of this preflight ignored it and reported the Groq catalogue as
        # HTTPError on one run and fine on the next. A flaky check on the provider
        # that has rotted twice is worse than no check.
        keys = (env.get("GROQ_API_KEYS") or env.get("GROQ_API_KEY") or "")
        first = next((k.strip() for k in keys.split(",") if k.strip()), "")
        from groq import Groq
        return [str(m.id) for m in Groq(api_key=first).models.list().data]
    if "openrouter.ai" in url:
        headers["Authorization"] = "Bearer " + (env.get("OPENROUTER_API_KEY") or "").strip()
    elif "generativelanguage" in url:
        keys = (env.get("GEMINI_API_KEYS") or env.get("GEMINI_API_KEY") or "")
        first = next((k.strip() for k in keys.split(",") if k.strip()), "")
        url = url + "?key=" + first + "&pageSize=200"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=8) as r:
        body = json.loads(r.read().decode("utf-8", errors="replace"))

    if isinstance(body.get("models"), list) and body.get("models") and \
            isinstance(body["models"][0], dict) and "name" in body["models"][0]:
        # gemini: "models/gemini-3.7-flash"  |  ollama: {"name": "llava:latest"}
        return [str(m.get("name", "")).split("/")[-1] for m in body["models"]]
    if isinstance(body.get("data"), list):
        return [str(m.get("id", "")) for m in body["data"]]
    return []


def check_model_liveness(fetch=None, env=None) -> dict:
    """Compare every configured id against its provider live catalogue.

    Returns dead / alive / unknown lists. A provider that cannot be reached
    yields UNKNOWN for its ids, never DEAD.
    """
    env = os.environ if env is None else env
    if (env.get("JARVIS_MODEL_PREFLIGHT") or "1").strip() == "0":
        return {"dead": [], "alive": [], "unknown": [], "skipped": True}

    fetch = fetch or (lambda url: _default_fetch(url, env))
    catalogues: dict = {}
    dead, alive, unknown, down = [], [], [], []

    for provider, model, why in configured_models(env):
        if provider not in catalogues:
            try:
                catalogues[provider] = fetch(_CATALOGUE_URLS[provider])
            except Exception as e:                      # noqa: BLE001
                catalogues[provider] = e
        cat = catalogues[provider]
        if isinstance(cat, Exception) or not cat:
            # A LOCAL daemon that does not answer is DOWN, and that is a fact
            # rather than a maybe. Session 4 ran for hours with ollama not
            # running: every local-vision feature was dead, row 12.1 could not be
            # attempted, and nothing anywhere said so. Reporting that as
            # "unverified, not necessarily dead" — correct for a cloud provider
            # behind a flaky network — would be the same silence in a nicer coat.
            if provider in _LOCAL_PROVIDERS:
                down.append((provider, model, why,
                             type(cat).__name__ if isinstance(cat, Exception)
                             else "no models installed"))
            else:
                unknown.append((provider, model, why,
                                type(cat).__name__ if isinstance(cat, Exception)
                                else "empty catalogue"))
            continue
        names = [str(c) for c in cat]
        # An evergreen alias resolves server-side and need not appear verbatim; a
        # bare ollama tag matches its ":latest" form in either direction.
        hit = any(model == c or c.startswith(model + ":") or model.startswith(c + ":")
                  for c in names) or model.endswith("-latest")
        (alive if hit else dead).append((provider, model, why))
    return {"dead": dead, "alive": alive, "unknown": unknown, "down": down,
            "skipped": False}


def format_liveness(rep: dict) -> str:
    if rep.get("skipped"):
        return "[PREFLIGHT] model liveness check skipped (JARVIS_MODEL_PREFLIGHT=0)."
    lines = ["[PREFLIGHT] Model liveness (provider catalogues, no tokens spent):"]
    for provider, model, why in rep["dead"]:
        lines.append("  ❌ DEAD: " + provider + " '" + model + "' is NOT in the "
                     "live catalogue — " + why + " will fail on use. Fix the id.")
    _down = rep.get("down") or []
    if _down:
        _names = ", ".join(sorted({p for p, _m, _w, _r in _down}))
        lines.append("  ❌ NOT RUNNING: " + _names + " is not answering, so "
                     + ", ".join(sorted({w for _p, _m, w, _r in _down}))
                     + " CANNOT work. This is a fact, not a maybe — it is a local "
                       "daemon.")
        lines.append("     Start it:  powershell -ExecutionPolicy Bypass -File "
                     "tools" + chr(92) + "ensure_ollama.ps1")
    for provider, model, why, reason in rep["unknown"]:
        lines.append("  ⚠️  unverified: " + provider + " '" + model +
                     "' — catalogue unreachable (" + reason + "). Not necessarily dead.")
    if not rep["dead"] and not rep["unknown"] and not _down:
        lines.append("  ✅ all " + str(len(rep["alive"])) +
                     " configured model id(s) exist.")
    elif rep["alive"]:
        lines.append("  ✅ " + str(len(rep["alive"])) +
                     " other id(s) confirmed alive.")
    return "\n".join(lines)


def log_model_liveness() -> dict:
    """Run the liveness check against the live environment and print it.

    Safe on a background thread: it never raises and never blocks boot.
    """
    try:
        rep = check_model_liveness()
    except Exception as e:                              # noqa: BLE001
        _safe_print("[PREFLIGHT] model liveness check itself failed (" +
                    type(e).__name__ + ": " + str(e) + ") - continuing.")
        return {"dead": [], "alive": [], "unknown": [], "skipped": True}
    _safe_print(format_liveness(rep))
    return rep
