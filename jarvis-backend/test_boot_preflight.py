r"""
test_boot_preflight.py — G5.7 startup config preflight (no real env)

Run: venv\Scripts\python.exe test_boot_preflight.py

Feeds synthetic env dicts + a fake exists() through boot_preflight.preflight and
asserts the required/recommended/file classification and the `ok` flag.
"""

import pathlib

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


def test_a_local_daemon_that_is_not_answering_is_reported_as_DOWN():
    """Tier 0.4. "Unverified" is the right word for a cloud provider behind a
    flaky network. It is the wrong word for a socket on this machine.

    Session 4 ran for hours with ollama not running: every local-vision feature
    was dead, row 12.1 could not be attempted, and nothing anywhere said so. The
    preflight now separates a local daemon that is DOWN -- a fact -- from a remote
    catalogue that is merely UNREACHABLE.
    """
    def _local_down(url):
        if "localhost" in url or "127.0.0.1" in url:
            raise OSError("connection refused")
        return _FAKE_CATALOGUE[url]

    rep = bp.check_model_liveness(fetch=_local_down, env=dict(_HEALTHY_ENV))
    down_providers = {p for p, _m, _w, _r in rep.get("down", [])}
    check(down_providers == {"ollama"},
          f"the local daemon is listed as down ({down_providers})")
    check(not rep["dead"],
          "and NOT as a dead model id -- the id is fine, the server is off")
    check(not any(p == "ollama" for p, _m, _w, _r in rep["unknown"]),
          "it is not filed under 'unverified' any more")

    text = bp.format_liveness(rep)
    check("NOT RUNNING" in text, "the report says NOT RUNNING")
    check("fact, not a maybe" in text,
          "and says plainly that this is not a maybe")
    check("ensure_ollama.ps1" in text,
          "and names the command that fixes it, rather than leaving him to guess")
    check("local vision" in text,
          "and names what stops working")


def test_a_remote_catalogue_being_unreachable_is_still_only_unverified():
    """The distinction has to cut both ways or it is just a louder alarm."""
    def _remote_down(url):
        if "localhost" in url or "127.0.0.1" in url:
            return _FAKE_CATALOGUE[url]
        raise OSError("no network")

    rep = bp.check_model_liveness(fetch=_remote_down, env=dict(_HEALTHY_ENV))
    check(not rep.get("down"),
          f"nothing local is reported down ({rep.get('down')})")
    check(bool(rep["unknown"]), "the remote ids are unverified")
    check(not rep["dead"], "and none of them is called dead")
    check("unverified" in bp.format_liveness(rep),
          "the wording stays cautious for a network we cannot see")


def test_an_installed_but_empty_ollama_is_also_down():
    """A running daemon with no models pulled cannot serve vision either, and the
    honest word for that is still 'cannot work'."""
    def _empty_local(url):
        if "localhost" in url or "127.0.0.1" in url:
            return []
        return _FAKE_CATALOGUE[url]

    rep = bp.check_model_liveness(fetch=_empty_local, env=dict(_HEALTHY_ENV))
    check(any(p == "ollama" for p, _m, _w, _r in rep.get("down", [])),
          "an empty local catalogue counts as down")
    reasons = {r for _p, _m, _w, r in rep.get("down", [])}
    check("no models installed" in reasons,
          f"and the reason distinguishes it from a refused connection ({reasons})")


def test_a_rejected_credential_is_not_filed_as_unreachable():
    """Tier 0.2, and the same mistake as tier 0.4 in a second place. When the
    provider REJECTS THE KEY it has answered; calling that "catalogue unreachable,
    not necessarily dead" turns a fact into weather. Waiting fixes a 429 and never
    fixes a bad key, so the two must not share a line."""
    def _bad_credential(url):
        if "generativelanguage" in url:
            raise OSError("HTTP Error 400: Bad Request")
        return _FAKE_CATALOGUE[url]

    rep = bp.check_model_liveness(fetch=_bad_credential, env=dict(_HEALTHY_ENV))
    providers = {p for p, _m, _w, _r in rep.get("bad_key", [])}
    check(providers == {"gemini"},
          f"the rejected credential is its own bucket ({providers})")
    check(not any(p == "gemini" for p, _m, _w, _r in rep["unknown"]),
          "and NOT filed under 'unverified'")
    check(not rep["dead"],
          "and not called a dead model id -- the id was never checked")

    text = bp.format_liveness(rep)
    check("CREDENTIAL REJECTED" in text, "the report names the real problem")
    check("the KEY is bad" in text, "and says which half is at fault")
    check("not a quota problem" in text,
          "and rules out the wrong fix explicitly")
    check("Waiting will not fix it" in text, "including the useless remedy")


def test_the_classifier_separates_invalid_from_exhausted():
    """The distinction that cost a hand audit on 2026-08-22."""
    for msg in ("400 API key not valid", "API_KEY_INVALID", "HTTP Error 401",
                "Unauthorized", "invalid api key", "HTTP Error 400: Bad Request"):
        check(bp.classify_credential_error(Exception(msg)) == "invalid",
              f"{msg!r} is a credential problem")
    for msg in ("429 RESOURCE_EXHAUSTED", "You exceeded your current quota",
                "rate limit reached, retry in 48s"):
        check(bp.classify_credential_error(Exception(msg)) == "quota",
              f"{msg!r} is a quota problem, which waiting DOES fix")
    for msg in ("connection refused", "timed out", "temporary failure in name "
                "resolution"):
        check(bp.classify_credential_error(Exception(msg)) is None,
              f"{msg!r} is about neither -- it stays unverified")


def test_each_gemini_key_is_judged_separately():
    """The preflight used to list the catalogue with the FIRST key only. With the
    first one invalid and four good ones behind it, the whole provider read as
    "unverified" while the cascade was working fine -- a report that makes a
    healthy system look broken and a broken key look like weather."""
    def _first_bad(url):
        if "NotAReal" in url:
            raise OSError("HTTP Error 400: Bad Request")
        return {"models": [{"name": "models/gemini-flash-latest"}]}

    env = dict(_HEALTHY_ENV)
    env["GEMINI_API_KEYS"] = "AIzaNotAReal,AIzaGoodOne,AIzaAlsoGood"
    v = bp.check_gemini_keys(env=env, fetch=_first_bad)
    check([k for _n, k, _d in v] == ["invalid", "valid", "valid"],
          f"each key gets its own verdict ({[k for _n, k, _d in v]})")

    text = bp.format_gemini_keys(v)
    check("#1 of 3" in text, "the bad one is identified by position")
    check("2 of 3" in text and "still accepted" in text,
          "and the working ones are reported as working")
    check("first leg" in text, "so he can see the cascade still has its first leg")


def test_all_keys_good_says_so_in_one_line():
    v = [(1, "valid", ""), (2, "valid", "")]
    text = bp.format_gemini_keys(v)
    check("all 2 Gemini key(s) accepted" in text, f"one clean line: {text.strip()}")
    check("INVALID" not in text.upper(), "and no alarm when there is nothing wrong")


def test_exhausted_keys_are_told_the_truth_about_more_keys():
    """Measured 2026-08-22: four keys' retry-after counted down IN STEP (49/48/48/
    47s, spread 2.3s), so they share one bucket. Adding more keys to the same
    Google project multiplies nothing, and saying otherwise would send him to
    generate keys that cannot help."""
    text = bp.format_gemini_keys([(1, "quota", ""), (2, "quota", "")])
    check("exhausted" in text, "exhaustion is named as exhaustion")
    check("share one bucket" in text, "and the shared bucket is stated")
    check("separate project" in text,
          "along with the only thing that DOES multiply the quota")
    check("INVALID" not in text.upper(),
          "and an exhausted key is never called invalid")


def test_no_key_at_all_is_reported_without_pretending_it_is_fatal():
    text = bp.format_gemini_keys([])
    check("no Gemini key configured" in text, "the absence is stated")
    check("GEMINI_API_KEY" in text, "with the variable to set")


def test_the_liveness_report_always_carries_the_new_bucket():
    """A caller that reads rep["bad_key"] must not have to guess whether the key
    exists. The skipped path returns the same shape as the real one."""
    env = dict(_HEALTHY_ENV)
    env["JARVIS_MODEL_PREFLIGHT"] = "0"
    skipped = bp.check_model_liveness(env=env)
    for key in ("dead", "alive", "unknown", "down", "bad_key"):
        check(key in skipped, f"the skipped report still has '{key}'")
    check(skipped["skipped"] is True, "and says it was skipped")


_SRC = (pathlib.Path(__file__).resolve().parent / "modules"
        / "llm_router.py").read_text(encoding="utf-8")


def test_both_key_variables_are_read_not_one_of_them():
    """The router MERGES GEMINI_API_KEYS with the legacy single GEMINI_API_KEY.
    The first version of this check used `or`, so it read the 4 pool keys, never
    saw the 5th, and reported "all 4 accepted" while the router was hitting
    API_KEY_INVALID on the one it had not been shown. Checking a subset and
    reporting confidently is the exact defect this function exists to catch."""
    seen = []

    def _ok(url):
        seen.append(url)
        return {"models": [{"name": "models/gemini-flash-latest"}]}

    env = dict(_HEALTHY_ENV)
    env["GEMINI_API_KEYS"] = "poolA,poolB"
    env["GEMINI_API_KEY"] = "legacyC"
    v = bp.check_gemini_keys(env=env, fetch=_ok)
    check(len(v) == 3, f"all three keys are checked, not two ({len(v)})")
    check(any("legacyC" in u for u in seen),
          "including the one in the legacy singular variable")
    check(sum(1 for u in seen if "poolA" in u) == 1,
          "and no key is checked twice")


def test_a_key_in_both_variables_is_only_checked_once():
    def _ok(_url):
        return {"models": [{"name": "models/gemini-flash-latest"}]}
    env = dict(_HEALTHY_ENV)
    env["GEMINI_API_KEYS"] = "same"
    env["GEMINI_API_KEY"] = "same"
    check(len(bp.check_gemini_keys(env=env, fetch=_ok)) == 1,
          "the duplicate is dropped, matching the router's own merge")


def test_the_report_names_the_variable_the_bad_key_lives_in():
    """It used to say "fix GEMINI_API_KEYS" when the bad key was the legacy
    singular GEMINI_API_KEY -- sending him to edit the wrong line, which is worse
    than saying nothing."""
    def _legacy_bad(url):
        if "legacyC" in url:
            raise OSError("HTTP Error 400: Bad Request")
        return {"models": [{"name": "models/gemini-flash-latest"}]}

    env = dict(_HEALTHY_ENV)
    env["GEMINI_API_KEYS"] = "poolA,poolB"
    env["GEMINI_API_KEY"] = "legacyC"
    v = bp.check_gemini_keys(env=env, fetch=_legacy_bad)
    text = bp.format_gemini_keys(v)
    check("(GEMINI_API_KEY)" in text,
          f"the singular variable is named: {text.splitlines()[0][-60:]}")
    check("GEMINI_API_KEYS)" not in text,
          "and the plural one, which is fine, is NOT blamed")
    check("#3 of 3" in text, "with the position of the bad key")


def test_an_invalid_key_is_dropped_before_the_first_request_pays_for_it():
    """Measured 2026-08-22: one invalid key in the rotation cost 60 SECONDS on the
    first vision call -- the Gemini leg spent its whole timeout inside the SDK's
    retries before the cascade moved on, and Groq then answered the same question
    in 2.4 s. The preflight knows at boot, for free; throwing that away means
    every process re-learns it the expensive way."""
    from modules import llm_router
    before = set(llm_router._gemini_dead_keys)
    try:
        llm_router._gemini_dead_keys.clear()
        n = bp.preseed_dead_gemini_keys(
            [(1, "valid", "GEMINI_API_KEYS"),
             (2, "invalid", "GEMINI_API_KEYS | 400"),
             (3, "quota", "GEMINI_API_KEYS | 429")])
        check(n == 1, f"one key is preseeded as dead ({n})")
        check(llm_router._gemini_dead_keys == {1},
              f"by ZERO-BASED index, matching the router ({llm_router._gemini_dead_keys})")
        check(2 not in llm_router._gemini_dead_keys,
              "and an EXHAUSTED key is NOT dropped -- waiting does fix that one")
    finally:
        llm_router._gemini_dead_keys.clear()
        llm_router._gemini_dead_keys.update(before)


def test_preseeding_nothing_touches_nothing():
    from modules import llm_router
    before = set(llm_router._gemini_dead_keys)
    check(bp.preseed_dead_gemini_keys([(1, "valid", "")]) == 0,
          "all-good verdicts preseed nothing")
    check(set(llm_router._gemini_dead_keys) == before,
          "and the router's state is untouched")


def test_gemini_gets_a_short_first_leg_because_something_faster_is_behind_it():
    """A 60 s runway is only safe when nothing faster follows. Groq vision answers
    in about 2 s, so patience in front of it is pure loss -- 63.4 s measured, then
    3.5 s after this cap."""
    from modules import llm_router
    check(llm_router._GEMINI_FIRST_LEG_S <= 30,
          f"the first leg is capped at {llm_router._GEMINI_FIRST_LEG_S}s")
    src = _SRC
    i = src.index("def universal_vision_call")
    # To the END of the function, not a guessed character count: the llava leg is
    # the last thing in it and a 4000-char window stopped short of the ollama
    # call, so the ordering assertion below could not find its second landmark.
    nxt = src.find(chr(10) + "def ", i + 10)
    j = len(src) if nxt == -1 else nxt   # it is the last function in the file
    body = src[i:j]
    check("min(timeout, _GEMINI_FIRST_LEG_S)" in body,
          "and the cap is applied to the request, not merely defined")
    check(body.index("groq_vision_model") < body.index("VISION_MODEL"),
          "Groq vision is tried BEFORE local llava, which needs 4.4 GB of RAM")
    check("strip_reasoning" in body,
          "and its <think> block is stripped, as screen_reader already does")


def test_the_thinking_headroom_has_exactly_one_definition():
    """It is applied in brain.py and in llm_router. Two copies is root cause #4
    waiting: raise one and not the other, and the symptom is an empty answer from
    one provider only."""
    from modules import reasoning_guard
    check(reasoning_guard.THINKING_HEADROOM >= 512,
          f"the shared constant exists ({reasoning_guard.THINKING_HEADROOM})")
    brain_src = (pathlib.Path(__file__).resolve().parent / "brain.py").read_text(
        encoding="utf-8")
    check("_THINKING_HEADROOM = reasoning_guard.THINKING_HEADROOM" in brain_src,
          "brain.py defers to it rather than declaring its own number")
    check("_THINKING_HEADROOM = 1024" not in brain_src,
          "so there is no second literal to drift")
    check("reasoning_guard.THINKING_HEADROOM" in _SRC,
          "and llm_router uses the same one")


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
         test_reporting_never_raises_on_a_cp1252_console,
         test_a_local_daemon_that_is_not_answering_is_reported_as_DOWN,
         test_a_remote_catalogue_being_unreachable_is_still_only_unverified,
         test_an_installed_but_empty_ollama_is_also_down,
         test_a_rejected_credential_is_not_filed_as_unreachable,
         test_the_classifier_separates_invalid_from_exhausted,
         test_each_gemini_key_is_judged_separately,
         test_all_keys_good_says_so_in_one_line,
         test_exhausted_keys_are_told_the_truth_about_more_keys,
         test_no_key_at_all_is_reported_without_pretending_it_is_fatal,
         test_the_liveness_report_always_carries_the_new_bucket,
         test_both_key_variables_are_read_not_one_of_them,
         test_a_key_in_both_variables_is_only_checked_once,
         test_the_report_names_the_variable_the_bad_key_lives_in,
         test_an_invalid_key_is_dropped_before_the_first_request_pays_for_it,
         test_preseeding_nothing_touches_nothing,
         test_gemini_gets_a_short_first_leg_because_something_faster_is_behind_it,
         test_the_thinking_headroom_has_exactly_one_definition,]


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
