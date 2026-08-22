r"""ram_budget.py — 16 GB is the constraint, so measure it instead of guessing.

TIER 3.2. The numbers below were taken on this box on 2026-08-22, live.

    model                 on disk   resident (/api/ps)   context
    llama3.2:3b           1.88 GB   2.55 GB              4096
    llava:latest          4.41 GB   4.39 GB              -
    qwen2.5-coder         4.36 GB   (not loaded)
    llama3:8b             4.34 GB   (not loaded)

    free RAM, nothing warm                       4.61 GB
    free RAM with llava resident                 2.74 GB
    free RAM immediately after unloading llava   6.87 GB
    llava load + one inference at 2.56 GB free   91.9 s

WHAT THE MEASUREMENT CHANGED
----------------------------
The first version of this module REFUSED to load a model that did not fit, on the
assumption that a tight load would fail, or thrash badly enough to be useless.
That assumption is wrong, and the 91.9 s row is why: with 2.56 GB free — far less
than llava's 4.4 GB — the call still completed and returned a correct answer,
about three times slower than a warm one. Slow, not broken.

So refusing was the wrong lever, and wrong in the expensive direction: it would
have removed a working feature. Two things follow, and they are the whole module.

1.  A TIGHT LOAD NEEDS A LONGER DEADLINE, NOT A SHORTER ONE. A fixed 120 s timeout
    over a call that legitimately takes 92 s leaves 28 s of margin. Any less free
    memory than that measurement had and the request is cancelled while the model
    is still loading — then reported as "vision offline" when vision was in fact
    working. That false failure is the real defect here, and it is the opposite of
    what a naive guard would have caused.

2.  NOTHING EVER SET keep_alive, SO ONE SCREEN READ HELD 4.4 GB FOR FIVE MINUTES.
    Ollama's default keep_alive is 5m. A single "what is on my screen?" parked
    llava in memory long after the answer was spoken, and the 6.87 GB row is what
    came back the instant it was unloaded. When memory is tight a one-off vision
    call should release its model promptly; when there is room, keeping it warm is
    the better trade and this module says nothing.

THE LOCAL TEXT LEG IS DELIBERATELY NOT WRAPPED
----------------------------------------------
Only the two local VISION legs consult this module. The local text model is
1.88 GB on disk and 2.55 GB resident -- it fits in the free memory this box
normally has, so there is no tight case to advise on. More to the point, keeping
the text model warm is the whole value of having a local fallback at all: a short
keep_alive there would guarantee the ~27 s cold reload it is supposed to avoid.
A written decision, not an omission; if the small model is ever swapped for an 8B
one, revisit this paragraph first.

WHERE REFUSAL WOULD STILL BE WRONG
----------------------------------
Every local model call in this codebase sits BEHIND a cloud provider: the vision
cascade tries Gemini first and reaches llava only once Gemini has already failed,
and the local text leg is the offline fallback. At that point there is no cheaper
alternative left to escalate to, so refusing does not redirect the work — it
throws the work away. This module therefore advises, and never blocks.

FOOTPRINTS ARE READ, NOT HARDCODED
----------------------------------
Sizes come from ollama's own endpoints, so they stay right when he pulls a
different model. A resident model's footprint is already paid, so calling it again
is free no matter how little memory is left — checking that first avoids the
absurd case of declaring a model unaffordable *because it is already loaded*.
Note from the table that disk size and resident size disagree in both directions
(llama3.2 costs 36% more loaded than on disk; llava about the same), so there is
no single multiplier here worth pretending to.
"""

from __future__ import annotations

import os
import time

#: Free RAM that should REMAIN after a model is resident for a load to count as
#: comfortable. Not a hard floor — see the module docstring.
HEADROOM_GB = float(os.getenv("JARVIS_RAM_HEADROOM_GB", "0.6"))

#: Deadline for a local call that has to load under memory pressure. The measured
#: worst case was 91.9 s; the default leaves real margin over it, because the
#: failure this prevents is a false "offline" on a call that was about to succeed.
TIGHT_TIMEOUT_S = float(os.getenv("JARVIS_TIGHT_LOCAL_TIMEOUT_S", "240"))

#: How long ollama should hold a model that only just fitted. Short, so a one-off
#: screen read gives the memory back instead of sitting on it for five minutes.
TIGHT_KEEP_ALIVE = os.getenv("JARVIS_TIGHT_KEEP_ALIVE", "30s")

_CACHE_TTL_S = 20.0
_cache: dict = {"tags": (0.0, None), "ps": (0.0, None)}


def _base_url() -> str:
    return (os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
            .rsplit("/api/", 1)[0])


def available_gb():
    """Free RAM in GiB, or None if psutil cannot say."""
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception:                                   # noqa: BLE001
        return None


def _endpoint(kind: str, fetch=None):
    """The tags or ps endpoint, briefly cached. Empty when the daemon is silent."""
    if fetch is not None:
        try:
            return fetch(kind) or {}
        except Exception:                               # noqa: BLE001
            return {}
    now = time.monotonic()
    at, body = _cache[kind]
    if body is not None and now - at < _CACHE_TTL_S:
        return body
    try:
        import requests
        body = requests.get(_base_url() + "/api/" + kind, timeout=6).json() or {}
    except Exception:                                   # noqa: BLE001
        return {}
    _cache[kind] = (now, body)
    return body


def _sizes(body) -> dict:
    return {str(m.get("name", "")): float(m.get("size", 0)) / (1024 ** 3)
            for m in (body.get("models") or []) if m.get("name")}


def installed_gb(fetch=None) -> dict:
    """On-disk size of every pulled model, from the tags endpoint."""
    return _sizes(_endpoint("tags", fetch))


def resident_gb(fetch=None) -> dict:
    """Loaded size of every model currently in memory, from the ps endpoint."""
    return _sizes(_endpoint("ps", fetch))


def _match(model: str, sizes: dict):
    """Find the model in sizes, treating a bare tag and its :latest as one."""
    if not model or not sizes:
        return None
    if model in sizes:
        return sizes[model]
    want = model.split(":", 1)[0]
    for name, size in sizes.items():
        if name.split(":", 1)[0] == want:
            return size
    return None


def advise(model: str, fetch=None, free_gb=None) -> dict:
    """What will loading this model cost, and what should the caller do about it?

    Returns a dict rather than a verdict, because the two useful outputs are not
    booleans: a deadline and a keep_alive. The `blocked` key is always False and
    is kept in the shape so no future caller can read its absence as permission.
    """
    free = available_gb() if free_gb is None else free_gb
    already = _match(model, resident_gb(fetch))
    need = already if already is not None else _match(model, installed_gb(fetch))

    out = {"model": model, "resident": already is not None, "need_gb": need,
           "free_gb": free, "blocked": False,
           "comfortable": True, "tight": False,
           "keep_alive": None, "timeout_floor_s": None}

    if already is not None:
        out["reason"] = (f"{model} is already resident ({already:.2f} GB) — "
                         f"calling it again costs no extra memory")
        return out
    if free is None or need is None:
        unknown = "free memory" if free is None else f"the size of {model!r}"
        out["reason"] = f"{unknown} is unknown — treating the load as normal"
        return out
    if need + HEADROOM_GB <= free:
        out["reason"] = (f"{model} needs {need:.2f} GB and {free:.1f} GB is free "
                         f"— room to spare")
        return out

    out.update(comfortable=False, tight=True,
               keep_alive=TIGHT_KEEP_ALIVE, timeout_floor_s=TIGHT_TIMEOUT_S,
               reason=(f"{model} needs {need:.2f} GB but only {free:.1f} GB is "
                       f"free. It will still answer — measured 91.9 s at 2.6 GB "
                       f"free, about 3x a warm call — so the deadline is raised "
                       f"to {TIGHT_TIMEOUT_S:.0f}s and the model is released "
                       f"after {TIGHT_KEEP_ALIVE} instead of ollama's 5m"))
    return out


def apply(model: str, payload: dict, timeout: float, label: str = "",
          fetch=None, free_gb=None) -> float:
    """Stamp the payload with a keep_alive and return the deadline to use.

    One function so the local legs cannot drift apart: root cause #4 in this
    project is a fix applied at one door while its sibling stays open.

    `fetch` and `free_gb` exist so this is testable. Without them the first
    version of this function could only be exercised against whatever the machine
    happened to be doing, and two of its tests duly passed or failed according to
    how much RAM was free at that second -- which is not a test.
    """
    plan = advise(model, fetch=fetch, free_gb=free_gb)
    if plan["tight"]:
        payload["keep_alive"] = plan["keep_alive"]
        timeout = max(timeout, plan["timeout_floor_s"])
        print(f"[RAM] {label or model}: {plan['reason']}", flush=True)
    return timeout
