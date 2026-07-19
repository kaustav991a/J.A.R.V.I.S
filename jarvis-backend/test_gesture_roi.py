r"""
test_gesture_roi.py — G5.4 distance-ROI geometry harness (no hardware)

Run: venv\Scripts\python.exe test_gesture_roi.py

Covers the pure crop/remap math that lets a far hand fill MediaPipe's model
input without the cursor jumping: hand_box, expand_box (floor + clamp), to_px
(pixel clamp), remap_landmarks (crop-space -> full-frame, incl. the no-jump
invariant), face_anchored_box, and RoiTracker's follow/widen/reset lifecycle
plus the self-adaptive near=full-frame / far=tight-crop behaviour.
"""

from modules import gesture_roi as gr
from modules.gesture_roi import RoiTracker

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def approx(a, b, eps=1e-6):
    return abs(a - b) <= eps


def inside01(box):
    x, y, w, h = box
    return x >= -1e-9 and y >= -1e-9 and w <= 1.0 + 1e-9 and h <= 1.0 + 1e-9 \
        and x + w <= 1.0 + 1e-9 and y + h <= 1.0 + 1e-9


# ---- hand_box ---------------------------------------------------------- #

def test_hand_box():
    pts = [(0.4, 0.5), (0.6, 0.5), (0.5, 0.3), (0.5, 0.7)]
    x, y, w, h = gr.hand_box(pts)
    check(approx(x, 0.4) and approx(y, 0.3) and approx(w, 0.2) and approx(h, 0.4),
          "hand_box spans landmark extent")


def test_hand_box_clamps_out_of_range():
    # landmarks can drift slightly outside [0,1]; box must stay in-frame
    pts = [(-0.2, 0.5), (1.3, 0.5), (0.5, -0.1), (0.5, 1.2)]
    x, y, w, h = gr.hand_box(pts)
    check(x >= 0.0 and y >= 0.0 and x + w <= 1.0 and y + h <= 1.0,
          "hand_box clamps out-of-range landmarks into the frame")


def test_hand_box_ignores_z():
    pts = [(0.4, 0.5, -0.9), (0.6, 0.5, 0.2)]
    x, y, w, h = gr.hand_box(pts)
    check(approx(x, 0.4) and approx(w, 0.2), "hand_box ignores the z component")


# ---- expand_box -------------------------------------------------------- #

def test_expand_grows_and_stays_inside():
    box = gr.expand_box((0.45, 0.45, 0.10, 0.10), scale=2.0, min_frac=0.0)
    check(approx(box[2], 0.20) and approx(box[3], 0.20), "expand_box scales the box")
    check(inside01(box), "expanded box stays inside [0,1]")


def test_expand_enforces_min_frac():
    box = gr.expand_box((0.48, 0.48, 0.02, 0.02), scale=1.5, min_frac=0.30)
    check(box[2] >= 0.30 - 1e-9 and box[3] >= 0.30 - 1e-9,
          "expand_box floors a tiny box at min_frac")


def test_expand_clamps_to_full_frame():
    box = gr.expand_box((0.1, 0.1, 0.9, 0.9), scale=3.0, min_frac=0.5)
    check(approx(box[2], 1.0) and approx(box[3], 1.0) and approx(box[0], 0.0)
          and approx(box[1], 0.0), "expand_box clamps a near-full box to the whole frame")


def test_expand_edge_box_shifts_inside():
    box = gr.expand_box((0.95, 0.95, 0.04, 0.04), scale=3.0, min_frac=0.30)
    check(inside01(box), "expand_box shifts an edge box fully inside the frame")


# ---- to_px ------------------------------------------------------------- #

def test_to_px_basic():
    rx, ry, rw, rh = gr.to_px((0.25, 0.5, 0.5, 0.25), 640, 480)
    check((rx, ry, rw, rh) == (160, 240, 320, 120), "to_px converts normalized -> pixels")


def test_to_px_never_exceeds_frame():
    rx, ry, rw, rh = gr.to_px((0.9, 0.9, 0.5, 0.5), 640, 480)
    check(rx + rw <= 640 and ry + rh <= 480 and rx >= 0 and ry >= 0,
          "to_px keeps the rect inside the frame")


def test_to_px_min_one_px():
    rx, ry, rw, rh = gr.to_px((0.5, 0.5, 0.0, 0.0), 640, 480)
    check(rw >= 1 and rh >= 1, "to_px floors size at 1px")


# ---- remap_landmarks --------------------------------------------------- #

def test_remap_identity_full_frame():
    crop = (0, 0, 640, 480)
    raw = [(0.5, 0.5), (0.25, 0.75)]
    out = gr.remap_landmarks(raw, crop, 640, 480)
    check(approx(out[0][0], 0.5) and approx(out[0][1], 0.5)
          and approx(out[1][0], 0.25) and approx(out[1][1], 0.75),
          "remap over a full-frame crop is identity")


def test_remap_center_of_crop():
    # a landmark at the centre of a crop maps to that crop's centre in the frame
    crop = (160, 120, 320, 240)   # centre pixel = (320, 240) of a 640x480 frame
    out = gr.remap_landmarks([(0.5, 0.5)], crop, 640, 480)
    check(approx(out[0][0], 320 / 640) and approx(out[0][1], 240 / 480),
          "remap puts the crop centre at the crop's frame centre")


def test_remap_preserves_z():
    out = gr.remap_landmarks([(0.5, 0.5, -0.7)], (0, 0, 640, 480), 640, 480)
    check(len(out[0]) == 3 and approx(out[0][2], -0.7), "remap passes z through")


def test_remap_no_jump_across_crop_change():
    # A fixed WORLD point must map to the same full-frame coord whether it was
    # seen in a wide crop or a tighter one — this is the anti-jump guarantee.
    fw, fh = 640, 480
    world_x, world_y = 300, 200            # a real pixel in the frame
    for crop in [(0, 0, 640, 480), (200, 100, 240, 200), (250, 150, 120, 100)]:
        rx, ry, rw, rh = crop
        # only crops that actually contain the world point are valid observations
        if not (rx <= world_x <= rx + rw and ry <= world_y <= ry + rh):
            continue
        lx, ly = (world_x - rx) / rw, (world_y - ry) / rh   # where it lands in the crop
        out = gr.remap_landmarks([(lx, ly)], crop, fw, fh)
        check(approx(out[0][0], world_x / fw, 1e-6) and approx(out[0][1], world_y / fh, 1e-6),
              f"remap is crop-invariant for a fixed world point (crop={crop})")


# ---- face_anchored_box ------------------------------------------------- #

def test_face_anchored_below_and_inside():
    face = (0.4, 0.1, 0.2, 0.2)  # near top of frame
    box = gr.face_anchored_box(face, down=0.35, scale=3.0)
    check(inside01(box), "face-anchored box stays inside the frame")
    face_cy = face[1] + face[3] / 2
    box_cy = box[1] + box[3] / 2
    check(box_cy > face_cy, "face-anchored box centre sits below the face")


# ---- RoiTracker -------------------------------------------------------- #

def test_tracker_no_hand_no_face_is_full_frame():
    t = RoiTracker()
    check(t.next_crop(640, 480) is None, "no hand + no face -> full-frame (None)")


def test_tracker_seeds_from_face_before_hand():
    t = RoiTracker()
    crop = t.next_crop(640, 480, face_box_norm=(0.4, 0.1, 0.2, 0.2))
    check(crop is not None and crop[2] < 640, "before any hand, crop seeds from the face box")


def test_tracker_near_hand_is_full_frame():
    # a big (near) hand box expands+clamps to essentially the whole frame
    t = RoiTracker(expand=1.9, min_frac=0.30)
    t.update((0.15, 0.15, 0.6, 0.6))
    rx, ry, rw, rh = t.next_crop(640, 480)
    check(rw >= 600 and rh >= 460, "near hand -> crop ~= full frame (self-adaptive)")


def test_tracker_far_hand_tightens_crop():
    t = RoiTracker(expand=1.9, min_frac=0.30)
    t.update((0.49, 0.49, 0.02, 0.02))   # tiny far hand
    rx, ry, rw, rh = t.next_crop(640, 480)
    check(rw <= 640 * 0.40 and rh <= 480 * 0.40, "far hand -> crop tightens toward min_frac")
    check(rw >= 640 * 0.30 - 2 and rh >= 480 * 0.30 - 2, "far crop respects min_frac floor")


def test_tracker_follow_blends_toward_new():
    t = RoiTracker(follow=0.5)
    t.update((0.0, 0.0, 0.2, 0.2))
    t.update((0.4, 0.4, 0.2, 0.2))
    check(approx(t.box[0], 0.2) and approx(t.box[1], 0.2),
          "follow=0.5 blends the box halfway toward the new observation")


def test_tracker_widen_grows_crop_on_misses():
    t = RoiTracker(expand=1.5, min_frac=0.05, widen_after=2, reset_after=10)
    t.update((0.45, 0.45, 0.10, 0.10))
    base = t.next_crop(640, 480)[2]
    t.miss(); t.miss(); t.miss()          # past widen_after
    grown = t.next_crop(640, 480)[2]
    check(grown > base, "crop widens after consecutive misses (hand left the box)")


def test_tracker_reset_after_misses():
    t = RoiTracker(reset_after=3)
    t.update((0.4, 0.4, 0.1, 0.1))
    t.miss(); t.miss(); t.miss()
    check(t.box is None and t.next_crop(640, 480) is None,
          "after reset_after misses the tracker drops to a full-frame rescan")


def test_from_env(monkeypatch_env):
    monkeypatch_env({"JARVIS_ROI_EXPAND": "2.5", "JARVIS_ROI_MIN_FRAC": "0.4",
                     "JARVIS_ROI_FOLLOW": "0.8", "JARVIS_ROI_WIDEN_AFTER": "5",
                     "JARVIS_ROI_RESET_AFTER": "12"})
    t = RoiTracker.from_env()
    check(approx(t.expand, 2.5) and approx(t.min_frac, 0.4) and approx(t.follow, 0.8)
          and t.widen_after == 5 and t.reset_after == 12, "from_env reads all ROI knobs")


def test_from_env_defaults(monkeypatch_env):
    monkeypatch_env({})   # cleared
    t = RoiTracker.from_env()
    check(approx(t.expand, 1.9) and approx(t.min_frac, 0.30) and t.reset_after == 8,
          "from_env falls back to defaults when unset")


import os

_ENV_KEYS = ("JARVIS_ROI_EXPAND", "JARVIS_ROI_MIN_FRAC", "JARVIS_ROI_FOLLOW",
             "JARVIS_ROI_WIDEN_AFTER", "JARVIS_ROI_RESET_AFTER")


def _env_setter(mapping):
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    for k, v in mapping.items():
        os.environ[k] = v


TESTS = [
    test_hand_box, test_hand_box_clamps_out_of_range, test_hand_box_ignores_z,
    test_expand_grows_and_stays_inside, test_expand_enforces_min_frac,
    test_expand_clamps_to_full_frame, test_expand_edge_box_shifts_inside,
    test_to_px_basic, test_to_px_never_exceeds_frame, test_to_px_min_one_px,
    test_remap_identity_full_frame, test_remap_center_of_crop, test_remap_preserves_z,
    test_remap_no_jump_across_crop_change, test_face_anchored_below_and_inside,
    test_tracker_no_hand_no_face_is_full_frame, test_tracker_seeds_from_face_before_hand,
    test_tracker_near_hand_is_full_frame, test_tracker_far_hand_tightens_crop,
    test_tracker_follow_blends_toward_new, test_tracker_widen_grows_crop_on_misses,
    test_tracker_reset_after_misses, test_from_env, test_from_env_defaults,
]


def main():
    print("=" * 60)
    print("gesture_roi harness")
    print("=" * 60)
    _env_setter({})
    for t in TESTS:
        if t.__code__.co_argcount == 1:   # tests that need the env setter
            t(_env_setter)
        else:
            t()
    _env_setter({})
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
