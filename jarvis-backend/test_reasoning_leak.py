"""Harness: a reasoning model's thinking must never reach the operator.

2026-08-19, 19:16 IST. A photo sent from the phone's camera button came back as
the model's entire internal monologue and no answer at all. Read off the device
the next morning:

    <think>
    The user has sent a photo of what appears to be a smartwatch on a wrist.
    The watch face shows the time "07:16 PM" and the date "WED 08/19".
    ...
    Given the user has a dog named Kitty, it might be related, but it's hard to
    be certain.
    The user's prompt is just "The operator sent this photo without a caption —
    react to it helpfully."

— cut off there, with no closing tag and no reply behind it.

Three failures in one bubble, and only one of them is cosmetic:

  1. the reasoning was displayed as the answer;
  2. it carried the facts block and the injected caption prompt out with it, so a
     private note about a dog and the shape of the system prompt were both on
     screen;
  3. `max_tokens=700` was spent thinking, so the answer was never generated. The
     reply was not badly worded — it did not exist.

The path there was ordinary. `LLM_PROVIDER_VISION=gemini` with a free key whose
quota was exhausted (`/health`: `vision.gemini_ok: 0`, `last_error_was_quota:
true`), so `_complete` fell back to Groq — `GROQ_VISION_MODEL=qwen/qwen3.6-27b`,
which is a reasoning model. Nothing was misconfigured. The fallback leg simply
had a property the primary did not, and no test asked about it.

Vision itself was fine, which is worth recording: the model read the watch face,
the date on it, and the blurred stairs behind it correctly. Only the packaging
was wrong, which is why `/health` showed nothing amiss and why this needs a
harness rather than a metric.

WHAT THIS PINS
--------------
Offline and deterministic — no provider is called. The properties are the ones
the bug had: thinking is removed wherever it appears, an unterminated block takes
everything after it, an answer that survives stripping is left alone, and a reply
that is empty afterwards names itself instead of arriving blank.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Set BEFORE the import, the same way `test_app_link.py` does: the module-level
# config reads this, and `webhook` keeps the import from starting a poller.
os.environ.setdefault("CLOUD_GATEWAY_MODE", "webhook")

import cloud_gateway as cg  # noqa: E402


# The reply as it actually arrived, truncated exactly where the token budget ran
# out. Kept verbatim because a paraphrase would lose the property that matters:
# there is no closing tag, so a regex written only for balanced pairs passes this
# whole thing through.
LEAKED = '''<think>
The user has sent a photo of what appears to be a smartwatch on a wrist.
The watch face shows the time "07:16 PM" and the date "WED 08/19".
Given the user has a dog named Kitty, it might be related.
The user's prompt is just "The operator sent this photo without a caption —
react to it helpfully."'''


def test_a_finished_thought_is_removed_and_the_answer_kept():
    out = cg._strip_reasoning("<think>weighing it up</think>A fine evening, sir.")
    assert out == "A fine evening, sir."


def test_thinking_is_removed_wherever_it_sits():
    # not always first: a model that thinks again mid-answer would otherwise
    # leak the second block while the first was stripped
    out = cg._strip_reasoning("Before. <think>hmm</think> After.")
    assert "hmm" not in out
    assert "Before." in out and "After." in out


def test_an_unterminated_thought_takes_everything_after_it():
    """The case that actually shipped.

    No closing tag means the budget ran out mid-thought, so there is no answer
    behind it — and a regex for balanced pairs alone would have passed the whole
    monologue straight through.
    """
    assert cg._strip_reasoning(LEAKED) == ""


def test_the_leak_carries_nothing_private_out_with_it():
    out = cg._strip_reasoning(LEAKED)
    # the facts block and the injected caption prompt were both visible on the
    # phone; neither may survive
    assert "Kitty" not in out
    assert "without a caption" not in out


def test_an_ordinary_answer_is_untouched():
    plain = "It is 7:16, sir, and the stairs behind you suggest you are already moving."
    assert cg._strip_reasoning(plain) == plain


def test_a_reply_that_is_empty_afterwards_says_so():
    """An empty bubble reads as the app being broken.

    This project's recurring failure is a state that does not name itself, and a
    blank message is the purest form of it.
    """
    said = cg._answerable(cg._strip_reasoning(LEAKED))
    assert said.strip()
    assert "again" in said.lower()


def test_an_answer_is_never_replaced_by_the_admission():
    assert cg._answerable("Quite so, sir.") == "Quite so, sir."


def test_the_answer_budget_is_not_back_at_the_size_that_lost_one():
    """`max_tokens=700` is the number the monologue exceeded.

    Read from source rather than called, because calling it needs a provider.
    """
    src = (HERE / "cloud_gateway.py").read_text(encoding="utf-8")
    assert "max_tokens=700," not in src, "the budget that lost an answer is back"
