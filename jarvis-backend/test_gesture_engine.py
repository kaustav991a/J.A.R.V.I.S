"""Phase G3 — harness for the gesture state machine + pointer backend.

No camera, no mediapipe, no ctypes side effects: synthetic 21-landmark hands
are fed through GestureEngine and intents are asserted. Covers the G3
natural-grab vocabulary: index-up start, back-of-hand stop, palm-centroid
move, pinch tap click (fires on pinch-land, never drags), fist grab drag,
two-finger scroll, thumb+middle right click.
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


def make_hand(ext=("index", "middle", "ring", "pinky"), pinch=None,
              facing=True, shift=(0.0, 0.0)):
    """21 (x, y, z) landmarks. pinch = ("left"|"right", normalised_distance).
    shift translates the whole hand (drives the palm-centroid cursor)."""
    lm = [(WRIST_POS[0], WRIST_POS[1], 0.0)] * 21
    lm[9] = (0.5, WRIST_POS[1] - HAND_SIZE, 0.0)          # middle MCP
    lm[5] = ((0.44, 0.47, 0.0) if facing else (0.56, 0.47, 0.0))   # index MCP
    lm[17] = ((0.56, 0.48, 0.0) if facing else (0.44, 0.48, 0.0))  # pinky MCP
    lm[13] = (0.53, 0.475, 0.0)                            # ring MCP
    for name, x in FINGER_X.items():
        if name in ext:
            pip, tip = (x, 0.40), (x, 0.32)
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
    if shift != (0.0, 0.0):
        lm = [(p[0] + shift[0], p[1] + shift[1], p[2]) for p in lm]
    return lm


PALM = make_hand()                                   # open palm, facing camera
PALM_BACK = make_hand(facing=False)                  # back of hand = stop sign
FIST = make_hand(ext=())                             # grab
INDEX_UP = make_hand(ext=("index",))                 # start trigger
SCROLL_POSE = make_hand(ext=("index", "middle"))


def palm_at(dx, dy=0.0):
    return make_hand(shift=(dx, dy))


def pinched(d=0.2, which="left"):
    """A pinch with the hand otherwise open: the pinching finger dips to the
    thumb, every other finger stays extended (that is what arms the click)."""
    ext = (("middle", "ring", "pinky") if which == "left"
           else ("index", "ring", "pinky"))
    return make_hand(ext=ext, pinch=(which, d))


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
    assert sim.feed(INDEX_UP, 35) == [("engaged",)]
    assert sim.e.engaged
    return sim


def kinds(intents):
    return [i[0] for i in intents]


# ------------------------------------------------------------------ #
# start / stop gates
# ------------------------------------------------------------------ #

def test_index_hold_starts_control():
    sim = Sim()
    assert sim.feed(INDEX_UP, 35) == [("engaged",)]
    assert sim.e.engaged


def test_brief_index_does_not_start():
    assert Sim().feed(INDEX_UP, 20) == []


def test_palm_or_fist_never_starts():
    sim = Sim()
    out = sim.feed(PALM, 60) + sim.feed(FIST, 60) + sim.feed(PALM_BACK, 60)
    assert out == []
    assert not sim.e.engaged


def test_waving_hand_never_starts():
    sim = Sim()
    out = []
    for _ in range(12):  # open/close chatter, nothing held for a second
        out += sim.feed(PALM, 5)
        out += sim.feed(FIST, 5)
    assert out == []


def test_back_hand_hold_stops_control():
    sim = engaged_sim()
    out = sim.feed(PALM_BACK, 55)
    assert out == [("disengaged",)]
    assert not sim.e.engaged
    assert sim.feed(pinched(), 6) == []  # gestures dead after stop


def test_brief_back_hand_does_not_stop():
    sim = engaged_sim()
    assert kinds(sim.feed(PALM_BACK, 30)).count("disengaged") == 0
    assert sim.e.engaged


def test_start_progress_reports_while_arming():
    sim = Sim()
    sim.feed(INDEX_UP, 15)
    assert 0.0 < sim.e.start_progress < 1.0


def test_back_palm_freezes_cursor_while_arming():
    sim = engaged_sim()
    sim.feed(PALM, 40)  # settle
    out = sim.feed(make_hand(facing=False, shift=(0.1, 0.0)), 20)
    assert [i for i in out if i[0] == "move"] == []


# ------------------------------------------------------------------ #
# cursor movement (palm centroid)
# ------------------------------------------------------------------ #

def test_no_move_or_click_before_start():
    sim = Sim()
    out = sim.feed(PALM, 20) + sim.feed(pinched(), 6) + sim.feed(FIST, 6)
    assert out == []


def test_palm_moves_cursor_margin_mapped():
    sim = engaged_sim()
    out = sim.feed(PALM, 40)  # hold until the filter settles
    moves = [i for i in out if i[0] == "move"]
    assert moves, "engaged open palm must move the cursor"
    assert abs(moves[-1][1] - 0.5107) < 0.03   # centroid 0.5075 margin-mapped
    out2 = sim.feed(palm_at(0.15), 40)
    moves2 = [i for i in out2 if i[0] == "move"]
    assert moves2 and moves2[-1][1] > moves[-1][1] + 0.15  # followed rightwards
    assert all(0.0 <= m[1] <= 1.0 and 0.0 <= m[2] <= 1.0 for m in moves + moves2)


def test_deadzone_holds_steady_cursor():
    sim = engaged_sim()
    sim.feed(PALM, 40)  # settle on the target first
    out = []
    for i in range(10):  # sub-deadzone jitter
        out += sim.feed(palm_at(0.0005 * (-1) ** i))
    assert [i for i in out if i[0] == "move"] == []


def test_index_only_does_not_move_cursor_when_active():
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(make_hand(ext=("index",), shift=(0.1, 0.0)), 20)
    assert [i for i in out if i[0] == "move"] == []


# ------------------------------------------------------------------ #
# clicks (pinch tap — click fires on pinch-land, never drags)
# ------------------------------------------------------------------ #

def test_pinch_tap_clicks_fast():
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(pinched(), 4)          # click on the down-confirm frame
    out += sim.feed(PALM, 8)
    k = kinds(out)
    assert k.count("click") == 1
    assert "drag_start" not in k and "double_click" not in k


def test_pinch_hold_stays_single_click_no_drag():
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(pinched(), 30)         # held a full second — G2 turned this
    out += sim.feed(PALM, 8)              # into a drag-select; G3 must not
    k = kinds(out)
    assert k.count("click") == 1
    assert "drag_start" not in k and "drag_end" not in k


def test_double_tap_same_spot_double_clicks():
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(pinched(), 4)
    out += sim.feed(PALM, 8)
    out += sim.feed(pinched(), 4)
    out += sim.feed(PALM, 8)
    k = kinds(out)
    assert k.count("click") == 1 and k.count("double_click") == 1
    assert k.index("click") < k.index("double_click")


def test_slow_second_tap_stays_single():
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(pinched(), 4)
    out += sim.feed(PALM, 35)             # > double_window_s apart
    out += sim.feed(pinched(), 4)
    out += sim.feed(PALM, 8)
    k = kinds(out)
    assert k.count("click") == 2 and "double_click" not in k


def test_two_clicks_far_apart_stay_single():
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(pinched(), 4)
    out += sim.feed(palm_at(0.15), 12)    # cursor travels between the taps
    out += sim.feed(make_hand(ext=("middle", "ring", "pinky"),
                              pinch=("left", 0.2), shift=(0.15, 0.0)), 4)
    out += sim.feed(palm_at(0.15), 8)
    k = kinds(out)
    assert k.count("click") == 2 and "double_click" not in k


def test_single_frame_chatter_rejected():
    sim = engaged_sim()
    out = []
    for _ in range(10):  # one-frame pinch blips must be debounced away
        out += sim.feed(pinched(), 1)
        out += sim.feed(PALM, 3)
    assert [i for i in out if i[0] in ("click", "double_click")] == []


def test_cursor_frozen_during_pinch():
    sim = engaged_sim()
    sim.feed(PALM, 40)                                    # settle
    out = []
    for i in range(6):  # hand wobbles while pinched — cursor must not budge
        out += sim.feed(make_hand(ext=("middle", "ring", "pinky"),
                                  pinch=("left", 0.2), shift=(0.02 * i, 0.01)))
    assert [i for i in out if i[0] == "move"] == []


def test_right_pinch_tap_right_clicks():
    sim = engaged_sim()
    out = sim.feed(pinched(which="right"), 4)
    out += sim.feed(PALM, 5)
    k = kinds(out)
    assert k.count("right_click") == 1
    assert "click" not in k


# ------------------------------------------------------------------ #
# grab (fist) — drag & drop, fully separate from click
# ------------------------------------------------------------------ #

def test_fist_grabs_drags_and_drops():
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(FIST, 6)               # close hand -> mouse down
    for i in range(6):
        out += sim.feed(make_hand(ext=(), shift=(0.02 * i, 0.0)))
    out += sim.feed(PALM, 6)              # open hand -> drop
    k = kinds(out)
    assert k.count("drag_start") == 1 and k.count("drag_end") == 1
    assert "click" not in k and "double_click" not in k
    drag_moves = [i for i in out[k.index("drag_start"):] if i[0] == "move"]
    assert drag_moves, "cursor must keep moving while dragging"
    assert k.index("drag_start") < k.index("drag_end")


def test_fist_with_thumb_touching_fingers_never_clicks():
    sim = engaged_sim()
    sim.feed(PALM, 40)
    # a closing fist shortens BOTH thumb distances — must grab, never click
    out = sim.feed(make_hand(ext=(), pinch=("left", 0.2)), 8)
    out += sim.feed(make_hand(ext=(), pinch=("right", 0.2)), 8)
    out += sim.feed(PALM, 6)
    k = kinds(out)
    assert "click" not in k and "right_click" not in k and "double_click" not in k
    assert k.count("drag_start") == 1 and k.count("drag_end") == 1


def test_tracking_loss_releases_drag_then_disengages():
    sim = engaged_sim()
    sim.feed(PALM, 40)
    sim.feed(FIST, 6)
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
        out += sim.feed(make_hand(ext=("index", "middle"),
                                  shift=(0.0, -0.012 * i)))
    ticks = [i[1] for i in out if i[0] == "scroll"]
    assert sum(ticks) >= 3
    assert all(t > 0 for t in ticks)
    assert [i for i in out if i[0] == "move"] == []


# ------------------------------------------------------------------ #
# pose exposure (daemon/HUD live state)
# ------------------------------------------------------------------ #

def test_pose_attribute_tracks_committed_pose():
    sim = engaged_sim()
    sim.feed(PALM, 5)
    assert sim.e.pose == "palm"
    sim.feed(FIST, 5)
    assert sim.e.pose == "fist"
    sim.feed(PALM_BACK, 5)
    assert sim.e.pose == "back_palm"
    sim.feed(None, 2)
    assert sim.e.pose == "none"


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
