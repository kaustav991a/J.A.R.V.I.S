r"""
test_cursor_overlay.py — G5.3 overlay geometry/colour-key/deadman logic (no window)

Run: venv\Scripts\python.exe test_cursor_overlay.py

Covers the pure layer of cursor_overlay.py, extracted after a live incident
where the whole desktop went black and could not be dismissed (one fullscreen
near-black window + WS_EX_NOACTIVATE so Alt+F4 couldn't target it + a 1s
re-lift; it only died when the parent backend was killed). The three guards that
replaced it are all rooted in logic pinned here:

  1. small per-element windows -> `box_place` (centre on the cursor, clamp to the
     virtual desktop, and keep drawing ON the cursor once clamped) and
     `geometry_str` (Tk reads a bare '-' offset as "from the far edge", so a
     left-hand monitor needs '+-1920').
  2. verified colour-key -> `colorref` byte order. A red/blue swap keys out the
     wrong colour and the window paints SOLID — the exact failure mode being
     defended against.
  3. deadman -> `deadman_expired`.

Plus the display logic itself (`halo_style`, `toast_for`, `pulse_radius`), which
used to be unreachable inside Tk methods. Imports tkinter but never builds a
window, so this runs headless.
"""

import cursor_overlay as co

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


# --- colorref (guard #2) -------------------------------------------------- #

def test_colorref_byte_order():
    # COLORREF is 0x00BBGGRR — the REVERSE of #RRGGBB. Getting this backwards
    # keys out the wrong colour and the window renders opaque.
    check(co.colorref("#010203") == 0x00030201,
          f"#010203 -> 0x00030201, got {co.colorref('#010203'):#08x}")
    check(co.colorref("#ff0000") == 0x000000ff, "pure red -> low byte")
    check(co.colorref("#0000ff") == 0x00ff0000, "pure blue -> high byte")
    check(co.colorref("010203") == 0x00030201, "leading # optional")
    check(co.colorref(co.BG) == 0x00030201, "the module's own BG round-trips")


def test_colorref_rejects_bad_input():
    for bad in ("#fff", "", "#0102030", "nope"):
        try:
            co.colorref(bad)
            check(False, f"{bad!r} should raise")
        except ValueError:
            check(True, f"{bad!r} raises ValueError")
        except Exception as e:  # noqa: BLE001
            check(False, f"{bad!r} raised {type(e).__name__}, want ValueError")


# --- geometry_str (guard #1) ---------------------------------------------- #

def test_geometry_str_always_signs_offsets():
    check(co.geometry_str(72, 72, 100, 50) == "72x72+100+50",
          f"positive offsets, got {co.geometry_str(72, 72, 100, 50)}")
    # '-1920' would mean "1920 from the RIGHT edge" to Tk; '+-1920' means x=-1920.
    check(co.geometry_str(72, 72, -1920, 0) == "72x72+-1920+0",
          f"negative x keeps its '+', got {co.geometry_str(72, 72, -1920, 0)}")
    check(co.geometry_str(72, 72, 0, -100) == "72x72+0+-100",
          f"negative y keeps its '+', got {co.geometry_str(72, 72, 0, -100)}")


# --- box_place (guard #1) ------------------------------------------------- #

VS = (0, 0, 1920, 1080)     # a plain single 1080p desktop


def test_box_centred_on_cursor_when_clear_of_edges():
    wx, wy, lx, ly = co.box_place(500, 400, 72, *VS)
    check((wx, wy) == (464, 364), f"window centred, got {(wx, wy)}")
    check((lx, ly) == (36, 36), f"draw point at box centre, got {(lx, ly)}")
    check(wx + lx == 500 and wy + ly == 400, "window origin + local == cursor")


def test_box_clamped_at_top_left_still_draws_on_the_cursor():
    wx, wy, lx, ly = co.box_place(4, 2, 72, *VS)
    check((wx, wy) == (0, 0), f"clamped to desktop origin, got {(wx, wy)}")
    # THE point of clamping-without-drifting: the ring stays on the cursor.
    check((lx, ly) == (4, 2), f"local point follows the cursor, got {(lx, ly)}")
    check(wx + lx == 4 and wy + ly == 2, "window origin + local == cursor")


def test_box_clamped_at_bottom_right():
    wx, wy, lx, ly = co.box_place(1919, 1079, 72, *VS)
    check((wx, wy) == (1848, 1008), f"flush to the far edge, got {(wx, wy)}")
    check(wx + lx == 1919 and wy + ly == 1079, "window origin + local == cursor")
    check(wx + 72 <= 1920 and wy + 72 <= 1080, "box never overhangs the desktop")


def test_box_on_a_negative_origin_desktop():
    # second monitor to the LEFT: virtual desktop starts at x=-1920
    vs = (-1920, 0, 3840, 1080)
    wx, wy, lx, ly = co.box_place(-1900, 500, 72, *vs)
    check(wx == -1920, f"clamps to the negative origin, not 0, got {wx}")
    check(wx + lx == -1900, "window origin + local == cursor on the left monitor")
    wx2, _, lx2, _ = co.box_place(-1000, 500, 72, *vs)
    check((wx2, lx2) == (-1036, 36), f"unclamped negative x centres, got {(wx2, lx2)}")


def test_pulse_box_fits_a_full_expansion():
    # the ripple grows to DOT_R + 2*RING_R; the window must contain it centred
    r, _ = co.pulse_radius(co.PULSE_S)
    check(co.PULSE_BOX // 2 >= r, f"half-box {co.PULSE_BOX // 2} >= max radius {r}")
    check(co.HALO_BOX // 2 >= co.RING_R + 5, "halo box holds the ring at max stroke")


# --- halo_style ----------------------------------------------------------- #

def _active(**kw):
    f = {"engaged": True, "state": "active", "locked": False, "suspended": False,
         "clutch": False, "pose": "palm"}
    f.update(kw)
    return f


def test_halo_hidden_unless_actively_engaged():
    check(co.halo_style(_active(engaged=False)) is None, "not engaged -> hidden")
    check(co.halo_style(_active(state="idle")) is None, "not active -> hidden")
    check(co.halo_style(_active(locked=True)) is None, "locked -> hidden")
    check(co.halo_style(_active(suspended=True)) is None,
          "JARVIS driving -> hidden (its cursor, not the hand's)")
    check(co.halo_style({}) is None, "empty frame -> hidden")


def test_halo_style_per_pose():
    color, width, dash = co.halo_style(_active(pose="palm"))
    check((color, dash) == (co.CYAN, None), f"palm = solid cyan, got {(color, dash)}")
    color, width, dash = co.halo_style(_active(pose="fist"))
    check((color, width, dash) == (co.AMBER, 5, None),
          f"fist = thick amber (grab), got {(color, width, dash)}")
    color, _, dash = co.halo_style(_active(pose="two_finger"))
    check((color, dash) == (co.CYAN, (2, 3)), f"two_finger = dashed cyan, got {dash}")
    color, _, dash = co.halo_style(_active(clutch=True, pose="fist"))
    check((color, dash) == (co.DIM, (4, 4)),
          f"clutch outranks pose (frozen), got {(color, dash)}")
    color, _, _ = co.halo_style(_active(pose="unknown_pose"))
    check(color == co.CYAN, "unknown pose falls back to move-cyan")


# --- toast_for ------------------------------------------------------------ #

def _f(**kw):
    f = {"engaged": False, "suspended": False, "denied": False, "locked": False}
    f.update(kw)
    return f


def test_no_toast_without_a_transition():
    check(co.toast_for(None, _f(engaged=True)) is None,
          "first frame never toasts (no previous state to differ from)")
    check(co.toast_for(_f(engaged=True), _f(engaged=True)) is None,
          "steady state doesn't re-toast")
    check(co.toast_for(_f(), _f()) is None, "idle -> idle silent")


def test_toast_transitions():
    t = co.toast_for(_f(), _f(engaged=True))
    check(t == ("HAND READY", co.CYAN), f"engage, got {t}")
    t = co.toast_for(_f(engaged=True), _f(engaged=False))
    check(t == ("CONTROL OFF", co.DIM), f"disengage, got {t}")
    t = co.toast_for(_f(engaged=True), _f(engaged=True, suspended=True))
    check(t == ("JARVIS DRIVING", co.AMBER), f"arbiter took the cursor, got {t}")
    t = co.toast_for(_f(engaged=True, suspended=True), _f(engaged=True))
    check(t == ("YOU HAVE CONTROL", co.CYAN), f"arbiter released, got {t}")
    t = co.toast_for(_f(), _f(denied=True))
    check(t == ("UNAUTHORIZED", co.RED), f"stranger denied, got {t}")


def test_toast_priority_and_suppression():
    # denied wins over a simultaneous engage edge — security beats affordance
    t = co.toast_for(_f(), _f(engaged=True, denied=True))
    check(t == ("UNAUTHORIZED", co.RED), f"denied outranks engage, got {t}")
    check(co.toast_for(_f(engaged=True), _f(locked=True)) is None,
          "locked stays silent — the lock screen speaks for itself")
    # arbiter released but the hand let go too -> report the hand, not the arbiter
    t = co.toast_for(_f(engaged=True, suspended=True), _f(suspended=False))
    check(t == ("CONTROL OFF", co.DIM),
          f"release with no hand engaged says CONTROL OFF, not 'YOU HAVE CONTROL', got {t}")


# --- deadman (guard #3) --------------------------------------------------- #

def test_deadman():
    check(not co.deadman_expired(100.0, 110.0, 20.0), "10s of quiet is fine")
    check(co.deadman_expired(100.0, 120.0, 20.0), "expires exactly at the timeout")
    check(co.deadman_expired(100.0, 999.0, 20.0), "long silence expires")
    check(not co.deadman_expired(100.0, 999.0, 0.0), "timeout 0 disables the guard")
    check(not co.deadman_expired(100.0, 999.0, -1.0), "negative timeout disables it")
    # the daemon re-sends every HUD_HEARTBEAT_S=2.0s, so the shipped default must
    # tolerate several missed beats or the overlay would flap
    check(co.DEADMAN_S >= 10.0, f"default deadman {co.DEADMAN_S}s >= 5 heartbeats")


# --- pulse_radius --------------------------------------------------------- #

def test_pulse_expands_and_thins():
    r0, w0 = co.pulse_radius(0.0)
    r1, w1 = co.pulse_radius(co.PULSE_S)
    check(r0 == co.DOT_R, f"starts at the cursor dot, got {r0}")
    check(r1 == co.DOT_R + co.RING_R * 2.0, f"ends at full expansion, got {r1}")
    check(w0 > w1 and w1 >= 1, f"stroke thins but never vanishes, got {w0}->{w1}")
    mid, _ = co.pulse_radius(co.PULSE_S / 2)
    check(r0 < mid < r1, f"monotonic through the middle, got {mid}")


def test_pulse_clamps_out_of_range():
    r, w = co.pulse_radius(99.0)
    check(r == co.DOT_R + co.RING_R * 2.0, "past the end clamps to full radius")
    check(w >= 1, "width floor holds past the end")
    r, _ = co.pulse_radius(-5.0)
    check(r == co.DOT_R, "negative elapsed clamps to the start")
    r, _ = co.pulse_radius(1.0, duration=0.0)
    check(r == co.DOT_R, "zero duration doesn't divide by zero")


TESTS = [
    test_colorref_byte_order,
    test_colorref_rejects_bad_input,
    test_geometry_str_always_signs_offsets,
    test_box_centred_on_cursor_when_clear_of_edges,
    test_box_clamped_at_top_left_still_draws_on_the_cursor,
    test_box_clamped_at_bottom_right,
    test_box_on_a_negative_origin_desktop,
    test_pulse_box_fits_a_full_expansion,
    test_halo_hidden_unless_actively_engaged,
    test_halo_style_per_pose,
    test_no_toast_without_a_transition,
    test_toast_transitions,
    test_toast_priority_and_suppression,
    test_deadman,
    test_pulse_expands_and_thins,
    test_pulse_clamps_out_of_range,
]


def main():
    print("=" * 60)
    print("cursor_overlay geometry / colour-key / deadman harness")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
