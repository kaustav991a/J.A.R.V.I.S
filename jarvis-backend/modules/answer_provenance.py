"""Say where an answer came from, when it is not where he asked you to look.

WHY THIS EXISTS
---------------
Goal 1 of the tracker — **"He never claims what he did not do"** — measured on the
desk on 2026-08-29 by gate row `10.4`, which is one sentence:

    > go to python.org and find the latest Python version

What happened, from the log:

    [GOVERNANCE] action='tavily_search' -> tier=AUTO
    [ACTION ENGINE] Processing payload: {'action_type': 'tavily_search',
                                         'target': 'latest Python version'}
    [ACTION ENGINE] Tavily returned 5 result(s).
    [JARVIS] Python 3.14.6 is the latest stable release, Sir.

He never went to python.org. `web_browse` exists, is AUTO tier, and takes a URL —
he simply did not pick it. **The answer may well be right**, and that is what
makes this the failure this goal is about rather than a wrong fact: he was asked
to do a specific thing, did a different thing, and reported the result as though
the instruction had been carried out. Nothing he said was false; the sentence he
did not say is what makes it a claim.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
A **gate**, not a prompt instruction. The synthesis path streams sentence by
sentence and speaks each one as it arrives, so a rule in the prompt is a request
to a model that is already mid-sentence; this runs before the stream and states
the fact in the desk's own voice.

It is deliberately narrow. It fires only when all three are true:

  1. the request named a site to go to — "go to X", "open X", "visit X",
     "check X" with something that looks like a host or a bare domain;
  2. nothing that actually navigates ran (`web_browse`, `open_link`);
  3. something that searches DID run, so there is an answer to attribute.

The routing miss itself is not fixed here and should not be: choosing
`tavily_search` over `web_browse` is a competence question (tracker tier 2, where
`web` scores 1/4), and it will still be wrong sometimes after it improves. This
is the half that has to hold whether or not the routing does.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# Actions that genuinely fetch the page he named.
NAVIGATING = ("web_browse", "open_link", "web_click", "web_type", "web_scroll")

# Actions that answer from somewhere else.
SEARCHING = ("tavily_search", "web_search", "web_search_image")

# "go to python.org", "open the bbc.co.uk site", "visit github.com/x", and the
# bare-host form. The verb is required: "what is on python.org" is a question
# about a site, not an instruction to open one, and answering that from a search
# is not a claim about anything.
_GO = r"(?:go\s+to|goto|open|visit|browse\s+to|navigate\s+to|head\s+to|pull\s+up|check)"
_HOST = r"(?:https?://)?(?:www\.)?([a-z0-9][-a-z0-9]*(?:\.[a-z0-9][-a-z0-9]*)+)"
_ASKED = re.compile(rf"\b{_GO}\b[^.?!]{{0,40}}?\b{_HOST}", re.I)

# Hosts that are the name of a service rather than a page to read. "check gmail"
# is not a request to open mail.google.com in a browser, and treating it as one
# would put a correction on top of a perfectly ordinary answer.
_NOT_A_PAGE = {"gmail.com", "mail.google.com", "google.com", "calendar.google.com"}


def site_asked_for(text: str) -> Optional[str]:
    """The site he asked you to open, or None if he asked no such thing."""
    if not text:
        return None
    match = _ASKED.search(text)
    if not match:
        return None
    host = match.group(1).lower().strip(".")
    if host in _NOT_A_PAGE:
        return None
    # A host has a dot and a plausible TLD. "go to sleep" must not read as a site.
    if "." not in host or len(host.rsplit(".", 1)[-1]) < 2:
        return None
    return host


def provenance_note(text: str, executed: Iterable[str]) -> Optional[str]:
    """One sentence naming what was actually done, or None when nothing is owed.

    None is the common case and is meant to be: a correction attached to an
    answer that needed none is its own kind of noise, and this project has
    already paid for a guard that fired too often.
    """
    site = site_asked_for(text)
    if not site:
        return None
    ran = [a for a in executed if a]
    if any(a in NAVIGATING for a in ran):
        return None                      # he did go there
    if not any(a in SEARCHING for a in ran):
        return None                      # nothing to attribute yet
    return (f"I didn't open {site} itself, Sir — what follows is from a web "
            f"search.")


# ── the other half of the same habit: silence is not an answer ───────────────
#
# Gate row `10.9`, same session. "good morning" produced this, in full:
#
#     [BACKDOOR] Received command: good morning [auth: flagged_bypass]
#     [BRAIN] Payload -> 14 msgs | ~13,981 chars | ~3,495 tokens est
#     INFO: 127.0.0.1 - "POST /api/backdoor HTTP/1.1" 200 OK
#
# No `[JARVIS]` line. Nothing spoken, nothing sent, HTTP 200. `speak_text("")`
# returns without a sound by design — the reasoning guard strips a monologue down
# to nothing and the speaker must not narrate the emptiness — so a model that
# produced no text at all is **indistinguishable from a desk that never heard
# him**. He asks again; it happens again; the conclusion available to him is that
# the microphone or the machine is broken.
#
# This is the same failure as the unauthorised calendar and as K3's locked key
# store: an absence reported as a result. The honest move costs one sentence.
SILENT_ANSWER = ("I didn't get an answer together on that one, Sir. Nothing "
                 "came back from the brain — ask me again and I'll have "
                 "another go.")


def answer_or_admission(text: Optional[str]) -> str:
    """The answer, or the admission that there wasn't one. Never the empty string.

    Deliberately not in `speaker.speak_text`: some callers pass empty text on
    purpose — a stripped monologue, a status with no message — and narrating
    those would put a sentence where the code meant silence. This is for the
    places that were trying to ANSWER him.
    """
    return (text or "").strip() or SILENT_ANSWER
