"""Harness: an expired Google token must not hang the desk, or answer emptily.

WHY THIS EXISTS
---------------
Goal 1 of the tracker — **"He never claims what he did not do"** — and a 🔴 found
on the desk on 2026-08-29 while trying to run the A11 rows that goal is measured
by. Both halves come from one event, and they are worth separating.

*Half one: the desk stopped being a desk.* The stored token came back
`invalid_grant: Token has been expired or revoked`. One routine
`GET /api/health/summary` reached `is_health_available()`, which called
`get_google_credentials()`, which called `flow.run_local_server()` — a blocking
`socketserver.handle_request()` waiting for a browser redirect **on the event
loop, inside the request handler**. Every route died with it: `/docs` timed out
at ninety seconds, with `Application startup complete` in the log and the process
idle at 0% CPU. A `py-spy dump` is what found it; nothing in the log said a word.

*Half two, and the one this goal is about: the sentence.* With no credentials,
the calendar said **"Calendar integration is not configured yet"** and Gmail said
**"temporarily unavailable"**. Both are false in the direction that matters. The
first reads as a feature never set up, so nothing gets re-authorised and every
day after is answered the same way; the second is a claim about the future that
an expired refresh token does not support. And one step further down that road is
the failure the gate marks 🛑 STOP: an empty read reported as an empty day.

**"Your calendar is clear today" and "I could not read your calendar" are
different sentences**, and only one of them is true when a token has expired.

WHAT THIS PINS
--------------
Offline and deterministic. No Google, no network, no browser: the flow object is
replaced with one that fails the test by being called at all.

  * **no request path can reach the browser flow** — `interactive` defaults to
    False, and the three agents call it with that default;
  * the interactive flow still exists, is reachable from `tools/google_reauth.py`,
    and carries a timeout, because "waits forever" is a hang wherever it runs;
  * an unauthorised calendar, inbox and vitals each say so, name the cause, and
    name the fix;
  * **the honest empty is left alone**: "no health data recorded yet today" still
    means the service answered and had nothing, which is a different fact from
    nobody answering.

Run standalone: `python test_google_auth_door.py`
"""

import ast
import io
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from modules import google_auth as ga  # noqa: E402

_fails: list = []
_checks = 0


def check(ok: bool, why: str) -> None:
    global _checks
    _checks += 1
    if ok:
        print(f"PASS  {why}")
    else:
        print(f"FAIL  {why}")
        _fails.append(why)


class _ExplodingFlow:
    """Stands in for `InstalledAppFlow`. Being called at all is the failure."""

    called = False

    @classmethod
    def from_client_secrets_file(cls, *_a, **_k):
        cls.called = True
        raise AssertionError("the browser OAuth flow was reached")


def _dead_token(monkey_valid=False):
    """A credential object shaped like one whose refresh token has been revoked."""

    class _Creds:
        valid = monkey_valid
        expired = True
        refresh_token = None
        scopes: list = []

        def refresh(self, _request):
            raise AssertionError("nothing should try to refresh a revoked token")

    return _Creds()


def _with_dead_credentials(fn):
    """Run `fn` with the module believing it holds a revoked token."""
    real_present = ga._CREDENTIALS_PRESENT
    real_file = ga._TOKEN_FILE
    real_creds_cls = ga.Credentials
    real_flow = ga.InstalledAppFlow
    ga._CREDENTIALS_PRESENT = True

    class _FakeCredentials:
        @staticmethod
        def from_authorized_user_file(*_a, **_k):
            return _dead_token()

    class _Path:
        @staticmethod
        def is_file():
            return True

        def __str__(self):
            return "token.json"

    ga._TOKEN_FILE = _Path()
    ga.Credentials = _FakeCredentials
    ga.InstalledAppFlow = _ExplodingFlow
    _ExplodingFlow.called = False
    try:
        return fn()
    finally:
        ga._CREDENTIALS_PRESENT = real_present
        ga._TOKEN_FILE = real_file
        ga.Credentials = real_creds_cls
        ga.InstalledAppFlow = real_flow


# ── half one: the door that hung the desk ───────────────────────────────────

def test_a_revoked_token_returns_none_instead_of_opening_a_browser():
    """The whole 🔴 in one line: this used to block the event loop forever."""
    got = _with_dead_credentials(lambda: ga.get_google_credentials())
    check(got is None, f"a revoked token yields None ({got!r})")
    check(_ExplodingFlow.called is False,
          "...and NOTHING opened a browser flow to get there")
    check(ga.needs_reauth() is True,
          "...and the module records WHY, so callers can say it")


def test_the_interactive_flow_still_exists_and_is_opt_in():
    reached = _with_dead_credentials(
        lambda: [ga.get_google_credentials(interactive=True), _ExplodingFlow.called][1])
    check(reached is True,
          "interactive=True does reach the flow — re-authorising is still possible")


def test_no_caller_in_the_backend_asks_for_the_interactive_flow():
    """The default is what protects the request path, so the default must be what
    every agent uses. Asserted over the source rather than assumed: this is a
    one-word change away from being a hang again."""
    offenders = []
    for path in sorted(HERE.glob("**/*.py")):
        if "venv" in path.parts or path.name == Path(__file__).name:
            continue
        if path.name == "google_reauth.py":
            continue          # the one place a human is present by construction
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # the definition itself names the parameter; what matters is CALLERS
        calls = [ln for ln in src.splitlines()
                 if "get_google_credentials(interactive" in ln
                 and not ln.lstrip().startswith("def ")]
        if calls and path.name != "google_auth.py":
            offenders.append(str(path.relative_to(HERE)))
    check(not offenders,
          f"no backend caller passes interactive= except the re-auth tool "
          f"({offenders})")


def test_the_interactive_flow_cannot_wait_forever():
    src = (HERE / "modules" / "google_auth.py").read_text(encoding="utf-8")
    check("timeout_seconds" in src,
          "the browser flow carries a timeout — 'waits forever' is a hang "
          "wherever it runs")
    check("except TypeError" in src,
          "...passed defensively, so an older google-auth-oauthlib still "
          "re-authorises rather than raising")


def test_the_reauth_tool_exists_and_is_the_only_interactive_door():
    tool = HERE / "tools" / "google_reauth.py"
    check(tool.is_file(), "tools/google_reauth.py exists")
    src = tool.read_text(encoding="utf-8")
    check("interactive=True" in src, "...and it is the one that asks for the flow")
    tree = ast.parse(src)
    check(any(isinstance(n, ast.FunctionDef) and n.name == "main" for n in tree.body),
          "...and it is a script with a main(), not a module with a side effect")


# ── half two: the sentence, which is what goal 1 is about ───────────────────

def _say(module_name: str, call):
    """Call an agent's user-facing string with Google unauthorised."""
    return _with_dead_credentials(call)


def test_an_unauthorised_calendar_says_so_rather_than_clear():
    from modules import calendar_agent
    calendar_agent._service_singleton = None
    said = _say("calendar", lambda: calendar_agent.CalendarAgent().get_today_schedule())
    check("authorisation has expired" in said,
          f"the calendar names the cause: {said!r}")
    check("google_reauth" in said, "...and names the fix")
    check("clear today" not in said.lower(),
          "...and never reports an empty DAY for an empty READ")
    check("not configured yet" not in said,
          "...and does not read as a feature that was never set up")


def test_an_unauthorised_inbox_says_so_rather_than_spotless():
    from modules import gmail_agent
    agent = gmail_agent.GmailAgent() if hasattr(gmail_agent, "GmailAgent") else None
    if agent is None:
        check(False, "GmailAgent could not be constructed")
        return
    said = _say("gmail", lambda: agent.get_unread_emails())
    check("authorisation has expired" in said,
          f"the inbox names the cause: {said!r}")
    check("google_reauth" in said,
          "...and names the fix, which its own honest-but-vague sentence did not")
    check("spotless" not in said,
          "...and never reports an empty INBOX for an empty read")


def test_unauthorised_vitals_say_so_rather_than_nothing_recorded():
    from modules import health_agent
    health_agent._service_singleton = None
    said = _say("health", lambda: health_agent.HealthAgent().get_summary_string())
    check("authorisation has expired" in said,
          f"the vitals name the cause: {said!r}")
    check("No health data has been recorded" not in said,
          "...and are not reported as a day with no steps in it")


def test_the_honest_empty_is_still_allowed_to_be_empty():
    """The distinction runs both ways: a service that answered and had nothing
    must still say so plainly, or the fix would have replaced one wrong sentence
    with another."""
    src = (HERE / "modules" / "health_agent.py").read_text(encoding="utf-8")
    check("No health data has been recorded yet today" in src,
          "the genuine empty-day sentence survives")
    check("needs_reauth()" in src,
          "...and the unauthorised sentence sits above it rather than replacing it")


def test_one_sentence_in_one_place():
    """Three agents, one wording. Root cause #4 in this project is the same fix
    living in several files and drifting apart."""
    said = ga.unauthorised_reply("your calendar")
    for word in ("authorisation has expired", "google_reauth", "gap in what I can see"):
        check(word in said, f"the shared sentence carries {word!r}")
    for module in ("calendar_agent", "gmail_agent", "health_agent"):
        src = (HERE / "modules" / f"{module}.py").read_text(encoding="utf-8")
        check("unauthorised_reply(" in src,
              f"{module} uses the shared sentence rather than its own")


if __name__ == "__main__":
    import traceback

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
