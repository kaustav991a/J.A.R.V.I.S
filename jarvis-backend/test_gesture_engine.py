"""Phase G2 — harness for the gesture state machine + pointer backend.

No camera, no mediapipe, no ctypes side effects: synthetic 21-landmark hands
are fed through GestureEngine and intents are asserted (plan §4 G2). Same
no-hardware discipline as the other harnesses.
"""

from modules.gesture_camera import decorate_url
from modules.gesture_engine import GestureConfig, GestureEngine, OneEuroFilter
from modules.gesture_pointer import (
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_RIGHTDOWN,
    MOUSEEVENTF_RIGHTUP,
    MOUSEEVENTF_WHEEL,
    PointerBackend,
    to_absolute,
)

# ------------------------------------------------------------------ #
# synthetic hand builder
# ------------------------------------------------------------------ #

WRIST_POS = (0.5, 0.6)
FINGER_X = {"index": 0.44, "middle": 0.50, "ring": 0.53, "pinky": 0.56}
PIP_IDX = {"index": 6, "middle": 10, "ring": 14, "pinky": 18}
TIP_IDX = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}
HAND_SIZE = 0.15  # wrist -> middle MCP


def make_hand(index_tip=(0.42, 0.32), ext=("index", "middle", "ring", "pinky"),
              pinch=None, facing=True):
    """21 (x, y, z) landmarks. pinch = ("left"|"right", normalised_distance)."""
    lm = [(WRIST_POS[0], WRIST_POS[1], 0.0)] * 21
    lm[9] = (0.5, WRIST_POS[1] - HAND_SIZE, 0.0)          # middle MCP
    lm[5] = ((0.44, 0.47, 0.0) if facing else (0.56, 0.47, 0.0))   # index MCP
    lm[17] = ((0.56, 0.48, 0.0) if facing else (0.44, 0.48, 0.0))  # pinky MCP
    for name, x in FINGER_X.items():
        if name in ext:
            pip, tip = (x, 0.40), (x, 0.32)
            if name == "index":
                tip = index_tip
        else:
            pip, tip = (x, 0.48), (x, 0.55)
        lm[PIP_IDX[name]] = (pip[0], pip[1], 0.0)
        lm[TIP_IDX[name]] = (tip[0], tip[1], 0.0)
    if pinch is None:
        lm[4] = (0.70, 0.55, 0.0)  # thumb far from every fingertip
    else:
        which, d = pinch
        anchor = lm[TIP_IDX["index" if which == "left" else "middle"]]
        lm[4] = (anchor[0] + d * HAND_SIZE, anchor[1], 0.0)
    return lm


PALM = make_hand()                                   # open palm, facing camera
PALM_BACK = make_hand(facing=False)
FIST = make_hand(ext=())
POINT = make_hand(ext=("index",))                    # index-point (move pose)
SCROLL_POSE = make_hand(ext=("index", "middle"))


def point_at(x, y):
    return make_hand(index_tip=(x, y), ext=("index",))


def pinched(d=0.2, which="left", index_tip=(0.42, 0.32)):
    ext = ("index",) if which == "left" else ("index", "middle", "ring", "pinky")
    return make_hand(index_tip=index_tip, ext=ext, pinch=(which, d))


class Sim:
    """Feeds frames at a fixed fps with monotonically increasing time."""

    def __init__(self, engine=None, fps=30.0):
        self.e = engine or GestureEngine()
        self.dt = 1.0 / fps
        self.t = 0.0

    def feed(self, frame, n=1):
        out = []
        for _ in range(n):
            out += self.e.process(frame, self.t)
            self.t += self.dt
        return out


def engaged_sim():
    sim = Sim()
    assert sim.feed(PALM, 35) == [("engaged",)]
    sim.feed(POINT, 5)  # leave palm pose so the gate re-arms
    assert sim.e.engaged
    return sim


def kinds(intents):
    return [i[0] for i in intents]


# ------------------------------------------------------------------ #
# engage / disengage gate
# ------------------------------------------------------------------ #

def test_palm_hold_engages():
    assert Sim().feed(PALM, 35) == [("engaged",)]


def test_brief_palm_does_not_engage():
    assert Sim().feed(PALM, 20) == []


def test_waving_hand_never_engages():
    sim = Sim()
    out = []
    for _ in range(12):  # open/close chatter, none held for a second
        out += sim.feed(PALM, 5)
        out += sim.feed(FIST, 5)
    assert out == []


def test_palm_back_of_hand_does_not_engage():
    assert Sim().feed(PALM_BACK, 60) == []


def test_holding_palm_does_not_retoggle():
    assert Sim().feed(PALM, 90) == [("engaged",)]


def test_second_palm_hold_disengages():
    sim = engaged_sim()
    assert sim.feed(PALM, 35) == [("disengaged",)]
    assert sim.feed(point_at(0.30, 0.30), 10) == []  # gestures dead


# ------------------------------------------------------------------ #
# cursor movement
# ------------------------------------------------------------------ #

def test_no_move_before_engage():
    assert Sim().feed(POINT, 20) == []


def test_move_emits_margin_mapped_coords():
    sim = engaged_sim()
    out = sim.feed(point_at(0.30, 0.30), 40)  # hold until the filter settles
    moves = [i for i in out if i[0] == "move"]
    assert moves, "engaged index-point must move the cursor"
    assert abs(moves[-1][1] - (0.30 - 0.15) / 0.70) < 0.03
    assert abs(moves[-1][2] - (0.30 - 0.15) / 0.70) < 0.03
    out2 = sim.feed(point_at(0.60, 0.30), 40)
    moves2 = [i for i in out2 if i[0] == "move"]
    assert moves2 and moves2[-1][1] > moves[-1][1] + 0.3  # followed rightwards
    assert all(0.0 <= m[1] <= 1.0 and 0.0 <= m[2] <= 1.0 for m in moves + moves2)


def test_deadzone_holds_steady_cursor():
    sim = engaged_sim()
    sim.feed(point_at(0.30, 0.30), 40)  # settle on the target first
    out = []
    for i in range(10):  # sub-deadzone jitter
        out += sim.feed(point_at(0.30 + 0.0005 * (-1) ** i, 0.30))
    assert [i for i in out if i[0] == "move"] == []


# ------------------------------------------------------------------ #
# clicks, taps, chatter
# ------------------------------------------------------------------ #

def test_pinch_tap_clicks():
    sim = engaged_sim()
    out = sim.feed(POINT, 3)
    out += sim.feed(pinched(), 4)
    out += sim.feed(POINT, 8)
    assert kinds(out).count("click") == 1
    assert "drag_start" not in kinds(out)
    assert "double_click" not in kinds(out)


def test_double_tap_double_clicks():
    sim = engaged_sim()
    out = sim.feed(pinched(), 4)
    out += sim.feed(POINT, 4)
    out += sim.feed(pinched(), 4)
    out += sim.feed(POINT, 8)
    k = kinds(out)
    assert k.count("click") == 1 and k.count("double_click") == 1
    assert k.index("click") < k.index("double_click")


def test_single_frame_chatter_rejected():
    sim = engaged_sim()
    out = []
    for _ in range(10):  # one-frame pinch blips must be debounced away
        out += sim.feed(pinched(), 1)
        out += sim.feed(POINT, 3)
    assert [i for i in out if i[0] in ("click", "double_click", "drag_start")] == []


def test_cursor_frozen_during_pending_tap():
    sim = engaged_sim()
    sim.feed(point_at(0.30, 0.30), 40)                    # settle
    sim.feed(pinched(index_tip=(0.30, 0.30)), 2)          # debounce -> down
    out = []
    for i in range(5):  # finger dips while pinched — cursor must not budge
        out += sim.feed(pinched(index_tip=(0.30 + 0.02 * i, 0.32)))
    assert [i for i in out if i[0] == "move"] == []


def test_right_pinch_tap_right_clicks():
    sim = engaged_sim()
    out = sim.feed(pinched(which="right"), 4)
    out += sim.feed(PALM, 5)
    k = kinds(out)
    assert k.count("right_click") == 1
    assert "click" not in k


# ------------------------------------------------------------------ #
# drag & drop
# ------------------------------------------------------------------ #

def test_pinch_hold_drags_then_releases():
    sim = engaged_sim()
    out = sim.feed(pinched(), 12)  # held past tap_max -> drag
    for i in range(6):
        out += sim.feed(pinched(index_tip=(0.42 + 0.02 * i, 0.32)))
    out += sim.feed(POINT, 4)
    k = kinds(out)
    assert k.count("drag_start") == 1 and k.count("drag_end") == 1
    assert "click" not in k
    drag_moves = [i for i in out[k.index("drag_start"):] if i[0] == "move"]
    assert drag_moves, "cursor must keep moving while dragging"
    assert k.index("drag_start") < k.index("drag_end")


def test_tracking_loss_releases_drag_then_disengages():
    sim = engaged_sim()
    sim.feed(pinched(), 12)
    out = sim.feed(None, 75)  # hand leaves the frame mid-drag
    k = kinds(out)
    assert k.count("drag_end") == 1 and k.count("disengaged") == 1
    assert k.index("drag_end") < k.index("disengaged")
    assert not sim.e.engaged


# ------------------------------------------------------------------ #
# scroll
# ------------------------------------------------------------------ #

def test_two_finger_scroll_up():
    sim = engaged_sim()
    out = []
    for i in range(10):  # hand moves up -> positive ticks, no cursor moves
        out += sim.feed(make_hand(index_tip=(0.44, 0.32 - 0.012 * i),
                                  ext=("index", "middle")))
    ticks = [i[1] for i in out if i[0] == "scroll"]
    assert sum(ticks) >= 3
    assert all(t > 0 for t in ticks)
    assert [i for i in out if i[0] == "move"] == []


# ------------------------------------------------------------------ #
# One-Euro filter
# ------------------------------------------------------------------ #

def test_one_euro_converges_on_step():
    f = OneEuroFilter()
    f(0.0, 0.0)
    ys = [f(1.0, 0.033 * (i + 1)) for i in range(60)]
    assert ys == sorted(ys)      # monotonic approach, no overshoot
    assert ys[-1] > 0.95


def test_one_euro_attenuates_jitter():
    f = OneEuroFilter()
    xs = [0.5 + 0.05 * (-1) ** i for i in range(60)]
    ys = [f(x, 0.033 * i) for i, x in enumerate(xs)]
    tail = ys[10:]
    assert max(tail) - min(tail) < 0.05  # < half the input swing


# ------------------------------------------------------------------ #
# pointer backend (fake SendInput)
# ------------------------------------------------------------------ #

class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, flags, dx=0, dy=0, data=0):
        self.calls.append((flags, dx, dy, data))


def test_pointer_move_absolute_virtualdesk():
    rec = Recorder()
    PointerBackend(send_fn=rec).execute([("move", 0.5, 0.25)])
    flags, dx, dy, _ = rec.calls[0]
    assert flags == PointerBackend.MOVE_FLAGS
    assert abs(dx - 32768) <= 1 and abs(dy - 16384) <= 1


def test_pointer_click_and_drag_buttons():
    rec = Recorder()
    PointerBackend(send_fn=rec).execute(
        [("click",), ("drag_start",), ("drag_end",), ("right_click",)])
    flags = [c[0] for c in rec.calls]
    assert flags == [MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP,
                     MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP,
                     MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP]


def test_pointer_double_click_is_two_clicks():
    rec = Recorder()
    PointerBackend(send_fn=rec).execute([("double_click",)])
    assert [c[0] for c in rec.calls] == [MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP,
                                         MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP]


def test_pointer_scroll_signed_wheel():
    rec = Recorder()
    b = PointerBackend(send_fn=rec)
    b.execute([("scroll", 3), ("scroll", -2)])
    assert [(c[0], c[3]) for c in rec.calls] == [
        (MOUSEEVENTF_WHEEL, 360), (MOUSEEVENTF_WHEEL, -240)]


def test_pointer_ignores_engage_intents():
    rec = Recorder()
    PointerBackend(send_fn=rec).execute([("engaged",), ("disengaged",)])
    assert rec.calls == []


def test_to_absolute_clamps():
    assert to_absolute(-0.5) == 0
    assert to_absolute(2.0) == 65535


# ------------------------------------------------------------------ #
# camera URL helper (stream-lag carry-forward)
# ------------------------------------------------------------------ #

def test_ip_webcam_url_gets_resolution_query():
    assert decorate_url("http://192.168.0.105:8080/video") == \
        "http://192.168.0.105:8080/video?640x480"


def test_url_with_query_or_other_shape_untouched():
    assert decorate_url("http://h:8080/video?320x240") == "http://h:8080/video?320x240"
    assert decorate_url("http://h/stream.mjpg") == "http://h/stream.mjpg"
    assert decorate_url("http://h:8080/video", res=None) == "http://h:8080/video"


if __name__ == "__main__":  # plain-python runner, same no-pytest pattern as the other harnesses
    import sys
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
