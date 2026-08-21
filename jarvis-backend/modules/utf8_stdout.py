"""Make a log character unable to abort an operation.

`import modules.utf8_stdout` — importing it IS the call. There is nothing to
invoke, because the whole point is that it must have run before the first print,
and an `import` at the top of a file is the only thing that reliably has.

WHY
---
On Windows, `sys.stdout.encoding` is `cp1252` whenever stdout is not a UTF-8
console: redirected to a file, piped, captured by the Electron shell, or run
under a service. A single `print` containing an em dash, an arrow or an emoji
then raises `UnicodeEncodeError` — and it raises INSIDE whatever operation was
logging, so the operation dies. Not the log line: the operation.

main.py has opened with this block since the Electron work, and its comment says
exactly that. What it does not do is cover the other entry points, because it
only hardens the process when main.py is the process. Measured 2026-08-22:
**48 backend files print non-ASCII and had no guard**, and five of them are
their own entry point — `cloud_gateway.py` (19 such lines), `recorder.py`,
`run_phase1_regression.py`, `cursor_overlay.py`, `modules/web_agent.py`. The
rest are imported by main.py and inherit its hardening, which is why this went
unnoticed: it is invisible for as long as main.py is the only way in.

Two of the exposed lines are on paths the live gate is stuck on. `brain.py`
prints an em dash on the `close_app guard` path and an arrow on
`Code-file guard -> workspace_write`, so under `run_evals.py` or the worker a log
glyph sat between an instruction and a file write.

`errors="replace"` as well as UTF-8: if a stream cannot be reconfigured to UTF-8
at all, a substituted question mark is still better than a dead command.
"""

import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # A stream that cannot be reconfigured (already closed, replaced by a
        # non-TextIO object, some captured pytest/uvicorn wrapper) is not worth
        # raising over — this module exists to PREVENT a log line from being
        # fatal, so it must not become fatal itself.
        pass
