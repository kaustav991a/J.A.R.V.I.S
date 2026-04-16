import subprocess
import os
import shutil
from pathlib import Path
from pydantic import BaseModel, ValidationError

# 1. The Pydantic Firewall: Forces strict JSON structure
class ActionIntent(BaseModel):
    action_type: str
    target: str

class ActionEngine:
    def __init__(self):
        # 2. The App Registry
        # Map simple spoken names to their actual Windows executables or paths.
        # You will need to update the paths here for specific apps on your PC!
        self.app_registry = {
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "spotify": "spotify.exe", 
            "chrome": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            "code": "code" # VS Code
        }

        # 3. The Security Fence
        # JARVIS will hard-deny any attempt to delete files inside these folders.
        self.restricted_folders = [
            Path("C:/Windows").resolve(),
            Path("C:/Program Files").resolve(),
            Path("C:/Program Files (x86)").resolve()
        ]

    def execute(self, payload: dict) -> str:
        print(f"[ACTION ENGINE] Processing payload: {payload}")
        
        try:
            # Validate the incoming dictionary matches our ActionIntent rules
            intent = ActionIntent(**payload)
        except ValidationError:
            return "Validation Error: J.A.R.V.I.S. generated an invalid command structure."

        # Route to the correct function based on the action_type
        action = intent.action_type.lower()
        target = intent.target

        if action == "launch_app":
            return self._launch_app(target)
        elif action == "close_app":
            return self._close_app(target)
        elif action == "delete_file":
            return self._delete_file(target)
        else:
            return f"Unknown action requested: {action}"

    def _launch_app(self, app_name: str) -> str:
        app_name = app_name.lower()
        exe_path = self.app_registry.get(app_name)
        
        if not exe_path:
            return f"I do not have {app_name} registered in my database, sir."
        
        try:
            # Popen runs the app in the background so J.A.R.V.I.S. doesn't freeze
            subprocess.Popen(exe_path)
            return f"Launching {app_name}."
        except Exception as e:
            return f"Failed to launch {app_name}. Error: {e}"

    def _close_app(self, app_name: str) -> str:
        app_name = app_name.lower()
        try:
            # Get the exact exe name from the registry, or guess it if missing
            exe_name = self.app_registry.get(app_name, f"{app_name}.exe")
            
            # If the registry holds a full path (like Chrome), extract just the .exe name
            if "\\" in exe_name or "/" in exe_name:
                exe_name = Path(exe_name).name
                
            # Windows command to forcefully kill a task
            os.system(f'taskkill /IM {exe_name} /F')
            return f"Closed {app_name}."
        except Exception as e:
            return f"Could not close {app_name}."

    def _delete_file(self, target_path: str) -> str:
        try:
            path = Path(target_path).resolve()
            
            # Security Check: Compare against restricted folders
            for restricted in self.restricted_folders:
                if restricted in path.parents or path == restricted:
                    return "SECURITY OVERRIDE: Access denied. Target is in a restricted system folder."
            
            if path.is_file():
                path.unlink()
                return "File deleted successfully."
            elif path.is_dir():
                shutil.rmtree(path)
                return "Directory removed."
            else:
                return "Target not found on the file system."
        except Exception as e:
            return f"Error during deletion: {e}"