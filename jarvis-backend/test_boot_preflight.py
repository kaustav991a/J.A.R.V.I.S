r"""
test_boot_preflight.py — G5.7 startup config preflight (no real env)

Run: venv\Scripts\python.exe test_boot_preflight.py

Feeds synthetic env dicts + a fake exists() through boot_preflight.preflight and
asserts the required/recommended/file classification and the `ok` flag.
"""

from modules import boot_preflight as bp

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def _all_env():
    env = {names[0]: "x" for names in bp.REQUIRED_ANY.values()}
    env.update({name: "x" for name in bp.RECOMMENDED})
    return env


def test_all_present_ok():
    rep = bp.preflight(env=_all_env(), exists=lambda p: True)
    check(rep["ok"], "everything present -> ok")
    check(not rep["missing_required"] and not rep["missing_recommended"]
          and not rep["files_missing"], "no gaps reported when all present")


def test_missing_required_not_ok():
    rep = bp.preflight(env={}, exists=lambda p: True)
    check(not rep["ok"], "no LLM key -> not ok")
    check(any("LLM" in cap for cap, _ in rep["missing_required"]),
          "missing LLM group is reported")


def test_required_any_alternate_key_satisfies():
    rep = bp.preflight(env={"GROQ_API_KEY": "x"}, exists=lambda p: True)
    check(not rep["missing_required"], "either GROQ_API_KEYS or GROQ_API_KEY satisfies the group")


def test_whitespace_key_counts_as_missing():
    rep = bp.preflight(env={"GROQ_API_KEYS": "   \t"}, exists=lambda p: True)
    check(not rep["ok"], "a whitespace-only key is treated as missing")


def test_missing_critical_file_not_ok():
    target = next(iter(bp.CRITICAL_FILES))
    rep = bp.preflight(env=_all_env(), exists=lambda p: p != target)
    check(not rep["ok"], "a missing critical model file -> not ok")
    check(any(target == p for p, _ in rep["files_missing"]), "the missing file is listed")


def test_recommended_missing_still_ok():
    env = {names[0]: "x" for names in bp.REQUIRED_ANY.values()}  # required only
    rep = bp.preflight(env=env, exists=lambda p: True)
    check(rep["ok"], "missing recommended does not flip ok to False")
    check(len(rep["missing_recommended"]) == len(bp.RECOMMENDED),
          "all recommended gaps are still listed")


def test_recommended_files_are_soft():
    env = _all_env()
    rf = next(iter(bp.RECOMMENDED_FILES))
    rep = bp.preflight(env=env, exists=lambda p: p not in bp.RECOMMENDED_FILES)
    check(rep["ok"], "a missing recommended file does not flip ok")
    check(any(rf == p for p, _ in rep["files_recommended_missing"]),
          "missing recommended file is listed under the soft bucket")


def test_format_report_is_text():
    rep = bp.preflight(env={}, exists=lambda p: False)
    s = bp.format_report(rep)
    check(isinstance(s, str) and "REQUIRED" in s, "format_report returns a report string")
    ok_rep = bp.preflight(env=_all_env(), exists=lambda p: True)
    check("✅" in bp.format_report(ok_rep), "all-present report shows the ok marker")


# ═══════════════════════════════════════════════════════════════════════════
# MODEL LIVENESS — "is the key set" was never the question that bit us
# ═══════════════════════════════════════════════════════════════════════════
# Session 4. Every presence check above passed while two configured ids were dead:
#   F-46  llama-3.1-8b-instant, the desk chat default AND hardcoded in five files
#   F-67  llama-3.2-90b-vision-preview, hardcoded in screen_reader.py
# Both retired by Groq, both silent, and nothing in the repo asked the provider.
#
# Every test here injects `fetch`, so the suite makes no network call.

_FAKE_CATALOGUE = {
    "https://api.groq.com/openai/v1/models": [
        "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.6-27b",
        "whisper-large-v3",
    ],
    "https://openrouter.ai/api/v1/models": [
        "nvidia/nemotron-3.5-lightning:free", "openai/gpt-oss-20b:free",
        "google/gemma-4-26b-a4b-it:free", "nvidia/nemotron-nano-9b-v2:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
    ],
    "http://localhost:11434/api/tags": ["llama3.2:3b", "llava:latest"],
    "https://generativelanguage.googleapis.com/v1beta/models": [
        "gemini-3.7-flash", "gemini-3.1-flash-lite",
    ],
}

_HEALTHY_ENV = {
    "GROQ_MODEL": "openai/gpt-oss-120b",
    "GROQ_TOOL_MODEL": "openai/gpt-oss-120b",
    "GROQ_VISION_MODEL": "qwen/qwen3.6-27b",
    "GEMINI_MODEL": "gemini-3.7-flash",
    "OLLAMA_MODEL": "llama3.2:3b",
    "OLLAMA_VISION_MODEL": "llava",
    "OPENROUTER_MODELS": "nvidia/nemotron-3.5-lightning:free",
}


def _fake_fetch(url):
    return _FAKE_CATALOGUE[url]


def test_a_retired_id_is_named_dead():
    """The two ids that were actually dead, fed back in."""
    env = dict(_HEALTHY_ENV)
    env["GROQ_MODEL"] = "llama-3.1-8b-instant"                 # F-46
    env["GROQ_VISION_MODEL"] = "llama-3.2-90b-vision-preview"  # F-67

    rep = bp.check_model_liveness(fetch=_fake_fetch, env=env)
    dead = [m for _, m, _ in rep["dead"]]
    check("llama-3.1-8b-instant" in dead, f"F-46's dead id is flagged ({dead})")
    check("llama-3.2-90b-vision-preview" in dead, "F-67's dead id is flagged")
    check(not rep["unknown"], f"every provider answered ({rep['unknown']})")
    text = bp.format_liveness(rep)
    check("DEAD" in text, "the report says DEAD out loud")
    check("desk chat" in text, "and names what will break")


def test_a_healthy_configuration_is_quiet():
    rep = bp.check_model_liveness(fetch=_fake_fetch, env=dict(_HEALTHY_ENV))
    check(not rep["dead"], f"nothing is dead here ({rep['dead']})")
    check(bool(rep["alive"]), "the alive list is populated")
    check("all" in bp.format_liveness(rep), "and the report is a single clean line")


def test_a_bare_ollama_tag_matches_its_latest_form():
    """`llava` is configured; the daemon reports `llava:latest`. Same model."""
    rep = bp.check_model_liveness(fetch=_fake_fetch, env=dict(_HEALTHY_ENV))
    check(not any(m == "llava" for _, m, _ in rep["dead"]),
          "a bare ollama tag is not called dead")


def test_an_evergreen_alias_is_not_called_dead():
    """`gemini-flash-latest` resolves server-side and need not be listed.

    Calling it dead would be the check crying wolf on the default configuration,
    which is how a preflight earns the right to be ignored.
    """
    env = dict(_HEALTHY_ENV)
    env["GEMINI_MODEL"] = "gemini-flash-latest"
    rep = bp.check_model_liveness(fetch=_fake_fetch, env=env)
    check(not rep["dead"], f"an evergreen alias is not dead ({rep['dead']})")


def test_an_unreachable_provider_is_unknown_and_never_dead():
    """A laptop on a train must not be told its models are gone."""
    def _boom(url):
        raise OSError("no network")

    rep = bp.check_model_liveness(fetch=_boom, env=dict(_HEALTHY_ENV))
    check(not rep["dead"], f"offline is not dead ({rep['dead']})")
    check(bool(rep["unknown"]), "offline is reported as unverified")
    check("unverified" in bp.format_liveness(rep), "and the wording says so")

    rep2 = bp.check_model_liveness(fetch=lambda url: [], env=dict(_HEALTHY_ENV))
    check(not rep2["dead"] and bool(rep2["unknown"]),
          "an empty catalogue is unverified, not a death sentence")


def test_the_check_can_be_switched_off():
    rep = bp.check_model_liveness(fetch=_fake_fetch,
                                  env={"JARVIS_MODEL_PREFLIGHT": "0"})
    check(rep["skipped"], "JARVIS_MODEL_PREFLIGHT=0 skips it")
    check(not rep["dead"] and not rep["alive"], "and it probes nothing")
    check("skipped" in bp.format_liveness(rep), "and says so rather than staying mute")


def test_the_openrouter_defaults_are_checked_not_just_the_env_var():
    """The leg that was WHOLLY dead in August is normally unset in .env.

    Checking only `OPENROUTER_MODELS` would find nothing to check, because the
    router walks its own default list when the variable is absent.
    """
    ids = [m for provider, m, _ in bp.configured_models(env={})
           if provider == "openrouter"]
    check(len(ids) >= 2, f"the router's own OpenRouter lists are covered ({len(ids)})")
    check(all(i.endswith(":free") for i in ids), f"and they are free-tier ({ids})")


def test_reporting_never_raises_on_a_cp1252_console():
    """`sys.stdout.encoding` is cp1252 here, and printing ✅ raises INSIDE the
    thing that was reporting. Session 4 found 48 files exposed to that. A dead
    model must never be announced by an exception."""
    import io as _io
    import sys as _sys

    class _Cp1252(_io.TextIOBase):
        encoding = "cp1252"

        def write(self, s):
            s.encode("cp1252")      # raises on a tick, exactly like the console
            return len(s)

    saved = _sys.stdout
    _sys.stdout = _Cp1252()
    ok = True
    try:
        bp._safe_print("[PREFLIGHT] ✅ all good — no ❌ here ⚠️")
    except Exception:
        ok = False
    finally:
        _sys.stdout = saved
    check(ok, "a report full of ticks prints on a cp1252 console without raising")


TESTS = [test_all_present_ok, test_missing_required_not_ok,
         test_required_any_alternate_key_satisfies, test_whitespace_key_counts_as_missing,
         test_missing_critical_file_not_ok, test_recommended_missing_still_ok,
         test_recommended_files_are_soft, test_format_report_is_text,
         test_a_retired_id_is_named_dead,
         test_a_healthy_configuration_is_quiet,
         test_a_bare_ollama_tag_matches_its_latest_form,
         test_an_evergreen_alias_is_not_called_dead,
         test_an_unreachable_provider_is_unknown_and_never_dead,
         test_the_check_can_be_switched_off,
         test_the_openrouter_defaults_are_checked_not_just_the_env_var,
         test_reporting_never_raises_on_a_cp1252_console,]


def main():
    print("=" * 60)
    print("boot_preflight harness")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
