"""
test_mail_target.py — a pipe in the subject sent half of it as the body
=======================================================================

Pre-Electron review, 2026-08-15.

`gmail_send`'s `build_target` encoded three fields into one delimited string:

    f"{to} | {subject} | {body}"

and `_send_email` parses that with `split("|", 2)`. The maxsplit means a pipe in
the BODY is harmless — everything after the second delimiter is body, pipes and
all. A pipe in the SUBJECT is not:

    to@x.com | Re: Q3 | final | Here is the report
                 ^ subject ends here, silently

    subject = "Re: Q3"
    body    = "final | Here is the report"

The email sends. Nobody is told. "Re: Q3 | final" is an ordinary subject line,
and a model composing one from a thread or a web page it was just asked to read
can produce any character at all.

Fixed by not encoding structure in a character the content is allowed to
contain: `build_target` emits JSON, which `_send_email` already accepts (it
checks for a leading "{" before falling back to the delimiter). The alternative
— a precondition banning "|" from subjects — would refuse a legitimate subject
to protect a parser, which is the wrong way round.
"""

import json
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


def _roundtrip(to, subject, body):
    """build_target → the parse `_send_email` performs → the three fields."""
    target = at._mail_target({"to": to, "subject": subject, "body": body})
    stripped = str(target).strip()
    if stripped.startswith("{"):
        data = json.loads(stripped)
        return (str(data.get("to", "")).strip(),
                str(data.get("subject", "")).strip(),
                str(data.get("body", "")).strip())
    parts = [p.strip() for p in stripped.split("|", 2)]
    return (parts + ["", "", ""])[:3]


def test_an_ordinary_email_survives_the_roundtrip():
    got = _roundtrip("k@example.com", "Invoice", "Please see attached.")
    check(got == ("k@example.com", "Invoice", "Please see attached."),
          f"plain fields round-trip intact, got {got}")


def test_a_pipe_in_the_subject_no_longer_moves_it_into_the_body():
    to, subject, body = _roundtrip(
        "k@example.com", "Re: Q3 | final", "Here is the report.")
    check(subject == "Re: Q3 | final",
          f"the whole subject survives, got {subject!r}")
    check(body == "Here is the report.",
          f"the body is untouched, got {body!r}")
    check(to == "k@example.com", "and the recipient is intact")


def test_a_pipe_in_the_recipient_cannot_shift_the_fields():
    to, subject, body = _roundtrip("k|x@example.com", "Hello", "Body here.")
    check(subject == "Hello", f"subject unaffected by a pipe in `to`, got {subject!r}")
    check(body == "Body here.", f"body unaffected, got {body!r}")


def test_pipes_in_the_body_still_work_as_they_always_did():
    # This case was ALREADY safe under maxsplit=2 — it must stay safe.
    _, subject, body = _roundtrip("k@example.com", "Report",
                                  "a | b | c | d")
    check(subject == "Report", "subject clean")
    check(body == "a | b | c | d", f"every pipe in the body survives, got {body!r}")


def test_newlines_and_quotes_survive():
    # A model writing a real email produces both, and JSON must carry them.
    body = 'Line one.\nLine two with "quotes" and a \\ backslash.'
    _, _, got = _roundtrip("k@example.com", "S", body)
    check(got == body.strip(), f"multiline/quoted body survives, got {got!r}")


def test_the_target_is_a_string_so_nothing_downstream_changes():
    # `_send_email` also accepts a dict, but the payload travels through
    # governance and the confirm read-back as a target; keeping it a str means
    # this fix cannot change how any of that renders.
    target = at._mail_target({"to": "a@b.c", "subject": "s", "body": "b"})
    check(isinstance(target, str), "build_target still returns a string")
    check(target.strip().startswith("{"),
          "...that _send_email routes to its JSON branch")


def test_missing_fields_do_not_raise():
    for args in ({}, {"to": "a@b.c"}, {"subject": "only"}, None):
        try:
            out = at._mail_target(args or {})
            check(isinstance(out, str), f"handled {args!r} without raising")
        except Exception as e:  # noqa: BLE001
            check(False, f"raised on {args!r}: {e}")


def test_gmail_send_is_wired_to_it():
    import ast
    src = (HERE / "modules" / "agent_tools.py").read_text(
        encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    wired = False
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "register"
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "gmail_send"):
            for kw in node.keywords:
                if kw.arg == "build_target" and ast.unparse(kw.value) == "_mail_target":
                    wired = True
    check(wired, "gmail_send uses _mail_target — unwired, this is dead code")
    check('f"{str(a.get(\'to\', \'\')).strip()} | "' not in src,
          "the old delimited builder is gone, not merely bypassed")


TESTS = [
    test_an_ordinary_email_survives_the_roundtrip,
    test_a_pipe_in_the_subject_no_longer_moves_it_into_the_body,
    test_a_pipe_in_the_recipient_cannot_shift_the_fields,
    test_pipes_in_the_body_still_work_as_they_always_did,
    test_newlines_and_quotes_survive,
    test_the_target_is_a_string_so_nothing_downstream_changes,
    test_missing_fields_do_not_raise,
    test_gmail_send_is_wired_to_it,
]


def main():
    print("=" * 60)
    print("mail target harness (pre-Electron review)")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
