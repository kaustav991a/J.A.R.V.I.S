"""
Phase 10: Human GUI Agent
Provides true 'Agentic Computer Use' by physically moving the mouse and typing.
Integrates a visual Observation-Action loop for a Vision LLM.
"""
import time
import random
import pyautogui
import base64
from io import BytesIO
import os
import json
import re
from dotenv import load_dotenv
from PIL import ImageGrab, ImageDraw, ImageFont
import win32gui
import win32con
import win32clipboard

from modules.groq_key_manager import (
    get_initial_client,
    has_groq_keys,
    run_with_key_rotation,
)

pyautogui.FAILSAFE = True

# ── DPI awareness: ensure pyautogui/ImageGrab coords match physical pixels ────
# Without this, on high-DPI displays (125%/150% scaling), ImageGrab captures
# at the virtualized resolution but pyautogui clicks at physical resolution,
# causing coordinates to drift. PROCESS_PER_MONITOR_DPI_AWARE (2) gives us
# the real pixel grid on every monitor.
try:
    import ctypes as _ctypes_dpi
    _ctypes_dpi.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        import ctypes as _ctypes_dpi
        _ctypes_dpi.windll.user32.SetProcessDPIAware()  # fallback for Win8.0
    except Exception:
        pass

load_dotenv(override=True)

if has_groq_keys():
    client = get_initial_client()
else:
    client = None
    print("[HUMAN GUI AGENT] WARNING: No Groq API keys (GROQ_API_KEY / GROQ_API_KEYS) found.")

class HumanGUIAgent:
    def __init__(self):
        print("[HUMAN GUI AGENT] Initialized.")
        self.vision_client = client
        # Using Groq Meta Llama 4 Scout (Latest Multimodal Instruct Generation)
        self.model_id = "meta-llama/llama-4-scout-17b-16e-instruct"

    # --- 1. The Action Space (Wrappers) ---
    
    def move_mouse(self, x: int, y: int):
        """Glides the mouse to the target."""
        print(f"[HUMAN GUI AGENT] Moving mouse to ({x}, {y})")
        pyautogui.moveTo(x, y, duration=0.5, tween=pyautogui.easeInOutQuad)
        time.sleep(random.uniform(0.1, 0.3))

    def click_mouse(self, clicks: int = 1):
        """Clicks the mouse."""
        print(f"[HUMAN GUI AGENT] Clicking {clicks} time(s)")
        time.sleep(0.1) # Brief pause before clicking like a human
        pyautogui.click(clicks=clicks)
        time.sleep(random.uniform(0.1, 0.3))

    def type_text(self, text: str):
        """Types text naturally."""
        print(f"[HUMAN GUI AGENT] Typing: {text[:20]}...")
        pyautogui.write(text, interval=0.05)
        time.sleep(random.uniform(0.1, 0.4))

    def press_key(self, key: str):
        """Presses a specific key or hotkey combination (e.g. 'win', 'enter', 'ctrl+s')."""
        key = key.lower().replace(" ", "")
        print(f"[HUMAN GUI AGENT] Pressing key: {key}")
        
        if "+" in key:
            keys = key.split("+")
            pyautogui.hotkey(*keys)
        else:
            pyautogui.press(key)
        time.sleep(random.uniform(0.3, 0.6))

    def scroll(self, amount: int):
        """Scrolls the mouse wheel."""
        print(f"[HUMAN GUI AGENT] Scrolling by {amount}")
        pyautogui.scroll(amount)
        time.sleep(random.uniform(0.2, 0.5))

    def smart_open_app(self, target: str) -> str:
        """
        Windows Search Fallback macro.
        Bypasses brittle file paths by using native human-like OS interaction.
        """
        print(f"[HUMAN GUI AGENT] Triggering Windows Search for: {target}")
        try:
            self.press_key('win')
            time.sleep(0.5) 
            self.type_text(target)
            time.sleep(1.2)
            self.press_key('enter')
            return f"Used Windows Search to open '{target}'."
        except Exception as e:
            return f"Smart Open failed: {e}"

    def execute_gui_action(self, action_type: str, query: str = "") -> str:
        """
        Standardized router for simple GUI actions.
        Replaces the legacy GUIAgent with human-like physical inputs.
        """
        action_type = action_type.lower()
        print(f"[HUMAN GUI AGENT] Routing direct action: {action_type} with query: {query}")
        
        try:
            if action_type == "smart_open_app":
                return self.smart_open_app(query)
            elif action_type == "keyboard_type":
                self.type_text(query)
                return f"Typed '{query}'."
            elif action_type == "keyboard_press":
                self.press_key(query)
                return f"Pressed '{query}'."
            elif action_type == "mouse_scroll":
                # Handle "down 500" or "up 200"
                parts = query.split()
                if len(parts) >= 2:
                    dir = parts[0].lower()
                    amt = int(parts[1])
                    if dir == "down": amt = -amt
                    self.scroll(amt)
                    return f"Scrolled {dir} by {abs(amt)}."
                return "Scroll query invalid."
            elif action_type == "mouse_move":
                # Direct coordinate move (if LLM provides them)
                parts = query.replace(",", " ").split()
                if len(parts) >= 2:
                    self.move_mouse(int(parts[0]), int(parts[1]))
                    return f"Moved mouse to {parts[0]}, {parts[1]}."
                return "Mouse move query invalid."
            
            return f"Unknown GUI action: {action_type}"
        except Exception as e:
            return f"GUI Execution Error: {e}"
    def _find_matching_windows(self, title_regex: str, pid: int = None) -> list[int]:
        """Returns visible HWNDs whose title matches regex (and pid if provided)."""
        import win32process

        hwnds = []

        def window_enum_callback(hwnd, results):
            if not win32gui.IsWindowVisible(hwnd):
                return
            window_text = win32gui.GetWindowText(hwnd)
            if not re.search(title_regex, window_text, re.IGNORECASE):
                return
            if pid is not None:
                try:
                    _, hwnd_pid = win32process.GetWindowThreadProcessId(hwnd)
                    if hwnd_pid != pid:
                        return
                except Exception:
                    return
            results.append(hwnd)

        win32gui.EnumWindows(window_enum_callback, hwnds)
        return hwnds

    def _window_meta(self, hwnd: int) -> dict:
        """Safe metadata lookup for an HWND."""
        try:
            import win32process
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = None
        return {
            "hwnd": hwnd,
            "pid": pid,
            "title": win32gui.GetWindowText(hwnd) if hwnd else "",
        }

    def focus_window_hwnd(self, hwnd: int) -> bool:
        """HWND-first focus assertion (deterministic when handle is known)."""
        if not hwnd:
            return False
        try:
            if not win32gui.IsWindow(hwnd):
                return False
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.1)
            return True
        except Exception as e:
            print(f"[HUMAN GUI AGENT] HWND focus failed ({hwnd}): {e}")
            return False

    def resolve_window(self, title_regex: str, pid: int = None) -> dict | None:
        """
        Returns best-match window metadata for app session targeting.
        Priority: foreground match > first enum match.
        """
        hwnds = self._find_matching_windows(title_regex, pid=pid)
        if not hwnds:
            return None
        fg = win32gui.GetForegroundWindow()
        if fg in hwnds:
            return self._window_meta(fg)
        return self._window_meta(hwnds[0])

    def assert_window_focus(self, title_regex: str, pid: int = None) -> bool:
        """
        Forcefully asserts focus to a window using low-level OS calls.

        If pid is provided, ONLY matches windows owned by that exact process.
        This prevents the classic 'wrong Notepad' bug where multiple instances of
        the same app are running and an older one steals focus from the new one.
        """
        hwnds = self._find_matching_windows(title_regex, pid=pid)

        if not hwnds:
            return False

        hwnd = hwnds[0]
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.1)
            return True
        except Exception as e:
            print(f"[HUMAN GUI AGENT] Focus Assertion Failed for '{title_regex}' (pid={pid}): {e}")
            return False

    def post_launch_focus(self, app_name: str, pid: int = None) -> dict:
        """
        Called immediately after launching a new application.

        Strategy:
          1. Wait 1.0 s for the window to render.
          2. Try PID-precise focus first (always picks the NEW window we just spawned).
          3. Fall back to title-only match if PID lookup fails.
          4. Last resort: a center-screen pyautogui click.
        Returns metadata dict: {"focused": bool, "hwnd": int|None, "pid": int|None, "title": str}
        """
        time.sleep(1.0)
        print(f"[HUMAN GUI AGENT] Asserting post-launch focus for '{app_name}' (pid={pid})")

        focused = self.assert_window_focus(app_name, pid=pid) if pid else False
        if not focused:
            focused = self.assert_window_focus(app_name)

        if not focused:
            print(f"[HUMAN GUI AGENT] Could not locate '{app_name}' window. Clicking screen center as last resort.")
            try:
                sw, sh = pyautogui.size()
                pyautogui.click(sw // 2, sh // 2)
                time.sleep(0.2)
            except Exception as e:
                print(f"[HUMAN GUI AGENT] Center-screen fallback click failed: {e}")

        resolved = self.resolve_window(app_name, pid=pid) or self.resolve_window(app_name)
        if resolved:
            print(
                f"[HUMAN GUI AGENT] Post-launch window resolved: "
                f"title='{resolved['title']}' hwnd={resolved['hwnd']} pid={resolved['pid']}"
            )
            resolved["focused"] = focused
            return resolved
        return {"focused": focused, "hwnd": None, "pid": None, "title": ""}

    def _uia_set_control_text(self, text: str, app_hint: str = None,
                              app_pid: int = None, app_hwnd: int = None) -> bool:
        """
        Deterministic, focus-independent text injection via UI Automation.

        Connects to the target window by HWND (preferred) or PID, locates the
        primary editable control (Edit / Document), and sets its value directly
        through the UIA ValuePattern — no screen coordinates, no keystroke focus
        race, no char-by-char timing. This is the most accurate path and is tried
        FIRST; on any failure (control not found, read-only ValuePattern on some
        Win11 Notepad builds, COM error) it returns False so ghost_type falls back
        to keyboard.send_keys.

        Returns True only when the control's value was actually set.
        """
        try:
            from pywinauto.application import Application
        except ImportError:
            return False

        app = None
        try:
            if app_hwnd:
                try:
                    app = Application(backend="uia").connect(handle=app_hwnd, timeout=2)
                except Exception:
                    app = None
            if app is None and app_pid:
                app = Application(backend="uia").connect(process=app_pid, timeout=2)
            if app is None:
                return False

            win = app.window(handle=app_hwnd) if app_hwnd else app.top_window()

            # Win11 Notepad exposes its editor as a "Document" control (not "Edit"),
            # and neither exposes a set_text() wrapper method — but both support the
            # UIA ValuePattern. Classic Win32 edits expose "Edit". Try both.
            wrapper = None
            for ctype in ("Edit", "Document"):
                try:
                    cand = win.child_window(control_type=ctype, found_index=0)
                    if cand.exists(timeout=1):
                        wrapper = cand.wrapper_object()
                        break
                except Exception:
                    continue
            if wrapper is None:
                return False

            try:
                wrapper.set_focus()
            except Exception:
                pass  # value-set does not require focus; best-effort only

            # Drive the ValuePattern directly. This writes the string VERBATIM —
            # no send_keys escaping artifacts (e.g. '&' → '&&', '+^%~(){}' mangling).
            iface = getattr(wrapper, "iface_value", None)
            if iface is None:
                return False
            try:
                if iface.CurrentIsReadOnly:
                    return False  # read-only control (e.g. TextPattern-only editors)
            except Exception:
                pass
            iface.SetValue(text)

            # Verify the value actually took (newline-agnostic: SetValue may store
            # '\n' as '\r'). Compare a newline-stripped prefix.
            try:
                current = iface.CurrentValue or ""
                probe = text[:32].replace("\n", "").replace("\r", "")
                seen = current.replace("\n", "").replace("\r", "")
                if probe and probe not in seen:
                    return False
            except Exception:
                pass  # no readable value — trust SetValue's own error signalling

            print("[HUMAN GUI AGENT] Text injected via UIA ValuePattern (deterministic path).")
            return True
        except Exception as e:
            print(f"[HUMAN GUI AGENT] UIA set_text failed (falling back to keyboard): {e}")
            return False

    def ghost_type(self, text_to_type: str, shortcut_keys: str = None, app_hint: str = None,
                   app_pid: int = None, app_hwnd: int = None) -> str:
        """
        Ghost Protocol: Instantly injects text into the active foreground window.

        app_hint (optional): app name expected to be active (e.g. "Notepad").
        app_pid  (optional): PID hint for process-specific focus assertion.
        app_hwnd (optional): exact HWND for deterministic targeting (preferred).
        """
        try:
            from pywinauto import keyboard
        except ImportError:
            return "PYWINAUTO_NOT_INSTALLED"

        # --- Focus Verification Gate ---
        if app_hint:
            import win32process
            fg_hwnd  = win32gui.GetForegroundWindow()
            fg_title = win32gui.GetWindowText(fg_hwnd)
            try:
                _, fg_pid = win32process.GetWindowThreadProcessId(fg_hwnd)
            except Exception:
                fg_pid = None

            print(
                f"[HUMAN GUI AGENT] Ghost Type: active window '{fg_title}' (hwnd={fg_hwnd}, pid={fg_pid}, "
                f"expected app='{app_hint}', expected pid={app_pid}, expected hwnd={app_hwnd})"
            )

            # Wrong-window detection: title doesn't contain hint OR (pid given and doesn't match)
            title_mismatch = app_hint.lower() not in fg_title.lower()
            pid_mismatch   = app_pid is not None and fg_pid is not None and fg_pid != app_pid
            hwnd_mismatch  = app_hwnd is not None and fg_hwnd != app_hwnd

            if title_mismatch or pid_mismatch or hwnd_mismatch:
                print(
                    f"[HUMAN GUI AGENT] Focus mismatch — recovering to '{app_hint}' "
                    f"(pid={app_pid}, hwnd={app_hwnd})."
                )
                recovered = self.focus_window_hwnd(app_hwnd) if app_hwnd else False
                if not recovered and app_pid:
                    recovered = self.assert_window_focus(app_hint, pid=app_pid)
                if not recovered:
                    recovered = self.assert_window_focus(app_hint)
                time.sleep(0.3)
                if not recovered:
                    return (
                        f"ERROR: Ghost Type aborted. Expected '{app_hint}' (pid={app_pid}, hwnd={app_hwnd}) to be active, "
                        f"but '{fg_title}' (hwnd={fg_hwnd}, pid={fg_pid}) has focus."
                    )

        try:
            # If we're targeting Notepad and the current tab looks non-empty/non-fresh,
            # open a new tab first so generated content doesn't append into existing text.
            if app_hint and app_hint.lower() == "notepad":
                active_title = win32gui.GetWindowText(win32gui.GetForegroundWindow())
                title_lc = active_title.lower().strip()
                needs_fresh_tab = (
                    "notepad" in title_lc
                    and ("untitled" not in title_lc or active_title.lstrip().startswith("*"))
                )
                if needs_fresh_tab:
                    print("[HUMAN GUI AGENT] Notepad appears non-fresh — attempting new tab (^n).")
                    keyboard.send_keys("^n", pause=0.05)
                    time.sleep(0.35)

                    # If Notepad asks what to do with unsaved changes, DO NOT auto-discard.
                    # Return a sentinel so ActionEngine can ask the user first.
                    prompt_state = self._detect_notepad_unsaved_prompt()
                    if prompt_state:
                        print("[HUMAN GUI AGENT] Unsaved-changes prompt detected after ^n; awaiting user decision.")
                        return "UNSAVED_CHANGES_PROMPT"

                    # Re-assert focus back to Notepad window before typing.
                    if app_pid:
                        self.assert_window_focus("Notepad", pid=app_pid)
                    else:
                        self.assert_window_focus("Notepad")
                    time.sleep(0.2)

            # Normalize pipe characters used as line-break separators by the LLM.
            # The brain sometimes outputs "line1|line2" instead of "line1\nline2".
            text_to_type = text_to_type.replace("|", "\n")

            print(f"[HUMAN GUI AGENT] Ghost Type initiated. Text length: {len(text_to_type)}")

            # --- Primary path: deterministic UIA ValuePattern injection ---
            # Focus-independent and coordinate-free; targets the exact control in
            # the exact (pid/hwnd) window. Falls back to keyboard on any failure.
            injected = self._uia_set_control_text(
                text_to_type, app_hint=app_hint, app_pid=app_pid, app_hwnd=app_hwnd
            )

            if not injected:
                # --- Fallback: keyboard.send_keys into the focused window ---
                print("[HUMAN GUI AGENT] UIA path unavailable — using keyboard.send_keys fallback.")
                # Sanitize text for pywinauto special characters and newlines
                sanitized_text = ""
                for char in text_to_type:
                    if char in "+^%~{}()":
                        sanitized_text += f"{{{char}}}"
                    elif char == '\n':
                        sanitized_text += "{ENTER}"
                    else:
                        sanitized_text += char

                keyboard.send_keys(sanitized_text, with_spaces=True)

            # Fire shortcut if provided
            if shortcut_keys:
                print(f"[HUMAN GUI AGENT] Firing shortcut: {shortcut_keys}")
                time.sleep(0.5)
                keyboard.send_keys(shortcut_keys)
                time.sleep(0.5)

            return "Task completed successfully: Text injected via Ghost Type."
        except Exception as e:
            print(f"[HUMAN GUI AGENT] Ghost Type Exception: {e}")
            return f"ERROR: {str(e)}"

    def _detect_notepad_unsaved_prompt(self) -> bool:
        """
        Best-effort detector for the transient Notepad unsaved-changes prompt.
        On Win11 the dialog title can still be 'Notepad', so we use title + class hints.
        """
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            class_name = win32gui.GetClassName(hwnd)
            t = (title or "").lower()
            c = (class_name or "").lower()
            if "save" in t and "notepad" in t:
                return True
            # Generic modal dialog classes commonly used by message boxes.
            if c in {"#32770", "dialog"} and "notepad" in t:
                return True
            # Even if title is plain 'Notepad', dialog class often identifies modal.
            if c == "#32770" and "notepad" in t:
                return True
            return False
        except Exception:
            return False

    def handle_notepad_unsaved_prompt(self, decision: str) -> str:
        """
        Handles an active Notepad unsaved-changes prompt.
        decision: 'save' | 'discard' | 'cancel'
        Returns:
          SAVE_SELECTED | DISCARDED | CANCELLED | PROMPT_NOT_FOUND | ERROR:...
        """
        try:
            from pywinauto import keyboard
        except ImportError:
            return "PYWINAUTO_NOT_INSTALLED"

        try:
            if not self._detect_notepad_unsaved_prompt():
                return "PROMPT_NOT_FOUND"

            d = (decision or "").lower().strip()
            if d == "save":
                # Win11 Notepad dialog commonly supports Alt+S for Save.
                keyboard.send_keys("%s", pause=0.05)
                time.sleep(0.35)
                return "SAVE_SELECTED"
            if d == "discard":
                # Win11 Notepad dialog commonly supports Alt+N for Don't Save.
                keyboard.send_keys("%n", pause=0.05)
                time.sleep(0.35)
                return "DISCARDED"
            # cancel/default
            keyboard.send_keys("{ESC}", pause=0.05)
            time.sleep(0.25)
            return "CANCELLED"
        except Exception as e:
            return f"ERROR: {e}"

    def close_notepad_gracefully(self, app_pid: int = None, app_hwnd: int = None) -> str:
        """
        Attempts a graceful Notepad close via Alt+F4.
        Returns:
          CLOSED_OR_NO_PROMPT | UNSAVED_CHANGES_PROMPT | ERROR:...
        """
        try:
            from pywinauto import keyboard
        except ImportError:
            return "PYWINAUTO_NOT_INSTALLED"

        try:
            focused = self.focus_window_hwnd(app_hwnd) if app_hwnd else False
            if not focused and app_pid:
                focused = self.assert_window_focus("Notepad", pid=app_pid)
            if not focused:
                focused = self.assert_window_focus("Notepad")
            if not focused:
                return "ERROR: could_not_focus_notepad"
            time.sleep(0.2)
            keyboard.send_keys("%{F4}", pause=0.05)
            time.sleep(0.35)
            if self._detect_notepad_unsaved_prompt():
                return "UNSAVED_CHANGES_PROMPT"
            return "CLOSED_OR_NO_PROMPT"
        except Exception as e:
            return f"ERROR: {e}"

    def ghost_save_file(self, target_dir: str, filename: str, force_overwrite: bool = False,
                        app_hint: str = None, app_pid: int = None, app_hwnd: int = None) -> str:
        """
        Ghost Protocol — Keyboard-First Save (self-sufficient).

        The previous pywinauto-based version hung up to 10 s on Application.connect()
        when the Save As dialog had non-standard control IDs (common on Windows 11 Notepad).

        New strategy:
          0. Ensure the target app (e.g. Notepad) has focus, then SUMMON the Save dialog
             ourselves with Ctrl+S — never assume ghost_type already did it.
          1. Poll win32gui.GetForegroundWindow() for up to 3 s waiting for a dialog
             whose title matches /^Save\\b/ (matches "Save As", "Save as", "Save").
          2. The filename input box is auto-focused on Save As open. Put the full
             absolute path on the clipboard, then Ctrl+A → Ctrl+V → Enter (typing
             the path via send_keys can mangle backslashes, e.g. C:\\Users\\…).
          3. Briefly poll for a "Confirm Save As" overwrite popup; press Y/N via keyboard.
          4. Verify the file actually landed on disk before reporting SUCCESS.

        Returns: "SUCCESS" | "FILE_EXISTS" | "SAVE_DIALOG_NOT_FOUND" | "ERROR: ..."
        """
        try:
            from pywinauto import keyboard
        except ImportError:
            return "PYWINAUTO_NOT_INSTALLED"

        try:
            absolute_path = os.path.abspath(os.path.join(target_dir, filename))
            print(f"[HUMAN GUI AGENT] Ghost Save (keyboard-first) initiated for: {absolute_path}")

            # ── Pre-check: file already on disk? ───────────────────────────────────
            # If yes and we are NOT explicitly forcing overwrite, bail out immediately
            # WITHOUT touching the GUI at all. The action_engine will ask the user
            # whether to overwrite or save as a new name, then re-invoke us.
            if os.path.exists(absolute_path) and not force_overwrite:
                print(f"[HUMAN GUI AGENT] File already exists, deferring decision: {absolute_path}")
                return "FILE_EXISTS"

            # ── Step 0: Summon the Save dialog ourselves ───────────────────────────
            # Don't trust that ghost_type sent ^s — many JSON action chains omit it.
            # First check if a Save dialog is already up; if not, focus the target
            # app and send Ctrl+S to summon it.
            fg_title_now = win32gui.GetWindowText(win32gui.GetForegroundWindow())
            fg_class_now = win32gui.GetClassName(win32gui.GetForegroundWindow())
            # Detect Save dialog by title (English) OR window class #32770 (common dialog — locale-independent)
            _is_save_dialog = (
                re.match(r"^Save(\s|$)", fg_title_now, re.IGNORECASE)
                or (fg_class_now == "#32770" and any(kw in fg_title_now.lower() for kw in ("save", "speichern", "enregistrer", "guardar")))
            )
            if not _is_save_dialog:
                # Bring the app back to focus so Ctrl+S goes to the right window.
                if app_hint:
                    print(
                        f"[HUMAN GUI AGENT] Pre-save focus check → asserting '{app_hint}' "
                        f"(pid={app_pid}, hwnd={app_hwnd})."
                    )
                    focused = self.focus_window_hwnd(app_hwnd) if app_hwnd else False
                    if not focused and app_pid:
                        focused = self.assert_window_focus(app_hint, pid=app_pid)
                    if not focused:
                        self.assert_window_focus(app_hint)
                    time.sleep(0.25)
                print("[HUMAN GUI AGENT] Summoning Save dialog with Ctrl+S.")
                try:
                    keyboard.send_keys("^s", pause=0.05)
                except Exception as e:
                    print(f"[HUMAN GUI AGENT] Ctrl+S injection failed: {e}")
                time.sleep(0.4)

            # ── Step 1: Wait for Save dialog (max 5 s, 200 ms poll interval) ────────
            dialog_hwnd = None
            dialog_title = ""
            title = ""
            for _ in range(25):
                hwnd = win32gui.GetForegroundWindow()
                title = win32gui.GetWindowText(hwnd)
                wclass = win32gui.GetClassName(hwnd)
                # Match by title (English "Save As"/"Save") OR by window class #32770
                # (the standard common dialog class — works on any locale)
                is_save = (
                    re.match(r"^Save(\s|$)", title, re.IGNORECASE)
                    or (wclass == "#32770" and any(kw in title.lower() for kw in ("save", "speichern", "enregistrer", "guardar")))
                )
                if is_save:
                    dialog_hwnd = hwnd
                    dialog_title = title
                    break
                time.sleep(0.2)

            if not dialog_hwnd:
                print(f"[HUMAN GUI AGENT] Save dialog never appeared. Last active: '{title}'")
                return "SAVE_DIALOG_NOT_FOUND"

            print(f"[HUMAN GUI AGENT] Save dialog detected: '{dialog_title}'")

            # Ensure the dialog is the active window before keystrokes
            try:
                win32gui.SetForegroundWindow(dialog_hwnd)
                time.sleep(0.2)
            except Exception as e:
                print(f"[HUMAN GUI AGENT] Could not raise dialog (non-fatal): {e}")

            # ── Step 2: Put full path into the File name field ─────────────────────
            # NOTE: UIA ValuePattern is NOT used here. The Save-As dialog's file-name
            # control rejects ValuePattern.SetValue ("operation canceled by the user")
            # on Win11, and a failed attempt disturbs the working clipboard path. So
            # this dialog stays clipboard-first; UIA is used only for the app's own
            # editor surface in ghost_type (where it is reliable).
            #
            # Prefer clipboard paste: pywinauto send_keys can corrupt '\\' in Windows
            # paths (Notepad then validates a bogus path like C:\\Kaustav\\Desktop\\...).
            prev_clip = None
            used_clipboard = False
            # Retry OpenClipboard up to 5 times — another app may hold the lock briefly
            for _clip_attempt in range(5):
                try:
                    win32clipboard.OpenClipboard()
                    try:
                        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                            prev_clip = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                    except Exception:
                        prev_clip = None
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, absolute_path)
                    used_clipboard = True
                    print("[HUMAN GUI AGENT] Ghost Save using clipboard paste for path.")
                    break
                except Exception as e:
                    if _clip_attempt < 4:
                        time.sleep(0.1)  # short retry delay
                    else:
                        print(f"[HUMAN GUI AGENT] Clipboard failed after 5 attempts, using key fallback: {e}")
                finally:
                    try:
                        win32clipboard.CloseClipboard()
                    except Exception:
                        pass

            try:
                keyboard.send_keys("^a", pause=0.05)
                time.sleep(0.15)
                if used_clipboard:
                    keyboard.send_keys("^v", pause=0.05)
                else:
                    safe_path = (
                        absolute_path.replace("{", "{{}").replace("}", "{}}").replace("\\", "{\\}")
                    )
                    keyboard.send_keys(safe_path, with_spaces=True, pause=0.005)
                time.sleep(0.3)
                keyboard.send_keys("{ENTER}")
                time.sleep(1.0)
            except Exception as e:
                if used_clipboard:
                    try:
                        win32clipboard.OpenClipboard()
                        win32clipboard.EmptyClipboard()
                        if prev_clip is not None:
                            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, prev_clip)
                    except Exception:
                        pass
                    finally:
                        try:
                            win32clipboard.CloseClipboard()
                        except Exception:
                            pass
                return f"ERROR: keyboard injection failed: {e}"

            # ── Step 3 / 4 (clipboard restored in inner finally) ─────────────────────
            try:
                # ── Step 3: Handle "Confirm Save As" overwrite popup ───────────────
                for _ in range(5):
                    fg_title = win32gui.GetWindowText(win32gui.GetForegroundWindow())
                    if re.search(r"Confirm Save As|Replace|already exists", fg_title, re.IGNORECASE):
                        if force_overwrite:
                            print("[HUMAN GUI AGENT] Overwrite popup detected — pressing Y.")
                            keyboard.send_keys("y")
                            time.sleep(0.6)
                        else:
                            print("[HUMAN GUI AGENT] Overwrite popup detected — pressing N.")
                            keyboard.send_keys("n")
                            return "FILE_EXISTS"
                        break
                    time.sleep(0.2)

                # ── Step 4: Verify the file actually landed on disk ────────────────
                base_name, ext = os.path.splitext(filename)
                alt_candidates = [
                    os.path.join(target_dir, filename),
                    os.path.join(target_dir, f"{base_name}.txt"),
                    os.path.join(target_dir, f"{base_name}{ext}"),
                ]
                seen = set()
                alt_candidates = [p for p in alt_candidates if not (p in seen or seen.add(p))]

                for _ in range(30):  # ~7.5 s
                    if os.path.exists(absolute_path):
                        print(f"[HUMAN GUI AGENT] File verified on disk → {absolute_path}")
                        return "SUCCESS"

                    for candidate in alt_candidates:
                        if os.path.exists(candidate):
                            print(f"[HUMAN GUI AGENT] File verified via candidate path → {candidate}")
                            return "SUCCESS"

                    try:
                        if os.path.isdir(target_dir):
                            now = time.time()
                            for entry in os.listdir(target_dir):
                                full = os.path.join(target_dir, entry)
                                if not os.path.isfile(full):
                                    continue
                                entry_stem, entry_ext = os.path.splitext(entry)
                                # EXACT filename match (case-insensitive) — high confidence
                                if entry.lower() == filename.lower():
                                    if now - os.path.getmtime(full) <= 30:
                                        print(f"[HUMAN GUI AGENT] File verified by exact name match → {full}")
                                        return "SUCCESS"
                                # Same stem, different extension (e.g. Notepad appended .txt)
                                # — only within 5s to reduce false-positive window
                                elif entry_stem.lower() == base_name.lower() and now - os.path.getmtime(full) <= 5:
                                    print(f"[HUMAN GUI AGENT] File verified by stem+ext match → {full}")
                                    return "SUCCESS"
                    except Exception:
                        pass

                    time.sleep(0.25)

                return f"ERROR: Save dialog closed but file not found at {absolute_path}"
            finally:
                if used_clipboard:
                    try:
                        win32clipboard.OpenClipboard()
                        win32clipboard.EmptyClipboard()
                        if prev_clip is not None:
                            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, prev_clip)
                    except Exception:
                        pass
                    finally:
                        try:
                            win32clipboard.CloseClipboard()
                        except Exception:
                            pass
        except Exception as e:
            print(f"[HUMAN GUI AGENT] Ghost Save Exception: {e}")
            return f"ERROR: {str(e)}"

    # --- 4. Coordinate Mapping & Optimization Strategy ---
    
    def process_screenshot(self, image) -> tuple:
        """
        Hardware-Optimized Vision Pipeline.
        1. Resizes image to max 1024px on longest edge to save CPU.
        2. Draws a 10x10 coordinate grid.
        3. Compresses to JPEG quality 50.
        Returns: (base64_string, scale_factor_x, scale_factor_y)
        """
        orig_width, orig_height = image.size
        
        # Calculate resize ratio (max 1024px)
        max_edge = 1024
        ratio = min(max_edge / orig_width, max_edge / orig_height)
        new_width = int(orig_width * ratio)
        new_height = int(orig_height * ratio)
        
        # Resize image
        image = image.resize((new_width, new_height)) # default bicubic is fine
        
        # Draw grid on resized image
        draw = ImageDraw.Draw(image)
        x_step = new_width // 10
        y_step = new_height // 10
        line_color = (255, 0, 0, 128) 
        
        try:
            font = ImageFont.truetype("arial.ttf", 14)
        except IOError:
            font = ImageFont.load_default()

        # Vertical
        for i in range(1, 10):
            x = i * x_step
            draw.line([(x, 0), (x, new_height)], fill=line_color, width=1)
            draw.text((x + 2, 5), f"X:{x}", fill=(255, 0, 0), font=font)

        # Horizontal
        for i in range(1, 10):
            y = i * y_step
            draw.line([(0, y), (new_width, y)], fill=line_color, width=1)
            draw.text((5, y + 2), f"Y:{y}", fill=(255, 0, 0), font=font)
            
        # Intersections
        for i in range(1, 10):
            for j in range(1, 10):
                x = i * x_step
                y = j * y_step
                draw.text((x + 2, y + 2), f"({x},{y})", fill=(0, 255, 255), font=font)

        # Compress to memory buffer (quality 50)
        buffered = BytesIO()
        image.save(buffered, format="JPEG", quality=50)
        base64_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        # We need the inverse scaling factor to convert LLM (1024px) coords back to raw (1080p) coords
        scale_x = orig_width / new_width
        scale_y = orig_height / new_height
        
        return base64_str, scale_x, scale_y

    # --- 3. Internal-First Execution Engine ---
    # Tries to solve tasks with Python os/shutil/subprocess before waking the vision loop.

    _VISUAL_SIGNALS = frozenset([
        "click", "right-click", "double-click", "drag", "hover over",
        "the button", "the checkbox", "dropdown", "context menu",
        "navigate through", "scroll to find", "in the dialog",
    ])
    _WRITE_SIGNALS = frozenset([
        "save", "write", "create a file", "create a note", "make a note",
        "make a list", "save it to", "save to", "write to", "generate and save",
    ])
    _LOCATION_SIGNALS = frozenset(["desktop", "documents", "downloads", "pictures"])
    _FOLDER_MAP = {
        "desktop":   os.path.join(os.path.expanduser("~"), "Desktop"),
        "documents": os.path.join(os.path.expanduser("~"), "Documents"),
        "downloads": os.path.join(os.path.expanduser("~"), "Downloads"),
        "pictures":  os.path.join(os.path.expanduser("~"), "Pictures"),
    }

    def _internal_write_file(self, task: str) -> dict:
        """
        Generates the file content via a fast LLM call and writes it directly
        to disk using Python's open(). No mouse, no screenshots, no GUI.
        Returns: {"success": bool, "message": str} or {"success": False, "reason": str}

        Context-Lite design: sends ONLY the task description — no history, no system facts.
        The heavy Brain model handles those; this 8B call only needs to generate file content.
        """
        if not self.vision_client:
            return {"success": False, "reason": "no_api_client"}

        # Context-Lite prompt: minimal tokens in, full content out.
        # Do NOT add conversation history, system facts, or persona context here —
        # those belong to the main Brain (70B/90B). Keep this call lean to avoid TPM limits.
        extraction_prompt = (
            f'Task: "{task}"\n\n'
            'Output ONLY a JSON object with three keys:\n'
            '"content": the complete text to write — escape ALL newlines as \\n (do NOT include literal line breaks in the JSON),\n'
            '"filename": a short clean filename with extension (e.g. poem.txt, notes.txt),\n'
            '"directory": exactly one of Desktop, Documents, Downloads, Pictures\n\n'
            'Output raw JSON only. No markdown. No explanation. No trailing commentary.'
        )

        # Uses shared key pool + 429 rotation (same as brain / memory pipelines).
        parsed = None
        try:
            completion = run_with_key_rotation(
                lambda c: c.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": extraction_prompt}],
                    temperature=0.4,
                    max_tokens=512,
                )
            )
            raw = completion.choices[0].message.content.strip()
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

            # Tier 1: strict parse.
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                pass

            # Tier 2: lenient parse — accepts unescaped control characters in strings
            if parsed is None:
                try:
                    parsed = json.loads(raw, strict=False)
                except json.JSONDecodeError:
                    pass

            # Tier 3: regex extraction — last resort for severely malformed JSON.
            if parsed is None:
                content_m = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.DOTALL)
                filename_m = re.search(r'"filename"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.DOTALL)
                directory_m = re.search(r'"directory"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.DOTALL)
                if content_m:
                    raw_content = content_m.group(1)
                    raw_content = (
                        raw_content.replace("\\n", "\n")
                        .replace("\\t", "\t")
                        .replace('\\"', '"')
                        .replace("\\\\", "\\")
                    )
                    parsed = {
                        "content": raw_content,
                        "filename": filename_m.group(1) if filename_m else "jarvis_output.txt",
                        "directory": directory_m.group(1) if directory_m else "Desktop",
                    }
                    print(
                        "[HUMAN GUI AGENT] JSON parse fell through to regex extraction "
                        "(LLM produced unescaped output)."
                    )

            if parsed is None:
                raise json.JSONDecodeError("All parse tiers failed", raw, 0)

        except Exception as e:
            print(f"[HUMAN GUI AGENT] Content extraction LLM call failed: {e}")
            return {"success": False, "reason": f"llm_extraction_failed: {e}"}

        content      = parsed.get("content", "").strip()
        filename     = parsed.get("filename", "jarvis_output.txt").strip() or "jarvis_output.txt"
        dir_key      = parsed.get("directory", "Desktop").strip().lower()

        if not content:
            return {"success": False, "reason": "empty_content_generated"}

        resolved_dir = self._FOLDER_MAP.get(dir_key, self._FOLDER_MAP["desktop"])
        full_path    = os.path.join(resolved_dir, filename)

        try:
            os.makedirs(resolved_dir, exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"[HUMAN GUI AGENT] Direct write success → {full_path}")
            return {
                "success": True,
                "message": f"File '{filename}' written directly to {resolved_dir} via Python I/O. No visual loop needed.",
            }
        except PermissionError as e:
            return {"success": False, "reason": f"permission_denied: {e}"}
        except Exception as e:
            return {"success": False, "reason": f"write_error: {e}"}

    def _internal_launch_app(self, task: str) -> dict:
        """
        Extracts the app name from a simple launch task and fires it with subprocess.
        Only called for short, launch-only tasks with no follow-up work.
        """
        task_lower = task.lower()
        app_name = None
        for verb in ("open ", "launch ", "start "):
            if verb in task_lower:
                remainder = task_lower.split(verb, 1)[1].strip()
                app_name  = remainder.split()[0] if remainder else None
                break

        if not app_name:
            return {"success": False, "reason": "could_not_extract_app_name"}

        try:
            subprocess.Popen(f'start "" "{app_name}"', shell=True)
            print(f"[HUMAN GUI AGENT] App '{app_name}' launched via subprocess.")
            return {"success": True, "message": f"'{app_name}' launched via subprocess."}
        except Exception as e:
            return {"success": False, "reason": f"launch_error: {e}"}

    def _attempt_internal_execution(self, task: str) -> dict:
        """
        Internal-First Gate — the decision router.

        Decision tree:
          1. If task contains explicit visual-action keywords → skip immediately (not_applicable).
          2. If task has file-write intent + a known directory → call _internal_write_file.
          3. If task is a short, standalone app-launch command → call _internal_launch_app.
          4. Otherwise → not_applicable, hand off to vision loop.

        Returns:
          {"success": True,  "message": "..."}            — resolved internally
          {"success": False, "reason": "not_applicable"}  — must use vision loop
          {"success": False, "reason": "<error details>"} — tried internally, failed
        """
        task_lower = task.lower()

        # Fast-fail: explicit visual UI verbs → vision loop only
        if any(sig in task_lower for sig in self._VISUAL_SIGNALS):
            print("[HUMAN GUI AGENT] Visual signal detected — skipping internal path.")
            return {"success": False, "reason": "not_applicable"}

        # Path A: File write (needs both write intent AND a known directory)
        has_write_intent = any(sig in task_lower for sig in self._WRITE_SIGNALS)
        has_location     = any(sig in task_lower for sig in self._LOCATION_SIGNALS)

        if has_write_intent and has_location:
            print("[HUMAN GUI AGENT] File-write intent detected — attempting internal execution.")
            return self._internal_write_file(task)

        # Path B: Simple app launch (≤5 words, no combined operations)
        COMBINED_SIGNALS = ("and ", "then ", "type", "write", "save", "fill")
        is_short = len(task.split()) <= 5
        is_launch = any(task_lower.startswith(v) for v in ("open ", "launch ", "start "))

        if is_short and is_launch and not any(s in task_lower for s in COMBINED_SIGNALS):
            print("[HUMAN GUI AGENT] Simple launch task detected — attempting internal execution.")
            return self._internal_launch_app(task)

        return {"success": False, "reason": "not_applicable"}

    # --- 4. The Vision LLM System Prompt ---

    def get_system_prompt(self, task_description: str) -> str:
        return f"""You are J.A.R.V.I.S., physically operating a Windows PC. You are looking at a live screenshot of the user's monitor.
Your current goal is: {task_description}

Proceed step-by-step. One action per iteration. Do not attempt to do everything at once.

━━━ CORE RULES ━━━
1. FOCUS RULE: After opening an app or switching windows, you MUST move_mouse into the app's body area and click BEFORE typing. No focus = no keystrokes land.
2. HOTKEY RULE: Combine keys with "+": "ctrl+s", "win+r", "alt+tab".
3. NO HALLUCINATION: Only interact with elements you can visually confirm in the current screenshot. Never guess coordinates.

━━━ POPUP DISMISSAL PROTOCOL ━━━
Scan every screenshot for unexpected popups, ads, or system prompts (OneDrive, browser notifications, update dialogs).
If a popup is blocking your task: your IMMEDIATE next action MUST be to dismiss it ("No thanks", "Cancel", "Skip", or the "X" button). Do not proceed with the main task until the popup is gone.

━━━ APP LAUNCHING PROTOCOL (FOCUS DISCIPLINE) ━━━
To open any application, always use the Windows Start Menu — never win+r.
Iteration A: press_key "win"
Iteration B: Confirm Start Menu is open. type the app name (e.g., "Notepad").
Iteration C: press_key "enter"
CRITICAL: Once a menu or search bar is open, complete the full interaction (type + enter) before pressing any other meta-key. Do NOT rapidly switch between keyboard shortcuts.

━━━ THE 'SAVE AS' PROTOCOL — 4 STRICT OPERATING CONSTRAINTS ━━━

CONSTRAINT 1 — FOCUS-CLICK NAVIGATION:
Before scrolling the left navigation pane, you MUST click on a neutral empty area WITHIN that pane to give it active Windows focus. Without this click, scroll events are silently ignored by Windows.
Mandatory sequence: move_mouse to center of left folder pane → click (focus) → THEN scroll.

CONSTRAINT 2 — ADDRESS BAR TELEPORTATION (after 2 failed scroll attempts):
If the target folder (e.g., "Desktop") is NOT visible after 2 scroll attempts, STOP scrolling.
Execute this escape sequence immediately:
  a) move_mouse to the address bar at the TOP of the Save As dialog.
  b) click (triple-click to select the current path text).
  c) type the target folder name (e.g., "Desktop") or its full absolute path.
  d) press_key "enter" — the dialog will navigate there instantly.

CONSTRAINT 3 — FILENAMING DISCIPLINE (STRICTLY ENFORCED):
When you reach the "File name:" input box, type ONLY a short, clean 1-2 word filename with a file extension.
Examples: "poem.txt", "notes.txt", "groceries.txt", "code.py"
IT IS STRICTLY FORBIDDEN to type any document content, body text, or quotes from the file into the File Name box.
One short filename. Nothing else.

CONSTRAINT 4 — VERIFICATION CHECK BEFORE SAVING:
Before clicking the final "Save" button, READ the address bar and CONFIRM the folder name displayed matches the intended target directory.
If the address bar shows the WRONG folder: do NOT click Save. Use Constraint 2 to navigate to the correct location first.
Only click "Save" after visual confirmation of the correct folder.

━━━ TASK VERIFICATION ━━━
Do NOT output task_complete immediately after pressing Enter or clicking Save. Wait one full iteration to verify the Save dialog has closed and the operation succeeded. Confirm by checking the title bar or directory state.

━━━ EFFICIENCY RULE ━━━
Never type long content word-by-word. Format ALL body text (lists, paragraphs, code) into a SINGLE "type" action using \\n for line breaks.

━━━ VALID ACTIONS ━━━
Output strictly JSON. Each iteration outputs exactly ONE action.
- {{"action": "move_mouse", "x": 100, "y": 200}}
- {{"action": "click", "clicks": 1}}
- {{"action": "type", "text": "hello\\nworld"}}
- {{"action": "press_key", "key": "win"}}
- {{"action": "scroll", "amount": -500}}
- {{"action": "task_complete", "message": "Describe what was accomplished."}}

Estimate X/Y coordinates from the red coordinate grid overlaid on the screenshot.
"""

    # --- 5. The Observation-Action Loop ---

    def execute_autonomous_task(self, task_description: str, vision_llm_client) -> str:
        """
        Entry point for all autonomous PC tasks. Implements a two-path strategy:

        PATH A — Internal Execution (preferred):
          Resolves the task directly with Python os/shutil/subprocess if possible.
          Zero screenshots. Zero mouse movement. Instant and reliable.

        PATH B — Visual Loop (fallback):
          Activates the screenshot → Vision LLM → action loop for tasks that
          genuinely require interacting with a visible UI element.
        """
        print(f"[HUMAN GUI AGENT] Received task: '{task_description}'")

        # ── PATH A: Internal-First Gate ─────────────────────────────────────────
        internal = self._attempt_internal_execution(task_description)
        if internal.get("success"):
            print("[HUMAN GUI AGENT] Task resolved internally. Vision loop not needed.")
            return f"Task completed directly: {internal['message']}"

        reason = internal.get("reason", "unknown")
        if reason == "not_applicable":
            print("[HUMAN GUI AGENT] No internal path applicable. Engaging vision loop.")
        else:
            print(f"[HUMAN GUI AGENT] Internal path attempted but failed ({reason}). Vision loop is the fallback.")

        # ── PATH B: Vision Loop ──────────────────────────────────────────────────
        print(f"[HUMAN GUI AGENT] Starting vision loop for: '{task_description}'")
        iteration = 0
        max_iterations = 15
        action_history = []

        while iteration < max_iterations:
            print(f"[HUMAN GUI AGENT] --- Iteration {iteration + 1} / {max_iterations} ---")
            
            # --- Vision Agent Fail-Safe (The Handoff) ---
            try:
                hwnd = win32gui.GetForegroundWindow()
                if hwnd:
                    active_title = win32gui.GetWindowText(hwnd)
                    if active_title in ["Save As", "Save as"]:
                        print("[HUMAN GUI AGENT] Save As dialog detected. Aborting visual loop.")
                        return "Task completed successfully: Save dialog summoned. Handoff to Ghost Protocol."
            except Exception as e:
                print(f"[HUMAN GUI AGENT] Fail-Safe Error: {e}")
            
            # a. Take screenshot (all monitors — apps may be on secondary displays)
            screenshot = ImageGrab.grab(all_screens=True)
            
            # Draw grid, resize, compress, and get scale factors
            base64_img, scale_x, scale_y = self.process_screenshot(screenshot)
            
            # b. Send to Vision LLM
            prompt = self.get_system_prompt(task_description)
            
            print("[HUMAN GUI AGENT] Analyzing screen...")
            try:
                # Actual Vision API call
                response = vision_llm_client(base64_img, prompt, action_history)
                
                # Check if the response indicates a rate limit error (based on my call_vision_api implementation)
                if isinstance(response, dict) and "API Error" in response.get("message", "") and "429" in response.get("message", ""):
                     print("[HUMAN GUI AGENT] Rate limit hit. Pausing for 10 seconds...")
                     time.sleep(10)
                     continue # Retry this exact iteration
                
            except Exception as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    print("[HUMAN GUI AGENT] Rate limit hit. Pausing for 10 seconds...")
                    time.sleep(10)
                    continue # Retry this exact iteration
                    
                print(f"[HUMAN GUI AGENT] Vision LLM Error: {e}")
                return "Sir, my visual processors encountered an error."
                
            # c. Parse JSON response
            if not isinstance(response, dict) or "action" not in response:
                print(f"[HUMAN GUI AGENT] Invalid response format: {response}")
                break
                
            action = response.get("action")
            action_history.append(response)
            
            # d. Execute physical action
            try:
                if action == "move_mouse":
                    # Scale LLM coordinates back up to physical screen resolution
                    target_x = int(float(response.get("x", 0)) * scale_x)
                    target_y = int(float(response.get("y", 0)) * scale_y)
                    self.move_mouse(target_x, target_y)
                elif action == "click":
                    self.click_mouse(int(response.get("clicks", 1)))
                elif action == "type":
                    self.type_text(response.get("text", ""))
                elif action == "press_key":
                    self.press_key(response.get("key", "enter"))
                elif action == "scroll":
                    self.scroll(int(response.get("amount", -500)))
                elif action == "task_complete":
                    print(f"[HUMAN GUI AGENT] Task Complete: {response.get('message')}")
                    return f"Task completed successfully: {response.get('message', '')}"
                else:
                    print(f"[HUMAN GUI AGENT] Unknown action: {action}")
            except pyautogui.FailSafeException:
                return "Sir, the manual failsafe was triggered. I have aborted the task."
            except Exception as e:
                print(f"[HUMAN GUI AGENT] Execution error: {e}")
                
            # e. Wait 2 seconds for UI to render (increased for reliability)
            time.sleep(2.0)
            iteration += 1
            
        # Vision loop exhausted without task_complete signal
        return "Sir, I have hit my iteration limit for this task without a confirmed completion. The visual loop is handing off — shall I continue or abort?"

    # --- 6. The Real Vision API Caller ---
    
    def call_vision_api(self, base64_img: str, prompt: str, history: list) -> dict:
        """
        Calls Groq Llama 3.2 Vision using the standard OpenAI/Groq message format.
        Forces JSON output using response_format.
        """
        if not self.vision_client:
            print("[HUMAN GUI AGENT] ERROR: Vision client not initialized. Missing API Key?")
            return {"action": "task_complete", "message": "API Key missing."}
            
        print(f"[HUMAN GUI AGENT] Sending payload to {self.model_id} (Groq)...")
        
        # Prepare the history context
        history_str = json.dumps(history, indent=2) if history else "None"
        full_prompt = f"{prompt}\n\nPREVIOUS ACTIONS TAKEN:\n{history_str}\n\nWhat is your NEXT single action? RETURN ONLY VALID JSON."
        
        try:
            response = run_with_key_rotation(
                lambda c: c.chat.completions.create(
                    model=self.model_id,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": full_prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{base64_img}",
                                    },
                                },
                            ],
                        }
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                )
            )

            return json.loads(response.choices[0].message.content)
        except Exception as e:
            # Re-raise for the retry logic in execute_autonomous_task to handle
            raise e

    # --- 7. Stage 3 One-Shot Test ---
    
    def test_one_shot_vision(self, task_description: str):
        """
        Bypasses the 15-iteration loop to test a single API call and coordinate scaling.
        """
        print(f"\n--- STAGE 3: ONE-SHOT VISION TEST ---")
        print(f"Task: {task_description}")
        
        screenshot = ImageGrab.grab(all_screens=True)
        base64_img, scale_x, scale_y = self.process_screenshot(screenshot)
        
        prompt = self.get_system_prompt(task_description)
        
        response = self.call_vision_api(base64_img, prompt, [])
        print(f"[HUMAN GUI AGENT] Gemini Raw JSON Response: {json.dumps(response, indent=2)}")
        
        action = response.get("action")
        if action == "move_mouse":
            target_x = int(float(response.get("x", 0)) * scale_x)
            target_y = int(float(response.get("y", 0)) * scale_y)
            print(f"[HUMAN GUI AGENT] Math: Gemini ({response.get('x')}, {response.get('y')}) * Scale ({scale_x:.3f}, {scale_y:.3f}) = Physical ({target_x}, {target_y})")
            self.move_mouse(target_x, target_y)
            print("[SUCCESS] Mouse moved to scaled coordinates!")
        else:
            print(f"[HUMAN GUI AGENT] Action was not move_mouse. It was: {action}")
