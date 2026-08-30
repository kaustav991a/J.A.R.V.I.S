"""Harness: one backend origin for the whole HUD, including the small views.

WHY THIS EXISTS
---------------
Row `18.7` asks that every widget load from the `VITE_API_BASE` host. Driving the
rows found two that did not:

    src/NotchView.jsx:16    new WebSocket('ws://127.0.0.1:8000/ws')
    src/SidecarView.jsx:24  new WebSocket('ws://127.0.0.1:8000/ws')

`src/api.js` exists precisely to stop this, and says so in its own first line:

    Central backend origin. One place instead of hard-coded localhost:8000 /
    127.0.0.1:8000 scattered across the HUD + widgets. Override at build/run
    time with VITE_API_BASE (e.g. "192.168.1.5:8000") to drive JARVIS from
    another host.

Eight of the twelve call sites used it. Two did not, and they are the two views
meant for a **second screen** - which is the one situation where the override is
not optional. Set `VITE_API_BASE=192.168.1.5:8000`, open the sidecar on a tablet,
and it dials `127.0.0.1` - the tablet's own machine, where nothing is listening.
The failure is silent: a view that renders and never receives a frame.

Root cause #4 for the fifth time in two days - a rule applied to most of its call
sites and missed in the ones that needed it most. A harness rather than a fix,
because the fix is one line and the next component added is the risk.

WHAT THIS PINS
--------------
No source file outside `api.js` may name the backend host literally. The check is
on the SOURCE, not the bundle: a bundle is regenerated, and by the time a bad URL
reaches one it has already shipped.
"""

from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "jarvis-frontend" / "src"

_checks = 0
_fails: list[str] = []


def check(cond: bool, label: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _fails.append(label)
        print(f"FAIL  {label}")


# A literal backend origin: a scheme, a loopback-or-numeric host, and the port.
# `api.js` is the one file allowed to write it, being the definition.
_LITERAL = re.compile(r"""(?:ws|http)s?://(?:127\.0\.0\.1|localhost)(?::\d+)?""")
_ALLOWED = {"api.js"}


def _sources() -> list[Path]:
    return sorted(p for p in SRC.rglob("*")
                  if p.suffix in (".js", ".jsx") and p.name not in _ALLOWED)


def test_the_source_tree_is_there_to_be_checked():
    check(SRC.is_dir(), f"the frontend source exists at {SRC}")
    files = _sources()
    check(len(files) > 10,
          f"and there are files to check ({len(files)}) - an empty sweep "
          f"passes vacuously, which is the way this kind of test lies")


def test_no_file_names_the_backend_host_literally():
    """The whole rule, in one assertion."""
    offenders = []
    for p in _sources():
        text = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _LITERAL.search(line) and "VITE_API_BASE" not in line:
                offenders.append(f"{p.name}:{i}: {line.strip()[:70]}")
    check(not offenders,
          "no source file outside api.js names the backend host literally"
          + (f" - found {len(offenders)}: {offenders[:3]}" if offenders else ""))


def test_the_two_views_that_broke_it_use_the_shared_base():
    """Named explicitly, because these are the ones a second screen depends on."""
    for name in ("NotchView.jsx", "SidecarView.jsx"):
        p = SRC / name
        if not p.exists():
            check(False, f"{name} exists")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        check("WS_BASE" in text, f"{name} imports the shared WS base")
        check("ws://127.0.0.1" not in text,
              f"{name} no longer hardcodes the loopback socket")


def test_api_js_still_defines_the_override():
    """If the definition stops honouring VITE_API_BASE, every caller is wrong at
    once - and each of them would still pass the check above."""
    p = SRC / "api.js"
    check(p.exists(), "api.js exists")
    if not p.exists():
        return
    text = p.read_text(encoding="utf-8", errors="replace")
    check("VITE_API_BASE" in text, "api.js reads VITE_API_BASE")
    check("export const WS_BASE" in text, "api.js exports WS_BASE")
    check("export const API_BASE" in text, "api.js exports API_BASE")


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
