"""
test_conversational_truthfulness.py — conversation reports too (F-16)
=====================================================================

Live gate, 2026-08-09, 01:52. An ordinary voice turn, no briefing involved:

    "Now that I've adjusted the camera, I can see you clearly, Sir."

It adjusted nothing. There is no action in the registry that adjusts a camera.
Same false-completion class as F-09, different function: that guard wraps
`generate_briefing` only, and `process_command` / `process_stream` — the paths
every spoken and typed turn actually goes through — were unguarded.

F-09's allowlist could not simply be reused. A briefing REPORTS, so seven verbs
cover everything it may legitimately claim. Conversation says "I've told you",
"I've noticed", "I've opened it"; the briefing set would flatten all of it.

So the policy is wider and still closed on both sides:

  TIER 1  speech, perception, analysis — always admissible, no evidence needed.
  TIER 2  discrete capability (opened, closed, sent, played) — admissible ONLY
          when a real action ran in the last few turns, read from the
          "[Executed: ...]" stubs, which are written from a PARSE of what was
          dispatched rather than from what the model said about itself.
  ELSE    stripped. "adjusted", "calibrated", "fixed" are in neither set — not
          blocklisted, just never admitted, exactly like every verb nobody
          thought of. That is the F-09 lesson: the OPEN set must be the one
          that loses.

This harness drives the real functions — the policy lifted from brain.py's
source, and `process_stream` itself executed against a fake model — because a
guard that is only grepped for is not a guard (the f84f644 lesson).
"""

import ast
import pathlib
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent
BRAIN = HERE / "brain.py"

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS  {label}")
    else:
        _failed += 1
        print(f"FAIL  {label}")


# brain.py imports the whole model stack; lift the pure policy out of its
# SOURCE and run the real functions instead of importing the module.
_SOURCE = BRAIN.read_text(encoding="utf-8", errors="replace")
_TREE = ast.parse(_SOURCE)

_WANTED_CONSTS = {
    "_ALLOWED_REPORTING", "_IRREGULAR_PARTICIPLES", "_COMPLETION_RE",
    "_MANDATE_RE", "_BARE_COMPLETION", "_CONVERSATIONAL_REPORTING",
    "_CAPABILITY_CLAIMS", "_AUTHORING_CLAIMS", "_BARE_CONVERSATIONAL",
    "_FENCE_RE", "_SENTENCE_SPLIT_RE", "_EVIDENCE_WINDOW_MSGS",
    "_EXECUTED_STUB_RE",
    # Batch 3, finding A3: process_stream now shares ONE security scanner with
    # process_command, so the lifted namespace needs its vocabulary too.
    "_LOCK_MARKER", "_UNLOCK_PHRASES",
    # F-60, session 4: the guard caught the past tense and let every
    # forward-looking promise through. The capability rule lives in the shared
    # predicate, so this namespace needs its vocabulary too — and this harness
    # going to ZERO CHECKS with a NameError is exactly how it told us so.
    "_NO_TOOL_VERBS", "_PROMISE_RE", "_PROMISE_NEGATED_RE",
    # F-74: the schedule-claim guard shares this namespace. Its vocabulary has to
    # come with it, and the harness told us so the same way it did for F-60 -
    # zero checks and a NameError, which is this file working as designed.
    "_SCHEDULE_NOUNS", "_CLOCK_RE", "_HIS_SCHEDULE_RE", "_ADMITS_ABSENCE",
}
_WANTED_FUNCS = (
    "_claims_a_completion", "_actions_ran_recently", "_conversational_allowed",
    "_sentence_is_unfounded", "_strip_unfounded_conversational_claims",
    "_security_locked",
    "_promises_a_capability_it_lacks",          # F-60
    "_asserts_his_schedule",                    # F-74
)

_consts = [n for n in _TREE.body
           if isinstance(n, ast.Assign)
           and any(isinstance(t, ast.Name) and t.id in _WANTED_CONSTS
                   for t in n.targets)]
_funcs = [n for n in _TREE.body
          if isinstance(n, ast.FunctionDef) and n.name in _WANTED_FUNCS]

_NS: dict = {}
if _funcs and _consts:
    import re as _re
    _NS["re"] = _re
    _mod = ast.Module(body=_consts + _funcs, type_ignores=[])
    exec(compile(ast.fix_missing_locations(_mod), str(BRAIN), "exec"), _NS)

_strip = _NS.get("_strip_unfounded_conversational_claims")
_ran = _NS.get("_actions_ran_recently")
_allowed_for = _NS.get("_conversational_allowed")


def _stub(atypes="hud_open_widget"):
    return {"role": "assistant", "content": f"[Executed: {atypes}. Done.]"}


# ── the policy ───────────────────────────────────────────────────────────────

def test_the_guard_exists():
    check(bool(_strip), "_strip_unfounded_conversational_claims exists in brain.py")
    check(len(_funcs) == len(_WANTED_FUNCS),
          f"all {len(_WANTED_FUNCS)} policy functions are module-level, found {len(_funcs)}")
    check(len(_consts) == len(_WANTED_CONSTS),
          f"all {len(_WANTED_CONSTS)} vocabularies are module-level, found {len(_consts)}")


def test_the_exact_live_gate_sentence_is_removed():
    spoken = "Now that I've adjusted the camera, I can see you clearly, Sir."
    check("adjusted the camera" not in _strip(spoken, actions_ran=False),
          "the 01:52 confabulation is gone with no action evidence")
    check("adjusted the camera" not in _strip(spoken, actions_ran=True),
          "...and STILL gone even when actions did run — no registry action adjusts a camera, "
          "so 'adjusted' is in neither tier")


def test_the_vague_modification_family_is_never_admitted():
    # These are the verbs a model reaches for when it is narrating effort it
    # did not spend. None of them names anything this system can do.
    for verb in ("adjusted", "calibrated", "tuned", "fixed", "repaired",
                 "configured", "optimised", "optimized", "recalibrated",
                 "aligned", "corrected", "improved"):
        s = f"Of course, Sir. I have {verb} the settings for you."
        out = _strip(s, actions_ran=True)
        check(f"I have {verb}" not in out, f"'{verb}' is not admitted even with evidence")
        check("Of course, Sir." in out, f"...and the rest of the reply survives ({verb})")


def test_an_unlisted_verb_is_still_caught_because_this_is_an_allowlist():
    for s in ("I have throttled your network.", "I have defenestrated the router.",
              "I have reticulated the splines."):
        out = _strip(f"Certainly, Sir. {s}", actions_ran=True)
        check(s not in out, f"unlisted verb still stripped: {s[:36]}")


def test_capability_claims_need_evidence():
    s = "Yes, Sir. I have opened Chrome for you."
    check("I have opened Chrome" not in _strip(s, actions_ran=False),
          "'opened' is stripped when nothing ran — the turn itself did nothing")
    check("I have opened Chrome" in _strip(s, actions_ran=True),
          "'opened' survives when a real action ran a turn or two ago")


def test_every_capability_verb_is_evidence_gated_both_ways():
    for verb, sentence in (
        ("closed", "I have closed the window."),
        ("sent", "I've sent that email to your accountant."),
        ("deleted", "I have deleted the file from your desktop."),
        ("played", "I have played your evening playlist."),
        ("muted", "I have muted the room."),
        ("saved", "I've saved the note to your desktop."),
        ("cancelled", "I have cancelled your ten o'clock."),
        ("locked", "I have locked the workstation."),
    ):
        check(sentence not in _strip(f"Right away, Sir. {sentence}", actions_ran=False),
              f"unevidenced '{verb}' claim is removed")
        check(sentence in _strip(f"Right away, Sir. {sentence}", actions_ran=True),
              f"evidenced '{verb}' claim is kept — this strips lies, not speech")


def test_speech_and_perception_never_need_evidence():
    # The whole risk of porting F-09 here was flattening ordinary conversation.
    keep = [
        "I have told you about that already, Sir.",
        "I've noticed you have been at the desk for three hours.",
        "I have checked the weather for you.",
        "I've searched my records and found nothing.",
        "I have been monitoring the system since you left.",
        "I've taken the liberty of noting the time.",
        "I have remembered that, Sir.",
        "I've heard you, Sir.",
        "I have three items to mention.",
        "I've calculated the difference at 14 percent.",
    ]
    for s in keep:
        check(_strip(s, actions_ran=False) == s,
              f"kept with NO evidence, correctly: {s[:52]}")


def test_a_bare_completion_claim_is_never_admitted():
    # Unconditional, unlike the capability tier. A claim that names no object
    # cannot be checked against anything, so "an action ran recently" is not
    # evidence FOR it — only an excuse. "I've taken care of it" is also the
    # exact wording that shipped the F-09 fabrication.
    for s in ("I have taken care of that for you.", "I've handled that, Sir.",
              "I have sorted that out.", "I've seen to it."):
        for evidence in (False, True):
            check(_strip(s, actions_ran=evidence) != s,
                  f"bare completion stripped (actions_ran={evidence}): {s[:40]}")


def test_untouched_replies_come_back_byte_identical():
    # Conversational replies carry code, lists and deliberate line breaks. A
    # guard that reflows every reply it inspects damages more turns than it
    # repairs, so a clean reply must survive unchanged — not merely equivalent.
    for text in (
        "Good evening, Sir. The weather is 28 degrees and your calendar is clear.",
        "Here are the three options:\n\n1. Restart it\n2. Reinstall it\n3. Ignore it",
        "Line one.\nLine two.\n\nLine four.",
        "The value is 3.5 and the tolerance is 0.2.",
    ):
        check(_strip(text, actions_ran=False) is text or _strip(text, actions_ran=False) == text,
              f"unchanged, including its line breaks: {text[:40]!r}")
    multiline = "Line one.\nLine two.\n\nLine four."
    check("\n\n" in _strip(multiline, actions_ran=False),
          "the blank line is preserved, not collapsed to a single space")


def test_a_code_fence_admits_authoring_verbs_and_survives_intact():
    reply = ("Certainly, Sir. I have written a small function for you.\n\n"
             "```python\ndef add(a, b):\n    return a + b\n```\n\n"
             "I have adjusted nothing else.")
    out = _strip(reply, actions_ran=False)
    check("I have written a small function" in out,
          "'written' is admitted when the artifact is in the message — the fence is the evidence")
    check("def add(a, b):\n    return a + b" in out,
          "the fenced code survives with its indentation and newlines intact")
    check("I have adjusted nothing else." not in out,
          "...and a vague claim in the same reply is still cut")


def test_authoring_verbs_are_NOT_admitted_without_a_fence():
    s = "Certainly, Sir. I have written the report to your desktop."
    check("I have written the report" not in _strip(s, actions_ran=False),
          "'written' with no fence and no action is a claim about a FILE — stripped")


def test_a_mandate_is_allowed_here_although_the_briefing_forbids_it():
    # Same words, opposite prior. "As you asked" is routinely TRUE mid-
    # conversation because the user did just ask; in an unprompted 07:00
    # briefing it is an invented mandate. The difference is deliberate.
    s = "As you asked, Sir, here is the summary."
    check(_strip(s, actions_ran=False) == s,
          "an invoked mandate survives in conversation — _MANDATE_RE is briefing-only")


def test_it_never_returns_empty():
    out = _strip("I have adjusted the camera. I have calibrated the microphone.",
                 actions_ran=False)
    check(bool(out.strip()), "output is never empty even if every sentence was cut")
    check("adjusted" not in out.lower() and "calibrated" not in out.lower(),
          "...and the fabrication is still gone")


def test_the_honorific_follows_the_speaker():
    out = _strip("I have adjusted the camera.", actions_ran=False, title="Madam")
    check("Madam" in out, "Mousumi is addressed as Madam in the all-dropped fallback")


def test_empty_input_is_safe():
    check(_strip("") == "", "empty string passes through")
    check(_strip(None) is None, "None passes through without raising")


# ── the evidence, which must come from a parse and not from narration ────────

def test_evidence_is_read_from_executed_stubs_only():
    check(_ran([]) is False, "an empty buffer is no evidence")
    check(_ran([_stub()]) is True, "an [Executed: ...] stub is evidence")
    # ⚠️ This assertion used to read `is True`, and it was pinning the DEFECT.
    # Review batch 3, finding A1: process_stream wrote "[Action executed. Done.]"
    # whenever the reply parsed as JSON and carried NO actions — so a turn that
    # dispatched nothing minted the only evidence this function accepts, and the
    # next turn's guard admitted every capability claim as founded. The stub is
    # no longer written, and it is no longer evidence.
    check(_ran([{"role": "assistant", "content": "[Action executed. Done.]"}]) is False,
          "the old empty-action stub is NOT evidence — it never meant anything ran")
    check(_ran([{"role": "assistant",
                 "content": "I have opened Chrome for you, Sir."}]) is False,
          "the model SAYING it acted is not evidence — that is the bug, not the proof")
    check(_ran([{"role": "user", "content": "[Executed: open_app. Done.]"}]) is False,
          "a user turn quoting the stub is not evidence")


def test_the_evidence_window_is_narrow_and_is_a_named_constant():
    window = _NS.get("_EVIDENCE_WINDOW_MSGS")
    check(isinstance(window, int) and 0 < window <= 12,
          f"_EVIDENCE_WINDOW_MSGS is a small named constant, got {window!r}")
    filler = [{"role": "user", "content": "and?"} for _ in range(window)]
    check(_ran([_stub()] + filler) is False,
          "a stub older than the window stops counting — a claim cannot resurface 20 turns later")
    check(_ran(filler[:window - 1] + [_stub()]) is True,
          "a stub inside the window counts")


def test_malformed_buffer_entries_do_not_raise():
    check(_ran(["not a dict", None, 7, _stub()]) is True,
          "junk in the buffer is skipped rather than crashing the reply path")


def test_the_two_tiers_are_actually_different_sets():
    tier1 = _allowed_for(actions_ran=False)
    tier2 = _allowed_for(actions_ran=True)
    check(tier2 > tier1, "evidence only ever WIDENS what may be said, never narrows it")
    check("opened" in tier2 and "opened" not in tier1,
          "capability verbs live behind the evidence gate")
    check("noticed" in tier1, "speech and perception need no evidence")
    check("adjusted" not in tier2,
          "the vague-modification family is in neither tier, by construction")
    check("written" in _allowed_for(actions_ran=False, fenced=True)
          and "written" not in tier1,
          "a fence, and only a fence, admits the authoring verbs")


# ── wiring: the real functions, not a grep ───────────────────────────────────

def _call_in(fn_name, target):
    """True if function `fn_name` in brain.py calls `target` anywhere."""
    fn = next((n for n in ast.walk(_TREE)
               if isinstance(n, ast.FunctionDef) and n.name == fn_name), None)
    if fn is None:
        return False
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == target for n in ast.walk(fn))


def test_process_command_is_wired_to_the_guard():
    check(_call_in("process_command", "_strip_unfounded_conversational_claims"),
          "process_command passes its conversational reply THROUGH the guard")
    check(_call_in("process_command", "_actions_ran_recently"),
          "...and supplies real evidence rather than defaulting to permissive")


def test_process_stream_is_wired_to_the_same_policy():
    check(_call_in("process_stream", "_sentence_is_unfounded"),
          "process_stream judges sentences with the SAME predicate — one policy, two paths")
    check(_call_in("process_stream", "_actions_ran_recently"),
          "...on the same evidence")


# ── the streaming generator, executed for real against a fake model ──────────

def _build_stream(chunks, working_memory=None):
    """Exec the REAL process_stream against stubs and return (yields, buffer)."""
    ns = dict(_NS)
    buffer = list(working_memory or [])

    class _Mem:
        def get_working_memory(self):
            return list(buffer)

        def add_to_working_memory(self, role, content):
            buffer.append({"role": role, "content": content})

        def get_context_window(self, limit=None):
            return list(buffer)

        def recall_semantic_context(self, *a, **k):
            return ""

    class _Epi:
        def recall_past_sessions(self, *a, **k):
            return ""

    class _MemMgr:
        def get_balanced_memories_for_prompt(self, *a, **k):
            return []

        def format_memory_block(self, *a, **k):
            return ""

    fake_av = types.ModuleType("ambient_vision")
    fake_av.shared_optical_cache = {"camera_active": False}
    # Review batch 5: the visual block is gated on FRESHNESS now, not on the
    # bare `camera_active` flag — a dead daemon left that flag True forever.
    fake_av.vision_is_fresh = lambda *a, **k: False
    sys.modules["ambient_vision"] = fake_av

    import datetime as _dt
    import json as _json
    import os as _os
    ns.update({
        "memory": _Mem(), "episodic_memory": _Epi(), "memory_manager": _MemMgr(),
        "datetime": _dt, "json": _json, "os": _os,
        "classify_intent": lambda *a, **k: {"intent": "CHAT", "brevity_mode": False},
        "_BREVITY_VETO_KEYWORDS": (),
        "_should_force_action_json": lambda *a, **k: False,
        "_action_likely": lambda *a, **k: False,
        "_has_indic_script": lambda *a, **k: False,
        "_ROMANISE_NUDGE": "",
        "build_dynamic_prompt": lambda *a, **k: "",
        "get_persona_instructions": lambda *a, **k: "",
        "universal_llm_call": lambda *a, **k: iter(chunks),
    })
    fn = next((n for n in _TREE.body
               if isinstance(n, ast.FunctionDef) and n.name == "process_stream"), None)
    if fn is None:
        return None, buffer
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[fn], type_ignores=[])), str(BRAIN), "exec"), ns)
    out = list(ns["process_stream"]("what do you see", "KAUSTAV"))
    return out, buffer


def test_the_stream_withholds_a_claim_it_cannot_evidence():
    chunks = ["Now that I've ", "adjusted the camera", ", I can see you clearly, Sir. ",
              "You look tired."]
    out, buffer = _build_stream(chunks)
    spoken = "".join(out or [])
    check("adjusted the camera" not in spoken,
          "the live-gate sentence never leaves the generator — half a claim cannot be recalled")
    check("You look tired." in spoken, "the honest sentence still streams out")


def test_the_stream_does_not_leave_the_fabrication_in_working_memory():
    out, buffer = _build_stream(
        ["I've adjusted the camera. ", "The weather is 28 degrees."])
    stored = " ".join(str(m.get("content", "")) for m in buffer
                      if isinstance(m, dict) and m.get("role") == "assistant")
    check("adjusted the camera" not in stored,
          "what is buffered is what was SPOKEN — a fabrication in the buffer becomes "
          "established context and the next turn builds on it")
    check("28 degrees" in stored, "and the true half is remembered")


def test_the_stream_leaves_a_clean_reply_alone():
    chunks = ["Good evening, Sir. ", "The weather is 28 degrees ", "and your calendar is clear."]
    out, _ = _build_stream(chunks)
    check("".join(out or []) == "".join(chunks),
          "a reply with no claims streams through byte-for-byte")


def test_the_stream_passes_a_json_action_turn_through_untouched():
    payload = '{"actions": [{"action_type": "open_app", "target": "chrome"}]}'
    out, buffer = _build_stream([payload[:20], payload[20:]])
    check("".join(out or []) == payload,
          "an action payload is never sentence-split or rewritten")
    stored = " ".join(str(m.get("content", "")) for m in buffer
                      if isinstance(m, dict) and m.get("role") == "assistant")
    # `native_app_launcher`, not `open_app`: finding A2 moved this path onto the
    # shared parse spine, which NORMALISES the model's alias to the action type
    # the engine actually dispatches. That is the property the stub claims for
    # itself — a parse of what was dispatched, not of what the model called it.
    check("[Executed: native_app_launcher" in stored,
          "...and it still produces the Executed stub the NEXT turn reads as evidence")


def test_a_json_reply_with_no_actions_leaves_no_evidence_behind():
    """Finding A1, end to end through the real generator.

    `{"actions": []}` is a turn that dispatched nothing. It must not write the
    stub that the next turn reads as proof something ran.
    """
    payload = '{"actions": []}'
    _, buffer = _build_stream([payload])
    stored = " ".join(str(m.get("content", "")) for m in buffer
                      if isinstance(m, dict) and m.get("role") == "assistant")
    check("[Executed:" not in stored,
          f"no execution stub is written for an empty action list; got {stored!r}")
    check("[Action executed" not in stored, "and not the old one either")
    check(_ran(buffer) is False,
          "so the next turn still has to earn its capability claims")


def test_the_stream_honours_evidence_from_the_previous_turn():
    prior = [{"role": "user", "content": "open chrome"},
             {"role": "assistant", "content": "[Executed: open_app. Done.]"}]
    out, _ = _build_stream(["Yes, Sir. I have opened Chrome for you."], prior)
    check("I have opened Chrome" in "".join(out or []),
          "a capability claim right after a real action streams out intact")


def test_the_stream_never_goes_silent():
    out, _ = _build_stream(["I have adjusted the camera. ", "I have calibrated the mic."])
    spoken = "".join(out or [])
    check(bool(spoken.strip()), "an entirely fabricated reply still says something")
    check("adjusted" not in spoken and "calibrated" not in spoken,
          "...and says none of the fabrication")


TESTS = [
    test_the_guard_exists,
    test_the_exact_live_gate_sentence_is_removed,
    test_the_vague_modification_family_is_never_admitted,
    test_an_unlisted_verb_is_still_caught_because_this_is_an_allowlist,
    test_capability_claims_need_evidence,
    test_every_capability_verb_is_evidence_gated_both_ways,
    test_speech_and_perception_never_need_evidence,
    test_a_bare_completion_claim_is_never_admitted,
    test_untouched_replies_come_back_byte_identical,
    test_a_code_fence_admits_authoring_verbs_and_survives_intact,
    test_authoring_verbs_are_NOT_admitted_without_a_fence,
    test_a_mandate_is_allowed_here_although_the_briefing_forbids_it,
    test_it_never_returns_empty,
    test_the_honorific_follows_the_speaker,
    test_empty_input_is_safe,
    test_evidence_is_read_from_executed_stubs_only,
    test_the_evidence_window_is_narrow_and_is_a_named_constant,
    test_malformed_buffer_entries_do_not_raise,
    test_the_two_tiers_are_actually_different_sets,
    test_process_command_is_wired_to_the_guard,
    test_process_stream_is_wired_to_the_same_policy,
    test_the_stream_withholds_a_claim_it_cannot_evidence,
    test_the_stream_does_not_leave_the_fabrication_in_working_memory,
    test_the_stream_leaves_a_clean_reply_alone,
    test_the_stream_passes_a_json_action_turn_through_untouched,
    test_a_json_reply_with_no_actions_leaves_no_evidence_behind,
    test_the_stream_honours_evidence_from_the_previous_turn,
    test_the_stream_never_goes_silent,
]


def main():
    print("=" * 60)
    print("conversational truthfulness harness (F-16)")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
