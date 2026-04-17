import subprocess
import os
import shutil
from pathlib import Path
from pydantic import BaseModel, ValidationError
import memory # THE LINK: Import your SQLite memory system

# 1. The Pydantic Firewall
class ActionIntent(BaseModel):
    action_type: str
    target: str

class ActionEngine:
    def __init__(self):
        # The App Registry
        self.app_registry = {
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "spotify": "spotify.exe", 
            "chrome": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            "code": "code" 
        }

        # The Security Fence
        self.restricted_folders = [
            Path("C:/Windows").resolve(),
            Path("C:/Program Files").resolve(),
            Path("C:/Program Files (x86)").resolve()
        ]

    def execute(self, payload: dict) -> str:
        print(f"[ACTION ENGINE] Processing payload: {payload}")
        
        try:
            intent = ActionIntent(**payload)
        except ValidationError:
            return "Validation Error: I generated an invalid command structure, sir."

        action = intent.action_type.lower()
        target = intent.target

        # --- ROUTING TABLE ---
        if action == "launch_app":
            return self._launch_app(target)
        elif action == "close_app":
            return self._close_app(target)
        elif action == "delete_file":
            return self._delete_file(target)
        elif action == "remember_fact": # THE NEW ACTION
            return self._remember_fact(target)
        else:
            return f"I'm afraid I don't know how to perform the action '{action}', sir."

    # --- NEW: PERSISTENT MEMORY STORAGE ---
    def _remember_fact(self, target: str) -> str:
        """Parses the 'Category: Fact' format and saves to SQLite."""
        try:
            if ":" in target:
                category, fact = target.split(":", 1)
                category = category.strip()
                fact = fact.strip()
            else:
                category = "General"
                fact = target.strip()
            
            # Write to the Tier 2 Database
            memory.remember_fact(category, fact)
            return f"Of course, sir. I've committed that to my long-term memory under {category}."
            
        except Exception as e:
            return f"I had a bit of trouble storing that information, sir. Error: {e}"

    # --- EXISTING OS ACTIONS ---
    def _launch_app(self, app_name: str) -> str:
        app_name = app_name.lower()
        exe_path = self.app_registry.get(app_name)
        if not exe_path:
            return f"I do not have {app_name} registered in my database, sir."
        
        try:
            subprocess.Popen(exe_path)
            return f"Launching {app_name} now."
        except Exception as e:
            return f"Failed to launch {app_name}."

    def _close_app(self, app_name: str) -> str:
        app_name = app_name.lower()
        try:
            exe_name = self.app_registry.get(app_name, f"{app_name}.exe")
            if "\\" in exe_name or "/" in exe_name:
                exe_name = Path(exe_name).name
            os.system(f'taskkill /IM {exe_name} /F')
            return f"Task terminated. {app_name} is closed."
        except Exception as e:
            return f"I couldn't close {app_name}, sir."

    def _delete_file(self, target_path: str) -> str:
        try:
            path = Path(target_path).resolve()
            for restricted in self.restricted_folders:
                if restricted in path.parents or path == restricted:
                    return "Security override triggered. I cannot modify system directories."
            
            if path.is_file():
                path.unlink()
                return "File successfully deleted, sir."
            elif path.is_dir():
                shutil.rmtree(path)
                return "Directory removed."
            else:
                return "I couldn't find that specific target on the file system."
        except Exception as e:
            return f"Deletion protocol failed: {e}"