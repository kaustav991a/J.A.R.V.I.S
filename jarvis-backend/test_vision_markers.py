"""Harness for markers on the vision path — the photo that answered `[[LOOKUP: …]]`.

Seen on the device 2026-08-20 at 17:15. A photo of a motorcycle with the caption
"what is this bike? what is the mileage?" came back as, in full:

    [[LOOKUP: Royal Enfield Hunter 350 mileage ARAI real world]]

The vision half worked — it read the bike off the tank correctly. What reached the
screen was the machinery: `see()` was a parallel implementation of `think()` that
never got `think()`'s post-processing, while sharing the persona that TEACHES the
marker. So the model did exactly as instructed and the gateway printed the
instruction.

Three defects from the one omission, and the leak is the least of them:

  1. the search never ran, so "what is the mileage" was never answered at all;
  2. `[[REMEMBER: …]]` from a photo turn would leak the same way AND be
     discarded — a fact stated over a photo was unstorable;
  3. the raw reply went into the rolling history (`cloud_gateway.py:1229`), so a
     leaked marker became context for every following turn.

The fix is one shared `_resolve_markers()` rather than a second copy of the block,
because a third caller is exactly how this bug happened once already.

The property that needs its own test is the second pass. `see()`'s transcript
carries a base64 image in a list-shaped `content`, and the second pass runs on the
TEXT leg — handing that message to the text model is either an error or a very
expensive no-op. So the image is replaced by the same `[sent a photo]` stand-in the
history uses, and the model's own first reply carries what it saw.

No network and no model: `_complete`, `_web_lookup` and `remember_fact` are stubbed.
"""

import asyncio
import os
import sys

os.environ.setdefault("CLOUD_GATEWAY_MODE", "webhook")

import cloud_gateway as cg  # noqa: E402

_real_complete = cg._complete
_real_web_lookup = cg._web_lookup
_real_remember = cg.remember_fact
_real_facts_block = cg._facts_block

# every `_complete` call this test saw: (messages, capability)
_calls: list[tuple] = []
# every query handed to `_web_lookup`
_searched: list[str] = []
# every fact handed to `remember_fact`
_remembered: list[str] = []

IMG = "QUJD"  # not a real JPEG; nothing here decodes it


def _replies(*canned):
    """Answer each `_complete` call from the list, recording what it was asked."""

    def _fake(messages, model="", capability="text"):  # noqa: ARG001
        _calls.append((messages, capability))
        i = len(_calls) - 1
        return canned[i] if i < len(canned) else canned[-1]

    cg._complete = _fake


def _setup():
    _calls.clear()
    _searched.clear()
    _remembered.clear()
    cg._HISTORY.clear()
    cg._RECALLED.clear()
    cg.DATABASE_URL = ""
    cg.WEB_LOOKUP = True

    def _fake_lookup(query):
        _searched.append(query)
        return "- Hunter 350: about 36 kmpl claimed (2026-08-01)"

    async def _fake_remember(fact):
        _remembered.append(fact)
        return True

    async def _no_facts():
        return ""

    cg._web_lookup = _fake_lookup
    cg.remember_fact = _fake_remember
    cg._facts_block = _no_facts


def _teardown():
    cg._complete = _real_complete
    cg._web_lookup = _real_web_lookup
    cg.remember_fact = _real_remember
    cg._facts_block = _real_facts_block


def _see(caption="what is this bike? what is the mileage?"):
    return asyncio.run(cg.see(1, IMG, caption, "Kaustav", "Sir", surface="app"))


# ── the leak itself ─────────────────────────────────────────────────────────


def test_a_photo_lookup_marker_never_reaches_him():
    _replies("[[LOOKUP: Royal Enfield Hunter 350 mileage]]",
             "That is a Hunter 350, Sir — about 36 kmpl in real use.")
    out = _see()
    assert "[[" not in out and "LOOKUP" not in out, out
    assert out == "That is a Hunter 350, Sir — about 36 kmpl in real use."


def test_a_photo_lookup_actually_runs_the_search():
    _replies("[[LOOKUP: Royal Enfield Hunter 350 mileage]]", "About 36 kmpl, Sir.")
    _see()
    assert _searched == ["Royal Enfield Hunter 350 mileage"], _searched


def test_a_photo_with_no_marker_is_left_alone():
    _replies("A Royal Enfield, Sir. Well used.")
    out = _see()
    assert out == "A Royal Enfield, Sir. Well used."
    assert len(_calls) == 1 and _searched == []


# ── the second pass must not re-send the image ───────────────────────────────


def test_a_photo_second_pass_carries_no_image():
    _replies("[[LOOKUP: Hunter 350 mileage]]", "About 36 kmpl, Sir.")
    _see()
    assert len(_calls) == 2, f"expected a second pass, got {len(_calls)} call(s)"
    second = _calls[1][0]
    assert not any(isinstance(m.get("content"), list) for m in second), \
        "the image message survived into the text-leg second pass"
    assert not any("data:image" in str(m.get("content", "")) for m in second), \
        "base64 image data reached the text model"


def test_a_photo_second_pass_still_says_what_was_asked():
    _replies("[[LOOKUP: Hunter 350 mileage]]", "About 36 kmpl, Sir.")
    _see()
    blob = " ".join(str(m.get("content", "")) for m in _calls[1][0])
    assert "sent a photo" in blob, "the second pass lost the fact that this was a photo"
    assert "what is the mileage" in blob, "the second pass lost the question"


def test_a_photo_second_pass_carries_the_first_reply():
    # what it SAW is only in its own first answer; without that the results
    # arrive attached to nothing
    _replies("[[LOOKUP: Hunter 350 mileage]]", "About 36 kmpl, Sir.")
    _see()
    assert any(m.get("role") == "assistant" and "LOOKUP" in str(m.get("content"))
               for m in _calls[1][0]), "the model's own request left the transcript"


def test_a_photo_second_pass_uses_the_text_leg():
    _replies("[[LOOKUP: Hunter 350 mileage]]", "About 36 kmpl, Sir.")
    _see()
    assert _calls[0][1] == "vision" and _calls[1][1] == "text", \
        [c[1] for c in _calls]


def test_a_photo_second_pass_carries_the_results():
    _replies("[[LOOKUP: Hunter 350 mileage]]", "About 36 kmpl, Sir.")
    _see()
    blob = " ".join(str(m.get("content", "")) for m in _calls[1][0])
    assert "36 kmpl claimed" in blob, "the snippets never reached the second pass"


# ── failure, and the once-only rule ──────────────────────────────────────────


def test_a_photo_lookup_failure_is_admitted_not_promised():
    cg._web_lookup = lambda q: ""  # noqa: ARG005
    _replies("[[LOOKUP: Hunter 350 mileage]]", "I could not find the figure, Sir.")
    out = _see()
    assert len(_calls) == 2, "a failed search must still get a second pass"
    blob = " ".join(str(m.get("content", "")) for m in _calls[1][0])
    assert "LOOKUP FAILED" in blob, "the model was not told the search came back empty"
    assert out == "I could not find the figure, Sir."


def test_a_photo_lookup_asks_only_once():
    _replies("[[LOOKUP: one]]", "[[LOOKUP: two]] About 36 kmpl, Sir.")
    out = _see()
    assert len(_calls) == 2, f"a second request was honoured: {len(_calls)} calls"
    assert _searched == ["one"], _searched
    assert "[[" not in out, out


def test_a_photo_lookup_is_skipped_when_web_is_off():
    cg.WEB_LOOKUP = False
    try:
        _replies("[[LOOKUP: Hunter 350 mileage]] A Royal Enfield, Sir.")
        out = _see()
        assert _searched == [] and len(_calls) == 1
        # still stripped: a marker he can read is worse than one nobody acted on
        assert "[[" not in out, out
        assert out == "A Royal Enfield, Sir."
    finally:
        cg.WEB_LOOKUP = True


# ── remember, and the history ────────────────────────────────────────────────


def test_a_photo_remember_marker_is_stored_and_stripped():
    _replies("Noted, Sir. [[REMEMBER: He rides a Royal Enfield Hunter 350]]")
    out = _see("this is my bike")
    assert _remembered == ["He rides a Royal Enfield Hunter 350"], _remembered
    assert "[[" not in out and "REMEMBER" not in out, out
    assert out == "Noted, Sir."


def test_a_photo_remember_survives_a_lookup():
    _replies("[[LOOKUP: Hunter 350 mileage]]",
             "About 36 kmpl, Sir. [[REMEMBER: He rides a Hunter 350]]")
    out = _see()
    assert _remembered == ["He rides a Hunter 350"], _remembered
    assert "[[" not in out, out


def test_a_photo_history_keeps_the_clean_reply():
    _replies("[[LOOKUP: Hunter 350 mileage]]", "About 36 kmpl, Sir.")
    _see()
    stored = [m["content"] for m in cg._HISTORY[cg._memory_key(1)]
              if m["role"] == "assistant"]
    assert stored == ["About 36 kmpl, Sir."], stored
    assert not any("[[" in s for s in stored), stored


# ── the text path must not regress ───────────────────────────────────────────


def test_a_text_turn_still_resolves_its_markers():
    _replies("[[LOOKUP: Hunter 350 mileage]]", "About 36 kmpl, Sir.")
    out = asyncio.run(cg.think(2, "mileage of a Hunter 350?", "Kaustav", "Sir"))
    assert out == "About 36 kmpl, Sir."
    assert _searched[-1] == "Hunter 350 mileage", _searched


def test_a_text_turn_still_stores_its_facts():
    _replies("Noted, Sir. [[REMEMBER: He rides a Hunter 350]]")
    out = asyncio.run(cg.think(3, "I ride a Hunter 350", "Kaustav", "Sir"))
    assert _remembered == ["He rides a Hunter 350"], _remembered
    assert out == "Noted, Sir."


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        _setup()
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
        finally:
            _teardown()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
