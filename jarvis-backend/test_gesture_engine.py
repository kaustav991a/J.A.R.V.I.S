"""Phase G3 + G5.1 — harness for the gesture state machine + pointer backend.

No camera, no mediapipe, no ctypes side effects: synthetic 21-landmark hands
are fed through GestureEngine and intents are asserted. Covers the G3
natural-grab vocabulary (index-up start, back-of-hand stop, palm-centroid
move, fist grab drag, two-finger scroll) plus the G5.1 ergonomics rework:
quick pinch = left click / held pinch = DWELL right click (both on release),
thumb+middle right-click RETIRED, relative trackpad move_delta with an
acceleration curve, and back-of-hand CLUTCH (freeze + reposition, no jump).
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
    assert abs(moves[-1][1] - 0.516) < 0.03    # centroid 0.5075, sensitivity 1.5
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
    out = sim.feed(pinched(), 4)          # quick pinch: click fires on release
    out += sim.feed(PALM, 8)
    k = kinds(out)
    assert k.count("click") == 1
    assert "drag_start" not in k and "double_click" not in k and "right_click" not in k


def test_pinch_hold_is_dwell_right_click():
    # G5.1: a left pinch HELD past dwell_right_click_s is a RIGHT click (on
    # release) — never a left click, never a drag (grab is the fist).
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(pinched(), 50)         # held ~1.67s >= 1.5s dwell
    out += sim.feed(PALM, 8)              # release -> decision
    k = kinds(out)
    assert k.count("right_click") == 1
    assert "click" not in k and "double_click" not in k
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
    out += sim.feed(PALM, 8)              # release the first tap AT spot 0
    out += sim.feed(palm_at(0.15), 12)    # then travel to a new spot
    out += sim.feed(make_hand(ext=("middle", "ring", "pinky"),
                              pinch=("left", 0.2), shift=(0.15, 0.0)), 4)
    out += sim.feed(palm_at(0.15), 8)     # release the second tap AT spot 0.15
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


def test_thumb_middle_no_longer_right_clicks():
    # thumb+middle was RETIRED as the right-click in G5.1 (it reads as an
    # ambiguous "other" pose now); right-click is the left-pinch dwell instead.
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(pinched(which="right"), 6)
    out += sim.feed(PALM, 6)
    k = kinds(out)
    assert "right_click" not in k and "click" not in k


def test_quick_then_dwell_are_different_clicks():
    # same finger pair, two hold lengths -> two different clicks.
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(pinched(), 4) + sim.feed(PALM, 40)      # quick -> left click
    out += sim.feed(pinched(), 50) + sim.feed(PALM, 8)     # held  -> right click
    k = kinds(out)
    assert k.count("click") == 1 and k.count("right_click") == 1
    assert k.index("click") < k.index("right_click")


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


def test_click_fires_with_other_fingers_curled():
    # thumb+index touch with the rest of the hand relaxed/curled is still a
    # click — the old middle+ring-extended gate silently dropped these.
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(make_hand(ext=(), pinch=("left", 0.2)), 4)
    out += sim.feed(PALM, 8)
    k = kinds(out)
    assert k.count("click") == 1
    assert "drag_start" not in k and "double_click" not in k


def test_grab_with_thumb_near_middle_never_right_clicks():
    # a closed hand with the thumb parked over the MIDDLE (not pinching index)
    # is a grab, never a right click.
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(make_hand(ext=(), pinch=("right", 0.2)), 10)
    out += sim.feed(PALM, 6)
    k = kinds(out)
    assert "right_click" not in k and "click" not in k
    assert k.count("drag_start") == 1 and k.count("drag_end") == 1


def test_drag_sticky_through_classify_flicker():
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(FIST, 6)                                # grab
    out += sim.feed(make_hand(ext=("index", "ring")), 6)   # "other" mid-drag
    out += sim.feed(make_hand(ext=(), shift=(0.08, 0.0)), 6)
    out += sim.feed(PALM, 6)                               # only open palm drops
    k = kinds(out)
    assert k.count("drag_start") == 1 and k.count("drag_end") == 1
    assert [i for i in out[:-6] if i[0] == "drag_end"] == []  # not dropped early


def test_tilted_fist_one_finger_misread_still_grabs():
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(make_hand(ext=("middle",)), 6)  # tip-PIP misreads one finger
    out += sim.feed(PALM, 6)
    k = kinds(out)
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
# G6.2 — click / right-click / grab reliability (live-run bug)
# ------------------------------------------------------------------ #

def test_slow_tap_is_left_click_not_right():
    # A deliberate (slow) tap under the raised dwell is a LEFT click. At the old
    # 0.5 s dwell an ~0.6 s pinch was misclassified as a right click.
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(pinched(), 18)   # ~0.6 s hold (18 frames @ 30 fps)
    out += sim.feed(PALM, 8)        # release -> decision
    k = kinds(out)
    assert k.count("click") == 1 and "right_click" not in k


def test_fist_thumb_near_index_grabs_not_right_clicks():
    # A closed hand whose thumb rests NEAR the index (the old 0.30-0.40 overlap
    # zone) is a GRAB, not a long pinch — previously it fired a spurious click /
    # right click and the grab never engaged.
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(make_hand(ext=(), pinch=("left", 0.35)), 12)  # thumb near index
    out += sim.feed(PALM, 6)
    k = kinds(out)
    assert k.count("drag_start") == 1 and k.count("drag_end") == 1
    assert "right_click" not in k and "click" not in k


def test_curled_click_does_not_bleed_into_grab():
    # A curled-hand pinch tap must not turn into a drag as the hand reopens/closes
    # through the fist-shaped zone (grab cooldown); a real grab a beat later still
    # engages.
    sim = engaged_sim()
    sim.feed(PALM, 40)
    out = sim.feed(make_hand(ext=(), pinch=("left", 0.2)), 4)   # curled quick tap
    early = sim.feed(FIST, 4)     # fist within the post-pinch cooldown
    late = sim.feed(FIST, 14)     # fist past the cooldown -> real grab
    assert kinds(out + early).count("click") == 1
    assert "drag_start" not in kinds(early)   # cooldown suppressed the bleed
    assert kinds(late).count("drag_start") == 1


def test_g62_click_knobs_env_tunable():
    import os
    keys = ("JARVIS_PINCH_DOWN", "JARVIS_PINCH_UP",
            "JARVIS_DWELL_RIGHT_CLICK_S", "JARVIS_GRAB_AFTER_PINCH_S")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["JARVIS_PINCH_DOWN"] = "0.25"
        os.environ["JARVIS_PINCH_UP"] = "0.55"
        os.environ["JARVIS_DWELL_RIGHT_CLICK_S"] = "0.9"
        os.environ["JARVIS_GRAB_AFTER_PINCH_S"] = "0.4"
        c = GestureConfig.from_env()
        assert abs(c.pinch_down - 0.25) < 1e-9
        assert abs(c.pinch_up - 0.55) < 1e-9
        assert abs(c.dwell_right_click_s - 0.9) < 1e-9
        assert abs(c.grab_after_pinch_s - 0.4) < 1e-9
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


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


# ------------------------------------------------------------------ #
# G5.1 — relative trackpad mapping + acceleration + clutch
# ------------------------------------------------------------------ #

def rel_sim(fps=30.0):
    sim = Sim(GestureEngine(GestureConfig(mapping_mode="relative")), fps=fps)
    assert sim.feed(INDEX_UP, 35) == [("engaged",)]
    return sim


def test_mapping_mode_defaults_to_absolute():
    assert GestureConfig().mapping_mode == "absolute"


def test_env_toggles_relative_mapping():
    import os
    old = os.environ.get("JARVIS_GESTURE_RELATIVE")
    try:
        os.environ["JARVIS_GESTURE_RELATIVE"] = "1"
        assert GestureConfig.from_env().mapping_mode == "relative"
        os.environ["JARVIS_GESTURE_RELATIVE"] = "0"
        assert GestureConfig.from_env().mapping_mode == "absolute"
    finally:
        if old is None:
            os.environ.pop("JARVIS_GESTURE_RELATIVE", None)
        else:
            os.environ["JARVIS_GESTURE_RELATIVE"] = old


def test_relative_emits_move_delta_not_absolute():
    sim = rel_sim()
    sim.feed(PALM, 5)                      # anchor sync (no emit)
    out = []
    for i in range(1, 8):                  # hand travels rightward
        out += sim.feed(palm_at(0.02 * i), 1)
    deltas = [i for i in out if i[0] == "move_delta"]
    assert deltas, "relative palm must emit move_delta"
    assert all(d[1] > 0 for d in deltas)          # rightward hand -> +dx
    assert [i for i in out if i[0] == "move"] == []   # never absolute in relative mode


def test_accel_curve_dampens_slow_amplifies_fast():
    e = GestureEngine(GestureConfig(mapping_mode="relative"))
    c = e.cfg
    assert e._accel(0.0) == c.accel_slow
    assert e._accel(c.accel_v_lo) == c.accel_slow
    assert e._accel(c.accel_v_hi) == c.accel_fast
    assert e._accel(1e9) == c.accel_fast
    mid = e._accel((c.accel_v_lo + c.accel_v_hi) / 2.0)
    assert c.accel_slow < mid < c.accel_fast          # monotonic ramp
    assert c.accel_slow < 1.0 < c.accel_fast          # slow dampens, fast amplifies


def test_relative_deadzone_ignores_micro_jitter():
    sim = rel_sim()
    sim.feed(PALM, 5)
    out = []
    for i in range(10):                   # sub-deadzone wobble
        out += sim.feed(palm_at(0.0005 * (-1) ** i))
    assert [i for i in out if i[0] == "move_delta"] == []


def test_relative_clutch_freezes_and_reengages_without_jump():
    sim = rel_sim()
    sim.feed(PALM, 5)
    sim.feed(palm_at(0.1), 5)             # establish motion
    # back-of-hand = clutch: reposition the hand far while frozen
    sim.feed(make_hand(facing=False, shift=(0.1, 0.0)), 5)
    out_clutch = sim.feed(make_hand(facing=False, shift=(0.4, 0.0)), 5)
    assert sim.e.clutch is True
    assert [i for i in out_clutch if i[0] == "move_delta"] == []   # frozen
    # re-face the palm at the NEW hand location -> resume, no jump
    out_resume = sim.feed(palm_at(0.4), 8)
    deltas = [i for i in out_resume if i[0] == "move_delta"]
    assert all(abs(d[1]) < 0.1 for d in deltas), "clutch must prevent a re-engage jump"
    assert sim.e.clutch is False


def test_clutch_flag_false_when_not_back_palm():
    sim = rel_sim()
    sim.feed(PALM, 6)
    assert sim.e.clutch is False


def test_relative_disengage_still_works():
    sim = rel_sim()
    out = sim.feed(PALM_BACK, 55)         # sustained back-of-hand = STOP
    assert ("disengaged",) in out
    assert not sim.e.engaged and sim.e.clutch is False


def test_pointer_move_rel_adds_delta_to_cursor():
    rec = Recorder()
    b = PointerBackend(send_fn=rec, cursor_fn=lambda: (0.5, 0.5))
    b.execute([("move_delta", 0.1, -0.2)])
    flags, dx, dy, _ = rec.calls[0]
    assert flags == PointerBackend.MOVE_FLAGS
    assert abs(dx - to_absolute(0.6)) <= 1
    assert abs(dy - to_absolute(0.3)) <= 1


def test_pointer_move_rel_clamps_at_edges():
    rec = Recorder()
    b = PointerBackend(send_fn=rec, cursor_fn=lambda: (0.95, 0.05))
    b.execute([("move_delta", 0.5, -0.5)])
    _, dx, dy, _ = rec.calls[0]
    assert dx == to_absolute(1.0) and dy == to_absolute(0.0)


# ------------------------------------------------------------------ #
# G5.5 precision / fine-target damping
# ------------------------------------------------------------------ #

def test_precision_gain_ramp():
    e = GestureEngine(GestureConfig())
    c = e.cfg
    assert e._precision_gain(0.0) == c.precision_gain
    assert e._precision_gain(c.precision_v_lo) == c.precision_gain
    assert e._precision_gain(c.precision_v_hi) == 1.0
    assert e._precision_gain(1e9) == 1.0
    mid = e._precision_gain((c.precision_v_lo + c.precision_v_hi) / 2.0)
    assert c.precision_gain < mid < 1.0          # monotonic ramp
    assert c.precision_gain < 1.0                 # slow speed is damped


def test_precision_disabled_is_unity():
    e = GestureEngine(GestureConfig(precision=False))
    assert e._precision_gain(0.0) == 1.0
    assert e._precision_gain(1e9) == 1.0


def test_precision_env_toggle():
    import os
    keys = ("JARVIS_GESTURE_PRECISION", "JARVIS_PRECISION_GAIN")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["JARVIS_GESTURE_PRECISION"] = "0"
        assert GestureConfig.from_env().precision is False
        os.environ["JARVIS_GESTURE_PRECISION"] = "1"
        os.environ["JARVIS_PRECISION_GAIN"] = "0.2"
        c = GestureConfig.from_env()
        assert c.precision is True and abs(c.precision_gain - 0.2) < 1e-9
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_precision_dampens_slow_relative_move():
    # a slow drift travels LESS far with precision on than off, but still tracks
    def total(precision):
        e = GestureEngine(GestureConfig(mapping_mode="relative", precision=precision))
        sim = Sim(e)
        assert sim.feed(INDEX_UP, 35) == [("engaged",)]
        sim.feed(PALM, 5)                       # anchor sync (no emit)
        out = []
        for i in range(1, 15):                  # steady slow rightward drift
            out += sim.feed(palm_at(0.006 * i), 1)
        return sum(d[1] for d in out if d[0] == "move_delta")
    on, off = total(True), total(False)
    assert off > on > 0, f"precision should dampen slow drift (on={on}, off={off})"


def test_precision_leaves_target_unbiased_absolute():
    # precision only eases the APPROACH — a settled cursor lands on the same
    # target with precision on or off (the easing fixed point is the target).
    def endpos(precision):
        e = GestureEngine(GestureConfig(precision=precision))
        sim = Sim(e)
        assert sim.feed(INDEX_UP, 35) == [("engaged",)]
        sim.feed(PALM, 40)
        out = sim.feed(palm_at(0.2), 40)        # big shift, then settle
        moves = [m for m in out if m[0] == "move"]
        return moves[-1][1] if moves else None
    on, off = endpos(True), endpos(False)
    assert on is not None and off is not None
    assert abs(on - off) < 0.01, f"precision must not bias the target (on={on}, off={off})"


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
