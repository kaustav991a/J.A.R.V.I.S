import subprocess
import os
import shutil
import webbrowser
import json
import time
from pathlib import Path
from pydantic import BaseModel, ValidationError
import memory 
from ddgs import DDGS 
import platform

# --- TV Control & Network Imports ---
from zeroconf import Zeroconf, ServiceBrowser, ServiceStateChange
from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.auth.keygen import keygen

class ActionIntent(BaseModel):
    action_type: str
    target: str

class ActionEngine:
    def __init__(self):
        self.app_registry = {
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "spotify": "spotify.exe", 
            "chrome": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            "code": "code" 
        }

        self.restricted_folders = [
            Path("C:/Windows").resolve(),
            Path("C:/Program Files").resolve(),
            Path("C:/Program Files (x86)").resolve()
        ]
        
        # --- SMART HOME: DYNAMIC TV DETAILS ---
        self.tv_ip = "192.168.0.108" 
        self.tv_config_file = "tv_config.json"
        self.tv_port = self._load_tv_port()
        self.adb_device = None
        self.signer = self._get_adb_signer()

    def _load_tv_port(self) -> int:
        """Loads the last known TV port from cache."""
        try:
            with open(self.tv_config_file, "r") as f:
                data = json.load(f)
                return data.get("port", 5555)
        except (FileNotFoundError, json.JSONDecodeError):
            return 5555

    def _get_adb_signer(self):
        """Uses the proven FromRSAKeyPath method from your working script."""
        adbkey = 'adbkey'
        if not os.path.isfile(adbkey):
            keygen(adbkey)
        return PythonRSASigner.FromRSAKeyPath(adbkey)

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
        elif action == "web_search_image": 
            return self._web_search_image(target)
        elif action == "open_link": 
            return self._open_link(target)
        elif action == "close_display": 
            return "Display clear command received."
        elif action == "tv_control":
            return self._control_tv(target)
        elif action == "tv_type":
            return self._tv_type(target)
        elif action == "tv_search":
            return self._tv_search(target)
        elif action == "movie_protocol":
            return self._movie_protocol()
        else:
            return f"I'm afraid I don't know how to perform the action '{action}', sir."

    # --- SMART HOME: AUTONOMOUS TV CONNECTION ---
    def _sweep_for_tv(self) -> int:
        """Uses your proven working ZeroConf logic to find the active port."""
        print("[ACTION ENGINE] Searching for Android TV devices on network...")
        found_port = None

        def on_service_state_change(zeroconf, service_type, name, state_change):
            nonlocal found_port
            if state_change is ServiceStateChange.Added:
                info = zeroconf.get_service_info(service_type, name)
                if info and info.addresses:
                    ip = ".".join(map(str, info.addresses[0]))
                    if ip == self.tv_ip:
                        found_port = info.port
                        print(f"[+] Discovered TV: {name} at {ip}:{found_port}")

        zc = Zeroconf()
        browser = ServiceBrowser(zc, "_adb._tcp.local.", handlers=[on_service_state_change])
        
        # Proven 5-second wait time from your script
        time.sleep(5) 
        zc.close()
        return found_port

    def _connect_tv(self):
        """Attempts fast connection, falls back to radar sweep if port changed."""
        if self.adb_device and self.adb_device.available:
            return True

        print(f"[ACTION ENGINE] Attempting TV uplink at {self.tv_ip}:{self.tv_port}...")
        self.adb_device = AdbDeviceTcp(self.tv_ip, self.tv_port, default_transport_timeout_s=9.)
        
        try:
            # FAST PATH: Try the last known port (should be instant)
            self.adb_device.connect(rsa_keys=[self.signer])
            print("[+] Connected to TV instantly.")
            return True
        except Exception as e:
            print(f"[ACTION ENGINE] Primary connection failed: {e}. Launching Radar...")
            
            # SLOW PATH: Sweep the network using your working logic
            new_port = self._sweep_for_tv()
            
            if new_port:
                self.tv_port = new_port
                
                # Save it so the next command is fast again
                with open(self.tv_config_file, "w") as f:
                    json.dump({"port": new_port}, f)
                
                # Reconnect with new port
                self.adb_device = AdbDeviceTcp(self.tv_ip, self.tv_port, default_transport_timeout_s=9.)
                try:
                    self.adb_device.connect(rsa_keys=[self.signer])
                    print(f"[+] Reconnected to TV at {self.tv_ip}:{self.tv_port}")
                    return True
                except Exception as e2:
                    print(f"[!] Failed to connect after sweep: {e2}")
                    return False
            else:
                print("[!] No Android TV devices found broadcasting on that IP.")
                return False

    def _control_tv(self, command: str) -> str:
        print(f"[ACTION ENGINE] Initiating TV protocol: {command}")
        
        if not self._connect_tv():
            return "I am unable to reach the Dining Room TV, sir. It may be powered off."

        key_map = {
            "power": "26", "home": "3", "mute": "164",
            "volume_up": "24", "volume_down": "25", "play_pause": "85",
            "back": "4", "up": "19", "down": "20", "left": "21", "right": "22", "select": "66",
            "youtube": "am start -a android.intent.action.VIEW -d 'vnd.youtube://'",
            "netflix": "am start -n com.netflix.ninja/.MainActivity"
        }

        cmd = command.lower().strip()
        if cmd in key_map:
            try:
                action_code = key_map[cmd]
                if action_code.startswith("am start"):
                    self.adb_device.shell(action_code)
                    return f"Launching {cmd} on the Dining Room TV, sir."
                else:
                    self.adb_device.shell(f"input keyevent {action_code}")
                    return f"TV {cmd} command executed successfully."
            except Exception as e:
                return f"I encountered an error transmitting to the TV: {e}"
        else:
            return f"I don't have a protocol mapped for the TV command '{cmd}', sir."

    def _tv_type(self, text: str) -> str:
        """Injects text directly into the TV's active text box."""
        print(f"[ACTION ENGINE] Typing on TV: {text}")
        if not self._connect_tv(): return "I am unable to reach the TV, sir."
        
        # ADB requires spaces to be formatted as %s
        formatted_text = text.replace(" ", "%s")
        try:
            self.adb_device.shell(f"input text {formatted_text}")
            self.adb_device.shell("input keyevent 66") # Press Enter automatically
            return f"I have typed '{text}' on the screen, sir."
        except Exception as e:
            return f"Failed to input text: {e}"

    def _tv_search(self, query: str) -> str:
        """Bypasses menus to directly search YouTube."""
        print(f"[ACTION ENGINE] TV YouTube Search: {query}")
        if not self._connect_tv(): return "I am unable to reach the TV, sir."
        
        formatted_query = query.replace(" ", "+")
        try:
            self.adb_device.shell(f'am start -a android.intent.action.VIEW -d "vnd.youtube://results?search_query={formatted_query}"')
            return f"Pulling up YouTube results for {query}, sir."
        except Exception as e:
            return f"Search failed: {e}"

    def _movie_protocol(self) -> str:
        """A multi-step macro to set up the room."""
        print("[ACTION ENGINE] Executing Movie Protocol")
        if not self._connect_tv(): return "I am unable to reach the TV, sir."
        
        try:
            self.adb_device.shell("input keyevent 26") # Ensure TV is awake
            time.sleep(1.5) # Wait for OS to boot
            self.adb_device.shell("am start -n com.netflix.ninja/.MainActivity")
            
            # Blast the volume up a few notches
            for _ in range(3):
                self.adb_device.shell("input keyevent 24")
                time.sleep(0.2)
                
            return "Movie protocol engaged. TV awake, audio primed, and Netflix launched."
        except Exception as e:
            return f"Protocol interrupted: {e}"

    # --- NEW: TV STATUS INTERROGATOR ---
    def get_tv_status(self) -> dict:
        """Polls the TV for its current power state and active app without freezing the server."""
        import socket

        # 1. THE LIGHTNING CHECK: Only attempt a heavy connection if the port is physically open
        if not (self.adb_device and self.adb_device.available):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5) # Strict half-second timeout to prevent server freezing
            try:
                # Try to knock on the exact ADB port
                s.connect((self.tv_ip, self.tv_port))
                s.close()
                
                # If the knock was answered, the TV woke up! Do the heavy connection.
                if not self._connect_tv():
                    return {"status": "offline", "power": "off", "app": "none"}
            except Exception:
                # Port is closed. TV is asleep. Return instantly.
                return {"status": "offline", "power": "off", "app": "none"}

        # 2. We are fully connected. Ask the TV what it is doing.
        try:
            power_output = self.adb_device.shell("dumpsys power | grep -E 'mWakefulness=|mInteractive=|Display Power'")
            is_on = (
                "mWakefulness=Awake" in power_output or 
                "mInteractive=true" in power_output or 
                "state=ON" in power_output
            )

            if not is_on:
                return {"status": "online", "power": "off", "app": "none"}

            app_output = self.adb_device.shell("dumpsys window windows | grep -E 'mCurrentFocus'")
            current_app = "Unknown"

            if "com.netflix.ninja" in app_output:
                current_app = "Netflix"
            elif "com.google.android.youtube.tv" in app_output:
                current_app = "YouTube"
            elif "com.spotify.tv.android" in app_output:
                current_app = "Spotify"
            elif "mCurrentFocus=null" in app_output or "com.google.android.tvlauncher" in app_output:
                current_app = "Home Screen"
            elif "u0 " in app_output:
                try:
                    current_app = app_output.split("u0 ")[1].split("/")[0]
                except IndexError:
                    current_app = "Unknown App"

            return {"status": "online", "power": "on", "app": current_app}

        except Exception as e:
            # If the connection drops mid-poll, clear the device so we use the Lightning Check next time
            self.adb_device = None
            return {"status": "offline", "power": "off", "app": "none"}

    # --- OS ACTIONS ---
    def _launch_app(self, app_name: str) -> str:
        """Translates app names to Windows executables and launches them safely."""
        print(f"[ACTION ENGINE] Launching app: {app_name}")
        app_name_lower = app_name.lower().strip()
        
        # Check if it's explicitly in our secure registry
        exe_path = self.app_registry.get(app_name_lower)
        
        if exe_path:
            try:
                # shell=True is crucial for Windows to resolve things like 'calc.exe' or 'code'
                subprocess.Popen(exe_path, shell=True)
                return f"Launching {app_name} now, sir."
            except Exception as e:
                return f"Failed to launch {app_name}. Error: {e}"
        else:
            # Fallback: Try launching the raw string if it's a known Windows default
            try:
                subprocess.Popen(app_name_lower, shell=True)
                return f"Attempting to launch {app_name}, sir."
            except Exception:
                return f"I do not have {app_name} registered in my database, sir."

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
                    return "Security override triggered."
            if path.is_file():
                path.unlink()
                return "File successfully deleted, sir."
            elif path.is_dir():
                shutil.rmtree(path)
                return "Directory removed."
            else:
                return "I couldn't find that specific target."
        except Exception as e:
            return f"Deletion protocol failed: {e}"

    def _open_link(self, url: str) -> str:
        print(f"[ACTION ENGINE] Navigating to: {url}")
        try:
            if not url.startswith("http"):
                url = f"https://{url}"
            webbrowser.open(url)
            return "Opening the requested page now, sir."
        except Exception as e:
            return f"Browser glitch: {e}"

    def _web_search(self, query: str) -> str:
        print(f"[ACTION ENGINE] Initiating research for: {query}")
        try:
            results = []
            with DDGS() as ddgs:
                search_data = ddgs.text(query, max_results=3)
                for r in search_data:
                    results.append(f"URL: {r.get('href', '')} | Data: {r.get('body', '')}")
            if not results:
                return "I searched the global archives, sir, but found no relevant data."
            return "\n".join(results)
        except Exception as e:
            return f"Error during research: {e}"

    def _web_search_image(self, query: str) -> dict:
        print(f"[ACTION ENGINE] Initiating image search for: {query}")
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(query, max_results=5))
                for r in results:
                    image_url = r.get('image') or r.get('url') 
                    if image_url:
                        return {"success": True, "url": image_url, "title": r.get('title', query)}
                return {"success": False, "error": "No valid image URLs found."}
        except Exception as e:
            return {"success": False, "error": str(e)}

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
            return f"Error: {e}"