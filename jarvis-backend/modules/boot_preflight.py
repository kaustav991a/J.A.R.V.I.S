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


def log_preflight() -> dict:
    """Run the preflight against the live environment and print the report.
    Returns the report dict (so a caller can react to `ok`)."""
    rep = preflight()
    print(format_report(rep), flush=True)
    return rep
