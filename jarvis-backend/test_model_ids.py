"""Harness: no configured model id is one the provider has already retired.

This exists because the same bug landed twice in one week, and both times every
other harness stayed green while it did.

  * Groq decommissioned `llama-3.3-70b-versatile` on 2026-08-16. The code
    default was moved in `a943582` — and `render.yaml` still declared the dead
    id, where a declared value WINS over the code default. The gateway's Groq
    leg would have started 404ing in production.
  * OpenRouter withdrew three of the four `:free` variants this repo walked.
    `OPENROUTER_TOOL_MODELS` was WHOLLY dead. Nothing noticed, because
    OpenRouter is the LAST leg of both cascades: it only runs after Groq and
    Gemini have already failed, which is exactly when nobody is reading logs.

A model id is a piece of configuration that rots on someone else's schedule, so
it needs a check that fails at home rather than in production.

WHAT THIS CAN AND CANNOT DO
---------------------------
It is offline and deterministic, so it cannot ask a provider what still exists —
that is `--live` work, and it costs a network round trip per id. What it pins is
the cheaper half: an id known to be dead must never come back, and the OpenRouter
legs must stay on `:free` variants, because the account is free-tier and a paid id
fails at request time rather than at start-up.

Re-verify the live catalogues by hand after any provider retirement mail:

    from groq import Groq; Groq(api_key=k).models.list()
    https://openrouter.ai/api/v1/models
"""

import os
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# Ids these providers have withdrawn. Add to this list when a retirement mail
# arrives — that is the cheapest moment, and it makes the removal enforceable
# instead of a note in a doc.
RETIRED = {
    # Groq, 2026-08-16
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "mixtral-8x7b-32768",
    "gemma-7b-it",
    "llama-3.1-70b-versatile",
    # OpenRouter :free variants withdrawn by 2026-08-15
    "openai/gpt-oss-120b:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
}


def _retired(model: str) -> bool:
    return model.strip() in RETIRED


def test_groq_model_defaults_are_not_retired():
    """The desk router's two Groq ids — chat and tool — must both be alive.

    Asserted on the RESOLVED values, not on the source text: a `.env` that pins a
    dead id is the same outage as a dead default, and only the resolved value
    catches both.
    """
    from modules import llm_router as lr

    assert not _retired(lr.GROQ_MODEL), f"GROQ_MODEL is retired: {lr.GROQ_MODEL}"
    assert not _retired(lr.GROQ_TOOL_MODEL), \
        f"GROQ_TOOL_MODEL is retired: {lr.GROQ_TOOL_MODEL}"
    # The tool leg is the one that matters most: TOOL_PROVIDERS puts groq first,
    # so every tool turn in the agent layer goes through this single id.
    assert lr.GROQ_TOOL_MODEL, "GROQ_TOOL_MODEL must not be empty"


def test_openrouter_lists_are_populated_and_alive():
    from modules import llm_router as lr

    for name, models in (("OPENROUTER_MODELS", lr.OPENROUTER_MODELS),
                         ("OPENROUTER_TOOL_MODELS", lr.OPENROUTER_TOOL_MODELS)):
        assert models, f"{name} is empty — the last leg of the cascade cannot answer"
        dead = [m for m in models if _retired(m)]
        assert not dead, f"{name} still walks retired ids: {dead}"


def test_openrouter_lists_stay_on_the_free_tier():
    """A paid id in a free-tier fallback fails at request time, not at start-up.

    OpenRouter serves the same model under two ids — `x` and `x:free` — so a
    withdrawn `:free` variant leaves a live-looking base id behind. That is
    exactly how the dead list survived a casual read.
    """
    from modules import llm_router as lr

    for name, models in (("OPENROUTER_MODELS", lr.OPENROUTER_MODELS),
                         ("OPENROUTER_TOOL_MODELS", lr.OPENROUTER_TOOL_MODELS)):
        paid = [m for m in models if not m.endswith(":free")]
        assert not paid, f"{name} names non-free ids: {paid}"


def test_render_yaml_does_not_declare_a_retired_model():
    """`render.yaml` is the artefact that deploys, and a value set there WINS
    over the code default — so fixing the default alone fixes nothing in
    production. This is the check that would have caught it."""
    spec = yaml.safe_load((REPO / "render.yaml").read_text(encoding="utf-8"))
    checked = 0
    for service in spec.get("services", []):
        for var in service.get("envVars", []) or []:
            key, value = var.get("key", ""), var.get("value")
            if not isinstance(value, str) or "MODEL" not in key.upper():
                continue
            checked += 1
            assert not _retired(value), \
                f"render.yaml {key} declares a retired model: {value}"
    assert checked, "no *MODEL* env var found in render.yaml — has it been renamed?"


def test_cloud_gateway_resolves_a_live_model():
    """The gateway's own default, resolved the way the process resolves it.

    Imported last: it calls `load_dotenv(override=True)` at module scope, so it
    must not colour the earlier assertions.
    """
    os.environ.setdefault("CLOUD_GATEWAY_MODE", "webhook")
    import cloud_gateway as cg

    assert cg.GROQ_MODEL, "cloud gateway has no Groq model at all"
    assert not _retired(cg.GROQ_MODEL), \
        f"cloud gateway GROQ_MODEL is retired: {cg.GROQ_MODEL}"


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
