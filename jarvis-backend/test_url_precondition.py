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


TESTS = [
    test_local_file_urls_are_refused,
    test_other_dangerous_schemes_are_refused,
    test_this_machine_and_this_network_are_refused,
    test_a_public_address_in_the_172_range_is_still_allowed,
    test_ordinary_web_addresses_still_work,
    test_a_bare_domain_is_allowed_because_open_link_documents_it,
    test_empty_is_refused_with_a_useful_sentence,
    test_the_macro_url_is_optional_but_checked_when_present,
    test_the_precondition_is_actually_attached_to_all_three_tools,
]


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
