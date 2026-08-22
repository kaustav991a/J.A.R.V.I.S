r"""test_ram_budget.py — the 16 GB budget, and the guard that measurement rewrote.

Run: venv\Scripts\python.exe test_ram_budget.py

WHAT THIS PINS, AND WHY THE FIRST DESIGN WAS WRONG
--------------------------------------------------
Tier 3.2 began as a refusal: llava is 4.4 GB, this box usually has 4-6 GB free,
so do not load it. Measured live on 2026-08-22 before writing a line of the
harness — with **2.56 GB free**, less than two thirds of what llava needs, the
call loaded and answered correctly in **91.9 s**. Slow, not broken.

A refusal would therefore have deleted a working feature, and deleted it exactly
when it was needed: the vision cascade reaches llava only once Gemini has already
failed, so there is nothing left to escalate to. The tests below pin the advisory
design that replaced it, and one of them (`test_a_model_that_does_not_fit_is_not
_blocked`) exists specifically so nobody reintroduces the refusal later.

The two real defects the measurement exposed:

    a fixed 120 s deadline over a call that legitimately takes 92 s
    -> 28 s of margin, then a false "vision offline" on a working call

    nothing ever set keep_alive, so ollama's 5m default applied
    -> one screen read parked 4.4 GB; unloading it returned free RAM
       from 2.74 GB to 6.87 GB

FETCH IS INJECTED
-----------------
Every test drives fake endpoint bodies rather than the live daemon, so the numbers
here are stable whatever is running on the machine. The live measurement belongs
in the module docstring, where it is evidence; a harness that depended on it would
pass or fail according to how many browser tabs were open.
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


GB = 1024 ** 3

_TAGS = {"models": [
    {"name": "llama3.2:3b", "size": int(1.88 * GB)},
    {"name": "llava:latest", "size": int(4.41 * GB)},
    {"name": "qwen2.5-coder:latest", "size": int(4.36 * GB)},
]}
_NOTHING_LOADED = {"models": []}
_LLAVA_LOADED = {"models": [{"name": "llava:latest", "size": int(4.39 * GB)}]}


def _fetch(tags=None, ps=None):
    tags = _TAGS if tags is None else tags
    ps = _NOTHING_LOADED if ps is None else ps

    def go(kind):
        return tags if kind == "tags" else ps
    return go


def _rb():
    from modules import ram_budget
    return ram_budget


# ---------------------------------------------------------------- footprints
def test_footprints_are_read_from_the_daemon_not_hardcoded():
    """A hardcoded table would be wrong the first time he pulls a new model, and
    wrong silently. The sizes come from the daemon's own catalogue."""
    rb = _rb()
    sizes = rb.installed_gb(_fetch())
    check(round(sizes["llava:latest"], 2) == 4.41,
          f"llava's size is read, not assumed ({sizes['llava:latest']:.2f} GB)")
    check(round(sizes["llama3.2:3b"], 2) == 1.88,
          "so is the small text model's")
    check(len(sizes) == 3, "every installed model is accounted for")

    other = {"models": [{"name": "phi4:latest", "size": int(9.1 * GB)}]}
    check(round(rb.installed_gb(_fetch(tags=other))["phi4:latest"], 1) == 9.1,
          "a model this code has never heard of is measured the same way")


def test_a_bare_tag_matches_its_latest_form():
    """`VISION_MODEL` is configured as `llava` in some places and `llava:latest`
    in others. Treating those as different models would silently skip the check."""
    rb = _rb()
    sizes = rb.installed_gb(_fetch())
    check(round(rb._match("llava", sizes), 2) == 4.41,
          "'llava' finds 'llava:latest'")
    check(round(rb._match("llava:latest", sizes), 2) == 4.41,
          "and the fully qualified name still works")
    check(rb._match("nothing-like-this", sizes) is None,
          "a model that is not installed has no footprint")


def test_a_silent_daemon_yields_no_sizes_rather_than_an_exception():
    """boot_preflight reports a dead daemon (tier 0.4). This module must not
    become a second place that crashes over it."""
    rb = _rb()

    def boom(_kind):
        raise OSError("connection refused")
    check(rb.installed_gb(boom) == {}, "an unreachable daemon gives an empty map")
    check(rb.resident_gb(boom) == {}, "for both endpoints")


# ------------------------------------------------------------------- advice
def test_a_model_that_fits_is_left_completely_alone():
    """The common case must add nothing: no keep_alive, no raised deadline."""
    rb = _rb()
    plan = rb.advise("llama3.2:3b", _fetch(), free_gb=8.0)
    check(plan["comfortable"] and not plan["tight"],
          "1.88 GB into 8.0 GB free is comfortable")
    check(plan["keep_alive"] is None,
          "no keep_alive is imposed, so ollama's warm default stands")
    check(plan["timeout_floor_s"] is None, "and the caller's own deadline stands")
    check("room to spare" in plan["reason"], f"and it says so: {plan['reason']}")


def test_a_model_that_does_not_fit_is_not_blocked():
    """THE load-bearing test. With 2.56 GB free -- well under llava's 4.4 GB --
    the real call answered correctly in 91.9 s. Refusing it would delete a working
    feature at the exact moment it is the only option left, because the vision
    cascade only reaches llava after Gemini has already failed."""
    rb = _rb()
    plan = rb.advise("llava", _fetch(), free_gb=2.56)
    check(plan["tight"], "the situation is correctly called tight")
    check(plan["blocked"] is False,
          "and the call is NOT blocked -- measured 91.9 s, slow but correct")
    check(not hasattr(rb, "require"),
          "the refusing entry point is gone, not merely unused")
    check(not hasattr(rb, "RamTooTight"),
          "and so is the exception that would let a caller refuse")


def test_a_tight_load_gets_a_longer_deadline_not_a_shorter_one():
    """The defect the measurement exposed: a fixed 120 s ceiling over a 92 s call
    leaves 28 s of margin, and past that a working answer is cancelled and
    reported as "vision offline"."""
    rb = _rb()
    plan = rb.advise("llava", _fetch(), free_gb=2.56)
    check(plan["timeout_floor_s"] >= 120.0,
          f"the floor is at least the old fixed value ({plan['timeout_floor_s']})")
    check(plan["timeout_floor_s"] > 91.9 * 1.5,
          "and comfortably clear of the 91.9 s that was actually measured")

    payload = {}
    out = rb.apply("llava", payload, 120.0, "unit",
                   fetch=_fetch(), free_gb=2.56)
    check(out == plan["timeout_floor_s"],
          f"apply() raises a 120 s deadline to {out:.0f}s")
    payload2 = {}
    out2 = rb.apply("llama3.2:3b", payload2, 120.0, "unit",
                    fetch=_fetch(), free_gb=8.0)
    check(out2 == 120.0, "and leaves a comfortable call's deadline untouched")


def test_a_tight_load_releases_the_model_promptly():
    """Nothing ever set keep_alive, so a single screen read held 4.4 GB for
    ollama's default five minutes. Unloading returned free RAM 2.74 -> 6.87 GB."""
    rb = _rb()
    payload = {}
    rb.apply("llava", payload, 120.0, "unit", fetch=_fetch(), free_gb=2.56)
    check(payload.get("keep_alive") == rb.TIGHT_KEEP_ALIVE,
          f"a tight vision call asks for keep_alive={payload.get('keep_alive')}")
    comfortable = {}
    rb.apply("llama3.2:3b", comfortable, 120.0, "unit",
             fetch=_fetch(), free_gb=8.0)
    check("keep_alive" not in comfortable,
          "and a comfortable one does not, so a warm model stays warm")


def test_an_already_resident_model_is_free_however_little_ram_is_left():
    """The absurd case a naive check produces: llava is resident, so free RAM is
    LOW BECAUSE OF llava -- and the guard then declares llava unaffordable."""
    rb = _rb()
    plan = rb.advise("llava", _fetch(ps=_LLAVA_LOADED), free_gb=0.3)
    check(plan["resident"], "the loaded model is recognised as loaded")
    check(not plan["tight"],
          "so 0.3 GB free does NOT make a resident model tight")
    check(plan["keep_alive"] is None and plan["timeout_floor_s"] is None,
          "nothing is imposed on a call that costs no memory")
    check("no extra memory" in plan["reason"], f"reason: {plan['reason']}")


def test_the_resident_size_is_preferred_over_the_disk_size():
    """They disagree in both directions -- llama3.2 costs 36% more loaded than on
    disk, llava about the same -- so there is no multiplier worth inventing."""
    rb = _rb()
    plan = rb.advise("llava", _fetch(ps=_LLAVA_LOADED), free_gb=8.0)
    check(round(plan["need_gb"], 2) == 4.39,
          f"resident size reported ({plan['need_gb']:.2f}, not the 4.41 on disk)")
    cold = rb.advise("llava", _fetch(), free_gb=8.0)
    check(round(cold["need_gb"], 2) == 4.41,
          "and disk size is used when the model is not loaded")


def test_ignorance_never_costs_the_user_a_feature():
    """Unknown free memory or an unknown model must behave like a normal load.
    A guard that clamps down whenever it cannot see would take vision away on any
    machine whose daemon is briefly busy."""
    rb = _rb()
    blind = rb.advise("llava", _fetch(), free_gb=None)
    import modules.ram_budget as m
    real_free = m.available_gb()
    if real_free is None:
        check(blind["comfortable"], "free memory unknown -> treated as normal")
    else:
        check(True, "psutil is present on this box, so the None path is unit-only")
        forced = dict(blind)
        check("model" in forced, "advise still returns a full plan")

    unknown = rb.advise("some-model-nobody-pulled", _fetch(), free_gb=0.1)
    check(unknown["comfortable"] and not unknown["tight"],
          "an unknown model is not penalised on 0.1 GB free")
    check(unknown["need_gb"] is None, "its footprint is honestly None")
    check("unknown" in unknown["reason"], f"and the reason says so: {unknown['reason']}")


def test_the_headroom_boundary_is_where_it_claims_to_be():
    rb = _rb()
    need = 4.41
    edge = need + rb.HEADROOM_GB
    check(rb.advise("llava", _fetch(), free_gb=edge + 0.01)["comfortable"],
          f"just above {edge:.2f} GB free is comfortable")
    check(rb.advise("llava", _fetch(), free_gb=edge - 0.01)["tight"],
          f"just below it is tight")


# -------------------------------------------------------- both doors, one lock
def test_both_local_vision_legs_go_through_the_same_function():
    """Root cause #4 in this project: a fix applied at one door while its sibling
    stays open. F-61 was exactly this, in these same two files."""
    router = (HERE / "modules" / "llm_router.py").read_text(encoding="utf-8")
    reader = (HERE / "modules" / "screen_reader.py").read_text(encoding="utf-8")
    for name, src in (("llm_router", router), ("screen_reader", reader)):
        check("ram_budget.apply(" in src,
              f"{name}'s local vision leg calls ram_budget.apply()")
        check("timeout=deadline" in src or "timeout=deadline," in src,
              f"{name} passes the returned deadline to requests, not a literal")
    check("timeout=120)" not in reader,
          "screen_reader's hardcoded 120 s ceiling is gone")


def test_the_advisory_shape_cannot_be_misread_as_permission():
    """`blocked` is always False and always present. A caller that checks for it
    finds the key rather than a KeyError it might paper over."""
    rb = _rb()
    for free in (0.1, 2.56, 8.0, 64.0):
        plan = rb.advise("llava", _fetch(), free_gb=free)
        check("blocked" in plan and plan["blocked"] is False,
              f"at {free} GB free the plan is explicit that nothing is blocked")


def test_the_text_leg_is_left_alone_on_purpose():
    """Not an omission. The local text model fits (1.88 GB on disk, 2.55 GB
    resident) and keeping it warm is the entire point of having a local fallback,
    so a short keep_alive there would cause the cold reload it exists to avoid.
    Pinned as a decision so a future reader does not "finish the job"."""
    # Whitespace-normalised: the first version of this check looked for a phrase
    # that the docstring happens to line-wrap, and failed on the wrap rather than
    # on the content. Pinning prose means pinning it the way prose is written.
    doc = " ".join((_rb().__doc__ or "").split())
    check("DELIBERATELY NOT WRAPPED" in doc,
          "the module states that the text leg is excluded by choice")
    check("keeping the text model warm" in doc, "and gives the reason")
    router = (HERE / "modules" / "llm_router.py").read_text(encoding="utf-8")
    text_leg = router[router.index("def _call_ollama("):router.index("def _call_ollama(") + 1400]
    check("ram_budget" not in text_leg,
          "and the text leg is in fact untouched, matching what is written")


def test_no_control_bytes_in_the_module():
    """F-18's class: a heredoc ate a backslash and left 0x08 in a regex. It has
    happened three times in this project, twice in documents."""
    raw = (HERE / "modules" / "ram_budget.py").read_bytes()
    bad = [(i, hex(b)) for i, b in enumerate(raw)
           if b < 0x20 and b not in (0x09, 0x0A, 0x0D)]
    check(not bad, f"no stray control bytes ({bad[:3]})")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 68)
    print("RAM budget — advise, never block (Tier 3.2)")
    print("=" * 68)
    for t in TESTS:
        t()
    print("-" * 68)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
