"""
gesture_pointer.py — Phase G2 pointer backend (HAND_GESTURE_CONTROL_PLAN.md §2)
================================================================================

Thin, dumb executor for gesture_engine intents. ctypes SendInput only — NOT
pyautogui: its per-call overhead + failsafe checks are too slow for 30 Hz
cursor updates (plan §1).

Multi-monitor + DPI aware: SetProcessDpiAwareness(2) (same fix as
human_gui_agent.py) and MOUSEEVENTF_VIRTUALDESK absolute coordinates, so a
normalised (0..1) position spans the whole virtual desktop in physical pixels.

Testable: pass send_fn to capture (flags, dx, dy, data) tuples instead of
calling the real SendInput (see test_gesture_engine.py).
"""

from __future__ import annotations

import ctypes
import sys

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
WHEEL_DELTA = 120
INPUT_MOUSE = 0

if sys.platform == "win32":
    try:  # physical-pixel coordinates on scaled displays (per human_gui_agent)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_long),  # LONG so wheel ticks can be negative
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class INPUT(ctypes.Structure):
        # MOUSEINPUT is the largest member of the real INPUT union, so a
        # mouse-only struct has the correct size for SendInput.
        _fields_ = [("type", ctypes.c_ulong), ("mi", MOUSEINPUT)]

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    def _real_send(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> None:
        inp = INPUT(type=INPUT_MOUSE,
                    mi=MOUSEINPUT(dx=dx, dy=dy, mouseData=data,
                                  dwFlags=flags, time=0, dwExtraInfo=0))
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    # Virtual-desktop metric indices for GetSystemMetrics.
    _SM_XVIRTUALSCREEN, _SM_YVIRTUALSCREEN = 76, 77
    _SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN = 78, 79

    def _real_cursor_norm() -> tuple[float, float]:
        """Live OS cursor position as normalised 0..1 over the virtual desktop.

        Used by relative (trackpad) mode: read-add-write, so a delta always moves
        from where the cursor ACTUALLY is (self-correcting if it drifts)."""
        pt = _POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        gsm = ctypes.windll.user32.GetSystemMetrics
        x0 = gsm(_SM_XVIRTUALSCREEN)
        y0 = gsm(_SM_YVIRTUALSCREEN)
        w = gsm(_SM_CXVIRTUALSCREEN) or 1
        h = gsm(_SM_CYVIRTUALSCREEN) or 1
        return ((pt.x - x0) / w, (pt.y - y0) / h)
else:  # non-Windows: import must stay safe for the harness
    def _real_send(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> None:
        raise RuntimeError("SendInput backend is Windows-only")

    def _real_cursor_norm() -> tuple[float, float]:
        return (0.5, 0.5)


def to_absolute(n: float) -> int:
    """Normalised 0..1 -> SendInput absolute coordinate (0..65535)."""
    return int(round(min(max(n, 0.0), 1.0) * 65535))


class PointerBackend:
    """Executes gesture_engine intent tuples as real mouse input."""

    MOVE_FLAGS = (MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                  | MOUSEEVENTF_VIRTUALDESK)

    def __init__(self, send_fn=None, cursor_fn=None):
        self._send = send_fn or _real_send
        # live normalised cursor position source (relative/trackpad mode).
        self._cursor = cursor_fn or _real_cursor_norm

    def move(self, nx: float, ny: float) -> None:
        self._send(self.MOVE_FLAGS, to_absolute(nx), to_absolute(ny))

    def move_rel(self, dx: float, dy: float) -> None:
        """Relative (trackpad) move: add signed screen-fraction deltas to the
        LIVE cursor position and re-issue as an absolute SendInput. Absolute
        placement (not MOUSEEVENTF_MOVE mickeys) so Windows pointer ballistics
        can't distort the engine's own acceleration curve; read-add-write keeps
        it correct even if the cursor was moved by something else."""
        cx, cy = self._cursor()
        x = min(max(cx + dx, 0.0), 1.0)
        y = min(max(cy + dy, 0.0), 1.0)
        self._send(self.MOVE_FLAGS, to_absolute(x), to_absolute(y))

    def left_down(self) -> None:
        self._send(MOUSEEVENTF_LEFTDOWN)

    def left_up(self) -> None:
        self._send(MOUSEEVENTF_LEFTUP)

    def click(self) -> None:
        self.left_down()
        self.left_up()

    def double_click(self) -> None:
        self.click()
        self.click()

    def right_click(self) -> None:
        self._send(MOUSEEVENTF_RIGHTDOWN)
        self._send(MOUSEEVENTF_RIGHTUP)

    def scroll(self, ticks: int) -> None:
        if ticks:
            self._send(MOUSEEVENTF_WHEEL, data=ticks * WHEEL_DELTA)

    def release_all(self) -> None:
        """Safety: never leave a button stuck down (daemon crash/disengage)."""
        self.left_up()

    def execute(self, intents) -> None:
        for intent in intents:
            kind = intent[0]
            if kind == "move":
                self.move(intent[1], intent[2])
            elif kind == "move_delta":
                self.move_rel(intent[1], intent[2])
            elif kind == "click":
                self.click()
            elif kind == "double_click":
                self.double_click()
            elif kind == "right_click":
                self.right_click()
            elif kind == "drag_start":
                self.left_down()
            elif kind == "drag_end":
                self.left_up()
            elif kind == "scroll":
                self.scroll(intent[1])
            # "engaged"/"disengaged" are daemon/HUD concerns, not pointer ones
