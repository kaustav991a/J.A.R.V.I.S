"""Harness: the HUD must not say a source is OFFLINE before it has asked.

WHY THIS EXISTS
---------------
Measured on the desk 2026-08-30, driving row `18.7`. The vitals panel read

    VITAL SIGNS
    VITALS OFFLINE

while the very same URL, fetched from the very same page a second later,
returned:

    {"configured":true,"steps":799,"heart_rate":0,"calories":803.9,"active_mins":10}

Nothing was offline. The widget was still waiting - `/api/health/summary` reaches
Google Fit and takes about ten seconds - and its initial state was
`{configured: false}`, which its render treats as "the source is down".

The same eight lines appeared in three widgets:

    const [data, setData] = useState({ configured: false, ... });
    try { const res = await fetch(url); if (res.ok) setData(await res.json()); }
    catch (e) { /* silent */ }
    if (!data.configured) return <span>VITALS OFFLINE</span>;

Three different situations rendered identically:

  1. **not asked yet** - a claim made before any request went out;
  2. **the request failed** - the catch was empty, so a network error and a
     source answering "not configured" were indistinguishable;
  3. **genuinely unavailable** - the only one the word OFFLINE actually means.

`loading` was tracked in all three and used only to spin an icon, never to change
the message. This is the same failure the backend spent two days removing from
what the desk SAYS - stating something it does not know - living in the HUD
instead. **A screen is an assertion too**, and this one was read by a person who
would reasonably have gone looking for a broken Google token.

Fixed in `src/useSource.js`, one hook rather than three copies, because three
copies of eight lines is how it happened. Root cause #4 again.

WHAT THIS PINS
--------------
That the widgets go through the shared hook, that the hook keeps the phases
apart, and that a panel cannot be stranded off-screen by a window resize (row
`18.6`, whose clamp is easy to delete by accident and impossible to notice).
"""

from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "jarvis-frontend" / "src"
WIDGETS = ("components/HealthWidget.jsx", "components/CalendarWidget.jsx",
           "components/EmailWidget.jsx")

_checks = 0
_fails: list[str] = []


def check(cond: bool, label: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _fails.append(label)
        print(f"FAIL  {label}")


def _read(rel: str) -> str:
    p = SRC / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def test_the_hook_exists_and_names_every_phase():
    src = _read("useSource.js")
    check(bool(src), "src/useSource.js exists")
    for phase in ("loading", "error", "ready", "unconfigured"):
        check(f'"{phase}"' in src, f"the hook distinguishes the {phase!r} phase")
    check("console.warn" in src,
          "a failed fetch is reported, not swallowed - the silent catch is what "
          "made a browser fault look like a backend one")


def test_no_widget_still_carries_its_own_silent_fetch():
    for name in WIDGETS:
        src = _read(name)
        check(bool(src), f"{name} exists")
        if not src:
            continue
        check("useSource" in src, f"{name} goes through the shared hook")
        check("/* silent */" not in src,
              f"{name} no longer swallows its fetch error")
        check("useState({ configured: false" not in src.replace("\n", " "),
              f"{name} no longer starts by asserting the source is off")


def test_offline_is_only_said_when_the_source_said_so():
    """The actual regression: the word OFFLINE reachable from the loading state."""
    for name in WIDGETS:
        src = _read(name)
        if not src:
            continue
        # the render must branch on the phase, not on a bare `configured` flag
        check("phase" in src, f"{name} renders on the phase")
        check(not re.search(r"if \(!data\.configured\) \{", src),
              f"{name} does not treat 'not configured yet' as 'offline'")
        m = re.search(r'phase === "loading" \? "([^"]+)"', src)
        check(bool(m), f"{name} has a distinct label while loading")
        if m:
            check("OFFLINE" not in m.group(1),
                  f"{name}'s loading label does not say OFFLINE "
                  f"(it says {m.group(1)!r})")


def test_a_shrinking_window_cannot_strand_a_panel():
    """Row 18.6. The clamp exists; this keeps it existing."""
    src = _read("App.jsx")
    check(bool(src), "App.jsx exists")
    check("const clampPos" in src, "positions are clamped to the viewport")
    check(re.search(r'addEventListener\("resize"', src) is not None,
          "and re-clamped when the window resizes, so a shrink cannot strand a "
          "widget where no pointer can reach it")
    check("clampPos(initialPos)" in src,
          "a restored position from storage is clamped too - a saved layout from "
          "a larger screen is the other way a panel arrives off-screen")


if __name__ == "__main__":
    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)),
                   key=lambda nf: nf[1].__code__.co_firstlineno)
    for name, fn in tests:
        try:
            fn()
        except Exception:
            _fails.append(name)
            print(f"FAIL  {name} raised")
            traceback.print_exc()
    print(f"\n{_checks - len(_fails)}/{_checks} passed.")
    sys.exit(1 if _fails else 0)
