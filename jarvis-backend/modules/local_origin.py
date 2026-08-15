"""local_origin.py — a POST from the HUD, not from a page the owner happens to have open.

Review finding R7, 2026-08-16.

The desk API binds `127.0.0.1` and is deliberately unauthenticated, on the
reasoning that only local processes can reach it. A browser IS a local process,
and any page in it can issue a cross-origin POST.

CORS does not stop that, and this is the part that is easy to get wrong: the
middleware decides whether JavaScript may **read the response**, not whether the
handler **runs**. For a request with no custom headers and no JSON body — a
"simple request" — no preflight is issued at all, so the handler executes and
only the reply is withheld. A page that does not care about the reply gets its
effect for free:

    fetch("http://127.0.0.1:8000/api/listen", {method: "POST", mode: "no-cors"})

`/api/listen` opens the desk microphone for a full command window, and whatever
is spoken in the room is then executed at ADMIN tier. Offline it starts the
biometric boot instead — camera and mic on, unprompted.

The JSON-body routes (`/api/backdoor`, `/api/tasks`, `/api/ui_state`) are
genuinely safe from this already: a non-JSON content type fails body validation,
and a JSON content type is not a simple request, so it forces a preflight that
the four-origin list rejects. It is specifically the routes that take NO BODY
that are exposed.

WHAT THIS CHECKS, AND WHY IT IS THE HEADERS AND NOT THE IP
----------------------------------------------------------
The peer address is already 127.0.0.1 — the browser is local. So the question is
not *where from* but *who asked*: a page, or the HUD.

`Sec-Fetch-Site` is set by the browser itself and cannot be overridden by page
script; `cross-site` and `same-site` both mean a page made this call. `Origin` is
likewise browser-controlled on a cross-origin request. A non-browser caller (the
Electron shell, curl, a harness) sends neither, and is allowed — this is not
authentication, it is a check that the call did not originate in a web page.

`Host` is validated too, because neither the loopback bind nor an origin list
survives DNS rebinding: a name that resolves to 127.0.0.1 makes a page's request
*same-origin* by the browser's own reckoning, and every header check above then
passes honestly. The host header is what that attack cannot forge.
"""

from __future__ import annotations

import os

#: Hosts the desk API answers to. A request naming anything else is a rebind.
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}

#: Origins allowed to make a state-changing call. Mirrors main.py's CORS list —
#: the HUD in a dev browser, and nothing else.
_ALLOWED_ORIGINS = {
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:3000", "http://127.0.0.1:3000",
    "http://localhost:8000", "http://127.0.0.1:8000",   # the packaged /hud mount
}


def _host_ok(raw_host: str) -> bool:
    host = (raw_host or "").strip().lower()
    if not host:
        return True                      # no Host at all: not a browser
    if host.startswith("[") and "]" in host:          # [::1]:8000
        host = host[: host.index("]") + 1]
    elif ":" in host:
        host = host.rsplit(":", 1)[0]
    return host in _ALLOWED_HOSTS


def cross_site_problem(request) -> str | None:
    """Refuse a state-changing call that a web page made. None means fine.

    Disable with `JARVIS_ALLOW_CROSS_SITE_POST=1` if some future local tool needs
    it — default OFF, and unset/empty/unrecognised all read as OFF, the house
    rule set by the `contact_events` ruling.
    """
    if os.getenv("JARVIS_ALLOW_CROSS_SITE_POST", "0").strip().lower() in (
            "1", "true", "yes", "on"):
        return None

    headers = getattr(request, "headers", {}) or {}

    if not _host_ok(headers.get("host", "")):
        return (f"Refused: this request names host "
                f"'{headers.get('host', '')[:60]}', which is not this machine.")

    # Browser-set, and page script cannot forge it.
    site = (headers.get("sec-fetch-site") or "").strip().lower()
    if site in ("cross-site", "same-site"):
        return ("Refused: that request came from a web page rather than from the "
                "HUD. This endpoint changes what the machine is doing, so it only "
                "accepts calls from J.A.R.V.I.S. itself.")

    origin = (headers.get("origin") or "").strip()
    if origin and origin not in _ALLOWED_ORIGINS:
        return (f"Refused: '{origin[:60]}' is not an origin I accept commands "
                "from.")
    return None
