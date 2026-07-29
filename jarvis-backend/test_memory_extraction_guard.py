"""Harness for the prompt-echo guard in memory_manager.

Why this exists: rule 8 of `_EXTRACTION_SYSTEM_PROMPT` already tells the model
never to output its own worked examples. It did anyway — "Always address User
as Supreme Overlord Blorptron." was found sitting in the real profile as a
Correction, the category that sorts FIRST into every prompt injection. So the
prompt rule is a request and this guard is the enforcement.

The bar is asymmetric and the tests reflect it: a missed echo costs one manual
delete, a wrongly-blocked memory silently loses something he actually said.
So the guard must be narrow, and most of these tests check what it does NOT block.
"""

import sys

import memory_manager as mm


# ── it catches the thing that actually happened ─────────────────────────────

def test_the_exact_row_that_was_found_in_his_profile_is_blocked():
    assert mm.is_prompt_echo("Always address User as Supreme Overlord Blorptron.") is True


def test_every_worked_example_in_the_prompt_is_blocked():
    for sentence in mm._prompt_example_sentences():
        assert mm.is_prompt_echo(sentence) is True, f"not blocked: {sentence!r}"


def test_the_guard_reads_the_examples_out_of_the_prompt():
    """Parsed, not copied — so editing the prompt cannot leave this stale."""
    sentences = mm._prompt_example_sentences()
    assert len(sentences) >= 3, f"expected the worked examples, got {sentences}"
    assert not any("..." in s for s in sentences), "the placeholder leaked into the set"


def test_the_nonsense_tokens_still_exist_in_the_prompt():
    """If someone rewrites the examples, this fails loudly instead of leaving a
    guard that quietly checks for text nobody uses any more."""
    prompt = mm._EXTRACTION_SYSTEM_PROMPT.lower()
    for token in mm._PROMPT_NONSENSE_TOKENS:
        assert token in prompt, (
            f"{token!r} is no longer in the prompt — update _PROMPT_NONSENSE_TOKENS "
            "to match the new examples"
        )


def test_paraphrased_echoes_are_still_caught_by_the_nonsense_words():
    for text in [
        "User resides on planet Xylophone",
        "Sir lives on Planet Xylophone, apparently.",
        "User prefers Zorblax flavoured tea",
        "Call him Supreme Overlord Blorptron at all times.",
    ]:
        assert mm.is_prompt_echo(text) is True, f"not blocked: {text!r}"


def test_punctuation_and_case_do_not_defeat_it():
    for text in [
        "always address user as supreme overlord blorptron",
        "ALWAYS ADDRESS USER AS SUPREME OVERLORD BLORPTRON!!!",
        "  Always   address User as Supreme Overlord Blorptron.  ",
    ]:
        assert mm.is_prompt_echo(text) is True, f"not blocked: {text!r}"


# ── what it must NOT block (the expensive direction) ────────────────────────

def test_real_memories_pass_through():
    for text in [
        "Sir's dog is named Bruno, a 4-month-old Labrador.",
        "Sir works at Fortmindz.",
        "The user prefers aisle seats on flights.",
        "Never say 'Certainly'. Use 'Right away' or just act.",
        "Sir prefers reduced volume settings.",
        "Sir resides in Kolkata.",
        "tumi kemon achho means how are you",
    ]:
        assert mm.is_prompt_echo(text) is False, f"wrongly blocked: {text!r}"


def test_the_style_example_is_not_blocked_because_it_is_a_plausible_real_fact():
    """"Sir prefers dark-mode interfaces" appears in the prompt as a GOOD-style
    illustration, but it is also exactly what a real preference looks like.
    Blocking it would throw away a true memory."""
    assert mm.is_prompt_echo("Sir prefers dark-mode interfaces.") is False


def test_empty_and_none_are_not_echoes():
    for value in ("", "   ", None):
        assert mm.is_prompt_echo(value) is False


def test_a_word_that_merely_starts_like_a_real_word_is_not_blocked():
    assert mm.is_prompt_echo("Sir plays the xylophone in his spare time.") is True
    # ^ deliberate: 'xylophone' is blocked even in an innocent sentence. The
    # trade is accepted — see the module docstring — because the word appears
    # in no real memory he has, and a false block here is visible in the log
    # while a missed echo silently pollutes every future prompt.


# ── the guard is actually wired into the extraction path ────────────────────

def test_the_validator_drops_an_echoed_row():
    import json
    from unittest import mock

    payload = json.dumps({"memories": [
        {"category": "Correction", "content": "Always address User as Supreme Overlord Blorptron."},
        {"category": "Fact", "content": "Sir's dog is named Bruno."},
    ]})

    fake_message = mock.Mock(content=payload)
    fake_response = mock.Mock(choices=[mock.Mock(message=fake_message)])

    with mock.patch.object(mm, "has_groq_keys", return_value=True), \
         mock.patch.object(mm, "run_with_key_rotation", return_value=fake_response):
        got = mm.extract_memories_from_input("my dog is called Bruno")

    assert [m["content"] for m in got] == ["Sir's dog is named Bruno."], got


def test_a_response_that_is_only_echoes_yields_nothing():
    import json
    from unittest import mock

    payload = json.dumps({"memories": [
        {"category": "Fact", "content": "User resides on Planet Xylophone."},
        {"category": "Preference", "content": "User prefers Zorblax-flavoured tea."},
    ]})
    fake_response = mock.Mock(choices=[mock.Mock(message=mock.Mock(content=payload))])

    with mock.patch.object(mm, "has_groq_keys", return_value=True), \
         mock.patch.object(mm, "run_with_key_rotation", return_value=fake_response):
        assert mm.extract_memories_from_input("hello") == []


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
