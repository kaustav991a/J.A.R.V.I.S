"""
Phase 9: GUI Automation Agent
Provides true "Computer Use" capabilities via PyAutoGUI.
Translates LLM intents into physical mouse/keyboard actions.
"""
import time
import pyautogui
from PIL import ImageGrab

# Core Setup
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.5  # Standard delay between actions

class GUIAgent:
    def __init__(self):
        print("[GUI AGENT] Initialized.")

    def get_element_coordinates(self, target_description: str, screenshot) -> tuple:
        """
        Vision-to-Coordinate Pipeline.
        MOCK IMPLEMENTATION: Currently returns None.
        In the future, this will send the screenshot to a Vision API (like GPT-4o or Gemini)
        to return the exact (X, Y) bounding box of the requested target.
        """
        print(f"[GUI AGENT] Scanning screen for '{target_description}'...")
        # For now, we mock the failure to find it on screen to force the Tier 3 fallback.
        return None

    def execute_gui_action(self, action_type: str, target: str = "") -> str:
        """Main execution router for GUI actions."""
        action_type = action_type.lower()
        print(f"[GUI AGENT] Executing: {action_type} on '{target}'")
        
        try:
            # --- Tier 3: The Windows Search Fallback (CRITICAL) ---
            if action_type == "smart_open_app":
                return self._smart_open_app(target)
                
            # --- Keyboard Actions ---
            if action_type == "keyboard_type":
                if not target:
                    return "No text provided to type."
                pyautogui.write(target, interval=0.05)
                return f"Typed '{target}' successfully."
                
            if action_type == "keyboard_press":
                if not target:
                    return "No key provided to press."
                pyautogui.press(target.lower())
                return f"Pressed '{target}'."
                
            # --- Tier 1 & 2: Mouse Actions (Vision Dependent) ---
            if action_type in ["mouse_click", "mouse_double_click"]:
                return self._vision_click_loop(action_type, target)
                
            if action_type == "mouse_scroll":
                # Expected target: "down 500" or "up 200"
                parts = target.split()
                if len(parts) >= 2:
                    direction = parts[0].lower()
                    try:
                        amount = int(parts[1])
                        if direction == "down":
                            amount = -amount # PyAutoGUI uses negative for scroll down
                        pyautogui.scroll(amount)
                        return f"Scrolled {direction} by {abs(amount)}."
                    except ValueError:
                        return "Invalid scroll amount provided."
                return "Scroll direction or amount missing."
                
            return f"Unknown GUI action: {action_type}"
            
        except pyautogui.FailSafeException:
            return "GUI Failsafe triggered. Action aborted."
        except Exception as e:
            print(f"[GUI AGENT] Error: {e}")
            return f"Failed to execute GUI action: {e}"

    def _vision_click_loop(self, action_type: str, target: str) -> str:
        """Tier 2: The Scroll-and-Search Loop."""
        max_attempts = 3
        
        for attempt in range(max_attempts):
            screenshot = ImageGrab.grab()
            coords = self.get_element_coordinates(target, screenshot)
            
            if coords:
                x, y = coords
                pyautogui.moveTo(x, y, duration=0.5)
                if action_type == "mouse_double_click":
                    pyautogui.doubleClick()
                else:
                    pyautogui.click()
                return f"Successfully clicked '{target}' at ({x}, {y})."
                
            print(f"[GUI AGENT] Attempt {attempt + 1}: '{target}' not found. Scrolling...")
            # Scroll down to see if it's further down the page
            pyautogui.scroll(-500)
            time.sleep(1.0)
            
        return f"Failed to locate '{target}' on screen after {max_attempts} attempts."

    def _smart_open_app(self, target: str) -> str:
        """
        Tier 3: The Windows Search Fallback macro.
        Bypasses brittle file paths by using native human-like OS interaction.
        """
        print(f"[GUI AGENT] Triggering Windows Search for: {target}")
        
        try:
            # 1. Press Windows Key
            pyautogui.press('win')
            time.sleep(0.8) # Wait for menu to open
            
            # 2. Type target
            pyautogui.write(target, interval=0.05)
            
            # 3. Wait for search indexing
            time.sleep(1.5)
            
            # 4. Press Enter
            pyautogui.press('enter')
            
            return f"Used Windows Search to open '{target}'."
            
        except Exception as e:
            return f"Smart Open failed: {e}"
