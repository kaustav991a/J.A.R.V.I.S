r"""
test_boot_preflight.py — G5.7 startup config preflight (no real env)

Run: venv\Scripts\python.exe test_boot_preflight.py

Feeds synthetic env dicts + a fake exists() through boot_preflight.preflight and
asserts the required/recommended/file classification and the `ok` flag.
"""

from modules import boot_preflight as bp

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def _all_env():
    env = {names[0]: "x" for names in bp.REQUIRED_ANY.values()}
    env.update({name: "x" for name in bp.RECOMMENDED})
    return env


def test_all_present_ok():
    rep = bp.preflight(env=_all_env(), exists=lambda p: True)
    check(rep["ok"], "everything present -> ok")
    check(not rep["missing_required"] and not rep["missing_recommended"]
          and not rep["files_missing"], "no gaps reported when all present")


def test_missing_required_not_ok():
    rep = bp.preflight(env={}, exists=lambda p: True)
    check(not rep["ok"], "no LLM key -> not ok")
    check(any("LLM" in cap for cap, _ in rep["missing_required"]),
          "missing LLM group is reported")


def test_required_any_alternate_key_satisfies():
    rep = bp.preflight(env={"GROQ_API_KEY": "x"}, exists=lambda p: True)
    check(not rep["missing_required"], "either GROQ_API_KEYS or GROQ_API_KEY satisfies the group")


def test_whitespace_key_counts_as_missing():
    rep = bp.preflight(env={"GROQ_API_KEYS": "   \t"}, exists=lambda p: True)
    check(not rep["ok"], "a whitespace-only key is treated as missing")


def test_missing_critical_file_not_ok():
    target = next(iter(bp.CRITICAL_FILES))
    rep = bp.preflight(env=_all_env(), exists=lambda p: p != target)
    check(not rep["ok"], "a missing critical model file -> not ok")
    check(any(target == p for p, _ in rep["files_missing"]), "the missing file is listed")


def test_recommended_missing_still_ok():
    env = {names[0]: "x" for names in bp.REQUIRED_ANY.values()}  # required only
    rep = bp.preflight(env=env, exists=lambda p: True)
    check(rep["ok"], "missing recommended does not flip ok to False")
    check(len(rep["missing_recommended"]) == len(bp.RECOMMENDED),
          "all recommended gaps are still listed")


def test_recommended_files_are_soft():
    env = _all_env()
    rf = next(iter(bp.RECOMMENDED_FILES))
    rep = bp.preflight(env=env, exists=lambda p: p not in bp.RECOMMENDED_FILES)
    check(rep["ok"], "a missing recommended file does not flip ok")
    check(any(rf == p for p, _ in rep["files_recommended_missing"]),
          "missing recommended file is listed under the soft bucket")


def test_format_report_is_text():
    rep = bp.preflight(env={}, exists=lambda p: False)
    s = bp.format_report(rep)
    check(isinstance(s, str) and "REQUIRED" in s, "format_report returns a report string")
    ok_rep = bp.preflight(env=_all_env(), exists=lambda p: True)
    check("✅" in bp.format_report(ok_rep), "all-present report shows the ok marker")


TESTS = [test_all_present_ok, test_missing_required_not_ok,
         test_required_any_alternate_key_satisfies, test_whitespace_key_counts_as_missing,
         test_missing_critical_file_not_ok, test_recommended_missing_still_ok,
         test_recommended_files_are_soft, test_format_report_is_text]


def main():
    print("=" * 60)
    print("boot_preflight harness")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
