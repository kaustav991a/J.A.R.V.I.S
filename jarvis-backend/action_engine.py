import subprocess
import os
import shutil
import webbrowser
from pathlib import Path
from pydantic import BaseModel, ValidationError
import memory 
from ddgs import DDGS 

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
        elif action == "remember_fact":
            return self._remember_fact(target)
        elif action == "web_search": 
            return self._web_search(target)
        elif action == "web_search_image": # NEW: Routes the image search
            return self._web_search_image(target)
        elif action == "open_link": 
            return self._open_link(target)
        elif action == "close_display": # NEW: Handles the UI dismiss
            return "Display clear command received."
        else:
            return f"I'm afraid I don't know how to perform the action '{action}', sir."

    # --- WEB NAVIGATION ---
    def _open_link(self, url: str) -> str:
        """Opens the system's default browser to the specified URL."""
        print(f"[ACTION ENGINE] Navigating to: {url}")
        try:
            # Basic validation to ensure it's a URL
            if not url.startswith("http"):
                url = f"https://{url}"
            
            webbrowser.open(url)
            return "Opening the requested page now, sir."
        except Exception as e:
            return f"I encountered a glitch while opening the browser, sir. Error: {e}"

    # --- SEARCH PROTOCOLS ---
    def _web_search(self, query: str) -> str:
        """Accesses the web and returns both snippets and URLs."""
        print(f"[ACTION ENGINE] Initiating research for: {query}")
        try:
            results = []
            with DDGS() as ddgs:
                # We fetch the top 3 results
                search_data = ddgs.text(query, max_results=3)
                for r in search_data:
                    results.append(f"URL: {r.get('href', '')} | Data: {r.get('body', '')}")
            
            if not results:
                return "I searched the global archives, sir, but found no relevant data."
            
            return "\n".join(results)
            
        except Exception as e:
            return f"I encountered an error during research, sir. Error: {e}"

    def _web_search_image(self, query: str) -> dict:
        """Fetches the top image URL for a given query using DDGS."""
        print(f"[ACTION ENGINE] Initiating image search for: {query}")
        try:
            with DDGS() as ddgs:
                # Fetch up to 5 results to ensure we get at least one valid hit
                results = list(ddgs.images(query, max_results=5))
                
                print(f"[ACTION ENGINE] Raw Image Results: {len(results)} found.")
                
                for r in results:
                    # Library updates frequently change the key name between 'image' and 'url'
                    image_url = r.get('image') or r.get('url') 
                    
                    if image_url:
                        title = r.get('title', query)
                        print(f"[ACTION ENGINE] Valid Image Found: {image_url}")
                        return {"success": True, "url": image_url, "title": title}
                
                print("[ACTION ENGINE] Search completed, but no valid image links were found in the data.")
                return {"success": False, "error": "No valid image URLs found."}
                
        except Exception as e:
            print(f"[ACTION ENGINE] Image search failed: {e}")
            return {"success": False, "error": str(e)}

    # --- PERSISTENT MEMORY STORAGE ---
    def _remember_fact(self, target: str) -> str:
        try:
            if ":" in target:
                category, fact = target.split(":", 1)
                category = category.strip()
                fact = fact.strip()
            else:
                category = "General"
                fact = target.strip()
            
            memory.remember_fact(category, fact)
            return f"Of course, sir. I've committed that to my long-term memory under {category}."
        except Exception as e:
            return f"I had a bit of trouble storing that information, sir. Error: {e}"

    # --- OS ACTIONS ---
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