"""url_safety.py — a URL the model chose is not automatically a WEB url.

This is the twin of `modules/shell_safety.py`, and it exists for the same reason
that one does: **the rule has to live at the sink, not at one of the doors.**

The history is the argument. Finding 10 (2026-08-15) added a URL precondition to
`web_browse`, `open_link` and `os_macro` in `agent_tools.py`. That is the AGENT
LAYER — and both actions are also in the ONE-SHOT catalogue
(`action_router.py`, `brain.py`), which the ordinary conversational path uses and
which never touches a tool-layer precondition. So the hole finding 10 closed was
still open through the door nobody had walked through yet:

    file:///F:/…/jarvis-backend/.env    Playwright renders it and `_web_browse`
                                        hands the CONTENTS back as the action
                                        result, which the model then reads. A
                                        read of any file on the disk, around
                                        `workspace_read` and around the
                                        protected-file list — which guards
                                        writing and deleting, not reading.
    http://127.0.0.1:8000/api/…         the desk API is unauthenticated ON
                                        PURPOSE, because only local processes
                                        reach it. A model steered into fetching
                                        localhost is the case that reasoning
                                        excluded — and `/api/agent/confirm`
                                        approves governance prompts.

Same root cause as findings 1, 2 and 6: **governance approves an action by TYPE
and never inspects the ARGUMENT.** And since §6.8 the argument can come from a
web page, an indexed document or an MCP reply — text the model did not write.

WHY THE HOST IS PARSED AND NOT PATTERN-MATCHED
----------------------------------------------
The first version of this matched host PREFIXES — `"127."`, `"localhost"`,
`"0.0.0.0"`. That recognises exactly one way of writing the loopback address, and
six others walked past it (finding 14): decimal `2130706433`, hex `0x7f000001`,
octal `0177.0.0.1`, bare `0`, `::ffff:127.0.0.1`, expanded `::1`. It also refused
`https://10.com/`, a real registered domain.

**A blocklist over the SPELLINGS of a thing can never be complete**, in exactly
the way F-09's blocklist over mutation VERBS could not be. So the host goes
through `inet_aton` — the same parser the socket layer uses when it CONNECTS —
and is then classified by `ipaddress`. Read the address the way the connection
will read it, not the way it is written.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

#: Host NAMES that mean this machine. Names only — every numeric spelling is
#: handled by `host_address`, which is the half a prefix list gets wrong.
LOCAL_HOST_NAMES = ("localhost",)
LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")

#: How long a DNS lookup may take before the guard gives up on it. Short on
#: purpose: this runs ahead of every browse, and a stalled resolver must not
#: stall the caller.
DNS_TIMEOUT_S = 2.0

LOCAL_ADDRESS_REFUSAL = (
    "That address is on this machine or this network, not the web. "
    "I don't fetch those — ask me directly for what you need instead.")


def host_address(host: str):
    """The IP a host literally IS, in ANY spelling. None if it is a name."""
    import ipaddress
    import socket

    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        return ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
    except (OSError, ValueError):
        return None


def is_local_address(ip) -> bool:
    """Does this address point at this machine, this network, or nowhere public?"""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:          # ::ffff:127.0.0.1 is loopback; ipaddress says no
        ip = mapped
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local
                or ip.is_reserved or ip.is_unspecified or ip.is_multicast)


def resolves_locally(host: str) -> bool:
    """Does this NAME resolve to a local address?

    The literal check cannot see `localtest.me`, `127.0.0.1.nip.io` or any other
    public name whose A record is 127.0.0.1 — a free redirect back to the desk's
    unauthenticated API. So the name is resolved once, in a worker thread under a
    hard timeout.

    A lookup that fails or times out returns False, and that is not fail-open: a
    name this machine cannot resolve is a name the fetch cannot reach either.
    """
    import concurrent.futures
    import ipaddress
    import socket

    def lookup():
        return socket.getaddrinfo(host, None)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            infos = pool.submit(lookup).result(timeout=DNS_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — timeout, NXDOMAIN, no network
        return False
    for info in infos:
        try:
            if is_local_address(ipaddress.ip_address(info[4][0])):
                return True
        except (ValueError, IndexError):
            continue
    return False


def url_problem(raw: Any) -> str | None:
    """Refuse anything that is not a public http(s) URL. None means fine."""
    text = str(raw or "").strip()
    if not text:
        return "A full URL is required, including https://."
    try:
        parsed = urllib.parse.urlparse(text)
        # `open_link` documents that https:// may be left off, so a bare domain
        # is legitimate input. A MISSING scheme becomes https; a WRONG one
        # (file:, javascript:, C:) still has to answer for itself below.
        if not parsed.scheme:
            parsed = urllib.parse.urlparse("https://" + text)
    except Exception:  # noqa: BLE001
        return f"'{text[:60]}' is not a URL I can parse."
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        return (f"I only open http and https addresses, and that one is "
                f"'{scheme or 'no scheme'}'. If you need a file from the disk, "
                "use `workspace_read` — it is the tool that is allowed to.")
    try:
        host = (parsed.hostname or "").lower()
    except ValueError:
        # urlparse defers malformed-IPv6 errors to .hostname
        return f"'{text[:60]}' is not a URL I can parse."
    if not host:
        return "That URL has no host."

    address = host_address(host)
    if address is not None:
        # A literal address, in whatever spelling. Classified, not pattern-matched.
        return LOCAL_ADDRESS_REFUSAL if is_local_address(address) else None

    # A name. The obvious ones by name, then what it actually resolves to.
    if host in LOCAL_HOST_NAMES or host.endswith(LOCAL_HOST_SUFFIXES):
        return LOCAL_ADDRESS_REFUSAL
    if resolves_locally(host):
        return LOCAL_ADDRESS_REFUSAL
    return None


def refuse_or_none(raw: Any, *, what: str = "that address") -> str | None:
    """`url_problem`, phrased as something the ENGINE can return to the user.

    The action-engine layer answers in JARVIS's voice and its return value is
    spoken, so a refusal here has to read as a sentence rather than as an
    instruction aimed at a model.
    """
    problem = url_problem(raw)
    if problem is None:
        return None
    return f"I won't open {what}, Sir. {problem}"
