"""reasoning_guard.py — a model's private thinking must never be spoken aloud.

Live-gate session 4. The desk answered "confirm" with this, out loud, in the
room:

    [JARVIS] Here's a thinking process:
    1.  **Analyze User Input:**
       - User says: "confirm"
       - Context: Previous turns involved writing a Python script `add.py`...

No `<think>` tags anywhere — the model simply wrote its monologue as the answer.

`cloud_gateway.py` has carried a `_strip_reasoning` since 2026-08-19, when a
photo came back as pure monologue (see `test_reasoning_leak.py`). The DESK had
nothing. Root cause #4 again: the class was closed at the door someone was
looking through, and left open at the other one.

THREE LAYERS, BECAUSE ONE IS NOT ENOUGH
---------------------------------------
1. Ask the provider not to send it. Measured this session:

     groq openai/gpt-oss-20b   reasoning_format=hidden -> reasoning field empty,
                               content identical, and 0.6s instead of 1.0s
     groq allam-2-7b           reasoning_format -> 400, "not supported with this
                               model", so it must be sent per-model, not always
     openrouter nemotron       reasoning:{exclude:true} -> reasoning field empty,
                               content identical, 15s instead of 45s

2. Strip what still arrives tagged. `qwen/qwen3.6-27b` — a live id on this
   account, and one the .env comment offers as an option — streams a full
   `<think>` block INSIDE content: 2,431 characters of it, measured.

3. Refuse to speak an untagged monologue. There is no reliable way to split one
   from an answer, so this layer does not try: a reply that OPENS like thinking
   is replaced wholesale with a line that admits it. Speaking a fallback costs
   one repeated sentence. Speaking the monologue reads every private note in the
   prompt out loud, which is what the 2026-08-19 photo did.

The openers below are deliberately anchored to the START of the reply. A real
answer can discuss thinking ("I think the calendar is wrong, Sir"); no real
answer BEGINS "Here's a thinking process:".
"""



from __future__ import annotations

import re

#: Tokens to add to every budget so a reasoning model does not spend the whole
#: allowance thinking and return nothing. Measured on openai/gpt-oss-120b: 288
#: completion tokens for a JSON reply carrying a whole file, and 1,020 REASONING
#: tokens on a similar call -- so ~1,000 can disappear before a word is spoken.
#:
#: Prose length is controlled by each prompt's own instructions ("maximum 2
#: sentences"), not by this ceiling, which is why headroom does not make him
#: ramble. F-44 established it for the classifier: "the answer is ~40 tokens and
#: the rest is thinking".
#:
#: It lives HERE rather than in brain.py because llm_router needs it too, and a
#: second copy is root cause #4 waiting to happen: the day one is raised and the
#: other is not, the symptom is an empty answer from one provider only.
THINKING_HEADROOM = 1024

_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)
# An unclosed tag means the budget ran out mid-thought: everything after it is
# thinking that never became an answer.
_THINK_OPEN = re.compile(r"<think\b[^>]*>.*\Z", re.DOTALL | re.IGNORECASE)

#: Phrases a reasoning model opens its monologue with. Matched at the start only.
MONOLOGUE_OPENERS: tuple[str, ...] = (
    "here's a thinking process",
    "here is a thinking process",
    "here's my thinking",
    "here is my thinking",
    "thinking process:",
    "let me think through",
    "let's think through",
    "let me analyze",
    "let me analyse",
    "we need to figure out",
    "first, i need to understand",
    "step 1:",
    "**analyze",
    "**analyse",
    "1.  **",
    "1. **",
)

#: Spoken when a monologue is all that came back. It promises nothing and claims
#: nothing — the turn genuinely produced no answer, and saying so is the honest
#: outcome. Callers may pass their own.
DEFAULT_FALLBACK = ("I lost the thread of that one, Sir — my reasoning ran on "
                    "instead of answering. Say it again and I'll be brief.")


def strip_reasoning(text: str) -> str:
    """Remove `<think>` blocks, and an unclosed `<think>` through to the end."""
    out = _THINK_BLOCK.sub("", text or "")
    out = _THINK_OPEN.sub("", out)
    return out.strip()


def looks_like_monologue(text: str) -> bool:
    """True when a reply OPENS as thinking rather than as an answer."""
    head = (text or "").strip().lower()
    if not head:
        return False
    return head.startswith(MONOLOGUE_OPENERS)


def guard_spoken(text: str, fallback: str | None = None) -> str:
    """The one call sites make: what is safe to say out loud.

    Returns the stripped text, or `fallback` when nothing survives the strip or
    what survives is a monologue. An empty input stays empty — silence is a
    legitimate reply here (plenty of turns speak nothing at all) and inventing a
    sentence for it would make every quiet action chatty.
    """
    if not (text or "").strip():
        return ""
    cleaned = strip_reasoning(text)
    if not cleaned or looks_like_monologue(cleaned):
        return fallback if fallback is not None else DEFAULT_FALLBACK
    return cleaned
