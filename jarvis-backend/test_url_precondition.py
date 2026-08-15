"""
test_url_precondition.py — a URL the model chose is not automatically a web URL
==============================================================================

Pre-Electron review, 2026-08-15, finding 10. `web_browse`, `open_link` and
`os_macro`'s optional `url` all took a bare string with no scheme check.

Two things a bare string is not:

    file:///…/jarvis-backend/.env
        Playwright renders it and hands the CONTENTS back as page text. That
        reads any file on the disk while going around `workspace_read` — and
        around the protected-file list, which only guards writing and deleting.

    http://127.0.0.1:8000/api/…
        The desk's own API is unauthenticated ON PURPOSE, on the reasoning that
        only local processes can reach it. A model steered into fetching
        localhost is precisely the case that reasoning excluded.

Same root cause as findings 1, 2 and 6: governance approves `web_browse` by
TYPE and never inspects the argument — and since §6.8 the argument can come
from a page the model was told to go and read.

Enforced as a rule-3 precondition, which the authorizer checks BEFORE anything
runs, rather than as a prompt instruction.
"""

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS  {label}")
    else:
        _failed += 1
        print(f"FAIL  {label}")


from modules import agent_tools as at  # noqa: E402


def test_local_file_urls_are_refused():
    for bad in (
        "file:///C:/Users/KINGSHUK/jarvis-backend/.env",
        "file:///etc/passwd",
        "FILE:///C:/secret.txt",
        r"C:\Users\KINGSHUK\jarvis-backend\jarvis_key.dpapi",
    ):
        problem = at._url_problem(bad)
        check(problem is not None, f"refused: {bad[:44]}")
        check("workspace_read" in (problem or "") or "http" in (problem or ""),
              f"...and points at the tool that IS allowed to read files")


def test_other_dangerous_schemes_are_refused():
    for bad in ("javascript:alert(1)", "data:text/html,<script>x</script>",
                "about:config", "chrome://settings", "ftp://x.example.com"):
        check(at._url_problem(bad) is not None, f"refused: {bad[:40]}")


def test_this_machine_and_this_network_are_refused():
    # The desk API is unauthenticated because only local processes reach it.
    for bad in ("http://127.0.0.1:8000/api/telemetry",
                "http://localhost:8000/api/agent/pending",
                "http://[::1]:8000/", "http://0.0.0.0:8000/",
                "http://10.0.0.5/", "http://192.168.1.1/",
                "http://169.254.169.254/latest/meta-data/",
                "http://172.16.0.1/", "http://172.31.255.254/"):
        check(at._url_problem(bad) is not None, f"refused: {bad[:44]}")


def test_every_spelling_of_this_machine_is_refused():
    """2026-08-16, finding 14 — the first fix only recognised ONE spelling.

    The original guard matched host prefixes: `startswith("127.")`, `"localhost"`,
    `"0.0.0.0"`. Six other ways of writing the same address walked straight past
    it, including the one that matters most — `http://2130706433:8000/` is
    127.0.0.1 in decimal, and 8000 is the desk's unauthenticated API, the one
    that approves governance prompts.

    A blocklist over the SPELLINGS of an address can never be complete, in
    exactly the way F-09's blocklist over mutation VERBS could not be. So the
    host is now parsed by the same code the socket layer uses to connect, and
    classified.
    """
    for bad, why in (
        ("http://2130706433:8000/api/agent/confirm", "decimal 127.0.0.1"),
        ("http://0x7f000001:8000/", "hex 127.0.0.1"),
        ("http://0177.0.0.1:8000/", "octal 127.0.0.1"),
        ("http://127.1:8000/", "short-form 127.0.0.1"),
        ("http://0:8000/", "0 = unspecified, connects locally"),
        ("http://[::ffff:127.0.0.1]:8000/", "v4-mapped v6 loopback"),
        ("http://[0:0:0:0:0:0:0:1]:8000/", "expanded ::1"),
        ("http://3232235777/", "decimal 192.168.1.1"),
        ("http://2852039166/", "decimal 169.254.169.254 (cloud metadata)"),
    ):
        check(at._url_problem(bad) is not None, f"refused ({why}): {bad[:40]}")


def test_a_name_that_resolves_to_this_machine_is_refused():
    """The literal check cannot see a PUBLIC name whose A record is 127.0.0.1.

    `localtest.me` and the `nip.io` family exist precisely to do that, and they
    are a free redirect back to the desk API. Resolution runs once, in a worker
    thread under a hard timeout, so a stalled resolver cannot stall the loop.

    Written to tolerate no network: if the name does not resolve here, there is
    nothing to assert — a name this machine cannot resolve is one the fetch
    cannot reach either.
    """
    import socket
    for host in ("localtest.me", "127.0.0.1.nip.io"):
        try:
            socket.getaddrinfo(host, None)
        except OSError:
            print(f"SKIP  {host} does not resolve here — nothing to prove")
            continue
        check(at._url_problem(f"http://{host}:8000/api/telemetry") is not None,
              f"refused: {host} resolves to loopback")


def test_the_name_check_does_not_punish_ordinary_hostnames():
    # `.local`/`.internal` are refused by name; a real public domain that merely
    # starts with digits must not be. "10.com" is a registered domain, and the
    # old prefix list refused it.
    check(at._url_problem("https://10.com/") is None,
          "10.com is a domain, not 10.0.0.0/8")
    check(at._url_problem("http://printer.local/") is not None,
          ".local is this network")
    check(at._url_problem("http://db.internal/") is not None,
          ".internal is this network")


def test_a_public_address_in_the_172_range_is_still_allowed():
    # 172.16/12 is private; 172.15 and 172.32 are not. A prefix match on "172."
    # alone would wrongly refuse real public addresses.
    for good in ("http://172.15.0.1/", "http://172.32.0.1/"):
        check(at._url_problem(good) is None, f"allowed, correctly: {good}")


def test_ordinary_web_addresses_still_work():
    for good in ("https://example.com",
                 "http://example.com/path?q=1#frag",
                 "https://news.ycombinator.com/item?id=1",
                 "https://en.wikipedia.org/wiki/Kolkata"):
        check(at._url_problem(good) is None, f"allowed: {good[:46]}")


def test_a_bare_domain_is_allowed_because_open_link_documents_it():
    # open_link's schema says "https:// is added if you leave it off", so
    # refusing a bare domain would break documented behaviour.
    for good in ("example.com", "www.bbc.co.uk", "news.ycombinator.com/news"):
        check(at._url_problem(good) is None, f"bare domain allowed: {good}")


def test_empty_is_refused_with_a_useful_sentence():
    problem = at._url_problem("")
    check(problem is not None, "an empty url is refused")
    check("required" in problem.lower(), "...and says what is needed")
    check(at._url_problem(None) is not None, "None is refused too")


def test_the_macro_url_is_optional_but_checked_when_present():
    # os_macro takes `macro` alone; url is an override.
    check(at._macro_url_precondition({"macro": "deep_work"}) is None,
          "os_macro with no url is fine")
    check(at._macro_url_precondition({"macro": "deep_work", "url": ""}) is None,
          "os_macro with an empty url is fine")
    check(at._macro_url_precondition(
        {"macro": "deep_work", "url": "file:///C:/secret.txt"}) is not None,
        "os_macro with a file:// override is refused")


def test_the_precondition_is_actually_attached_to_all_three_tools():
    """Unwired, this is dead code — the exact failure §6.8 already had once."""
    import ast
    src = (HERE / "modules" / "agent_tools.py").read_text(encoding="utf-8",
                                                          errors="replace")
    tree = ast.parse(src)
    attached = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "register"
                and node.args
                and isinstance(node.args[0], ast.Constant)):
            continue
        name = node.args[0].value
        for kw in node.keywords:
            if kw.arg == "precondition":
                attached[name] = ast.unparse(kw.value)
    for tool in ("web_browse", "open_link", "os_macro"):
        check(tool in attached, f"{tool} has a precondition attached")
        check("url" in attached.get(tool, ""),
              f"...and it is the URL one ({attached.get(tool)})")


# DISCOVERED, not hand-listed. This file used to carry an explicit `TESTS = [...]`
# and it cost exactly what that shape always costs: three tests were added for
# finding 14, the harness reported "43 passed, 0 failed" — the identical number
# as before — and none of the three had run. A green count that does not move is
# the most convincing possible way to not notice.
#
# `run_harnesses.py` had this same bug at the suite level and was converted to
# discovery for the same reason. Definition order is preserved so the output
# still reads top-to-bottom with the file.
def test_the_engine_refuses_too_not_only_the_agent_layer():
    """Finding 17, 2026-08-16 — the precondition guarded ONE of two doors.

    `web_browse` and `open_link` are in the ONE-SHOT catalogue as well
    (`action_router.py`, `brain.py`), and that path — the ordinary
    conversational one — never reaches a tool-layer precondition. So the hole
    finding 10 closed was still open through the door nobody had walked through.

    `_web_browse` is the bad one: Playwright RENDERS what it is given and the
    page text comes back as the action result, which the model then reads.

    Driven against the real methods with a WebAgent that records whether it was
    ever reached — asserting on the refusal string alone would pass even if the
    fetch had already happened.
    """
    import asyncio
    import action_engine as ae

    engine = ae.ActionEngine.__new__(ae.ActionEngine)   # no __init__: no hardware

    reached = []

    class SpyWeb:
        async def browse(self, url):
            reached.append(url)
            return "PAGE CONTENTS"

    engine.web_agent = SpyWeb()

    for bad in ("file:///F:/work/JARVIS-Project/jarvis-backend/.env",
                "http://127.0.0.1:8000/api/agent/confirm",
                "http://2130706433:8000/api/agent/confirm"):
        out = asyncio.run(engine._web_browse(bad))
        check("won't open" in out, f"engine refused web_browse: {bad[:40]}")
    check(reached == [], f"the browser must never have been driven; got {reached}")

    out = asyncio.run(engine._web_browse("https://example.com"))
    check(out == "PAGE CONTENTS" and reached == ["https://example.com"],
          "an ordinary page still goes through")


def test_open_link_refuses_at_the_engine():
    import action_engine as ae

    engine = ae.ActionEngine.__new__(ae.ActionEngine)
    opened = []
    original = ae.webbrowser.open
    ae.webbrowser.open = lambda u: opened.append(u)
    try:
        for bad in ("file:///C:/secret.txt", "http://localhost:8000/api/telemetry",
                    "javascript:alert(1)"):
            out = engine._open_link(bad)
            check("won't open" in out, f"engine refused open_link: {bad[:34]}")
        check(opened == [], f"nothing must have reached the browser; got {opened}")
        engine._open_link("example.com")
        check(opened == ["https://example.com"],
              "a bare domain still opens, with https added as documented")
    finally:
        ae.webbrowser.open = original


def test_the_macro_url_override_is_refused_at_the_macro():
    """`os_macro`'s "deep_work:<url>" override reaches `start "" <url>`, which
    hands any scheme to whatever Windows has registered for it."""
    from modules import macro_agent as ma

    agent = ma.MacroAgent.__new__(ma.MacroAgent)
    agent._dev_url = ma.MacroAgent.DEFAULT_DEV_URL
    agent.MACRO_REGISTRY = {"deep_work": lambda: "ran"}

    out = agent.run("deep_work:file:///c:/secret.txt")
    check("won't open" in out, "macro refused a file:// override")
    check(agent._dev_url == ma.MacroAgent.DEFAULT_DEV_URL,
          "...and the override never stuck")
    check(agent.run("deep_work") == "ran", "an ordinary macro still runs")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 60)
    print("URL precondition harness (pre-Electron review)")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
