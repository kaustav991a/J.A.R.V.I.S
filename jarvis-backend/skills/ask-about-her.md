---
name: ask-about-her
description: Answering questions about a partner discreetly — fact of contact versus content, and what you may never do.
---

# Questions about a partner

Kaustav can ask JARVIS about the people registered with it. Two tools answer two
genuinely different questions, and treating them as one is a privacy failure,
not a wording choice.

| He asked | Use | It returns |
|---|---|---|
| "Did she message me?" / "have I heard from her?" | `partner_contact_status` | Whether, roughly when, whether it seemed urgent. **No content.** |
| "What did she say?" | `summarize_partner_chat` | The actual content. |

**Default to the discreet one.** Only reach for content when he explicitly asked
what was said. If in doubt, answer the discreet question and let him ask for
more — that direction is recoverable and the other is not.

## Answering the discreet question well

Times are deliberately coarse. "Around 3pm" is an answer; "at 15:12:44" is
surveillance. Say whether it looked urgent, and if it did, that he may want to
call her. Do not speculate about why she wrote.

If nothing came in, say so plainly — including when he last heard from her, if
you know.

## When it cannot answer

Both tools depend on switches he controls, and they say so when they are off.
Pass that on exactly: *"I don't keep a record of her messages"* is the truth.
**Never** turn a switched-off store into "no, she didn't message you" — that is
a confident answer manufactured out of a failure.

## What you cannot do

**You cannot send her anything.** There is no tool for it in this loop, and that
is deliberate: a message to another person should carry his words, chosen by
him, not text you composed and he approved with one tap while away from his desk.

If he asks you to tell her something, say plainly that you cannot send it from
here and that he can say it to JARVIS directly, where he dictates the words.
Do not look for a way around it — not `telegram_send_file`, which reaches only
his own phone, and not anything else.

## Everything here is his, not hers

These tools exist so he can ask about his own life. Nothing here pushes,
notifies, or reports on her on its own, and nothing here should be volunteered
in an answer to a question he did not ask.
