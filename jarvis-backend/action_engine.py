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
from modules.screen_reader import read_active_screen
from modules.gmail_agent import GmailAgent
from modules.calendar_agent import CalendarAgent
from modules.file_agent import FileAgent
from modules.health_agent import HealthAgent

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
        elif action == "play_music":
            return self._play_music(target)
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
        elif action == "read_screen":
            return self._read_screen()
        # --- Phase 7: Health Data ---
        elif action == "check_vitals":
            return self._check_vitals()
        # --- Phase 6: Digital Life ---
        elif action == "check_email":
            return self._check_email()
        elif action == "read_email":
            return self._read_email(target)
        elif action == "send_email":
            return self._send_email(target)
        elif action == "check_calendar":
            return self._check_calendar()
        elif action == "create_event":
            return self._create_event(target)
        elif action == "clear_schedule":
            return self._clear_schedule()
        elif action == "find_file":
            return self._find_file(target)
        elif action == "create_note":
            return self._create_note(target)
        elif action == "organize_downloads":
            return self._organize_downloads()
        # --- Phase 8: HUD Widget Toggles (handled by main.py, not action_engine) ---
        elif action in ("open_sticky_note", "close_sticky_note", "open_browser", "close_browser", "open_calculator", "close_calculator"):
            return f"UI_WIDGET_TOGGLE:{action}"
        else:
            return f"I'm afraid I don't know how to perform the action '{action}', sir."

    # ==========================================
    # SELF-CORRECTION ENGINE (Phase 4.4)
    # ==========================================
    def execute_with_retry(self, payload: dict) -> str:
        """
        Wraps execute() with intelligent fallback strategies.
        If an action fails, attempts one automatic recovery before reporting failure.
        """
        result = self.execute(payload)
        
        # Check if the result indicates a failure
        if not self._is_failure(result):
            return result
        
        # Attempt fallback
        action = payload.get("action_type", "").lower()
        target = payload.get("target", "")
        
        print(f"[RETRY ENGINE] Primary action failed: '{result[:60]}'. Attempting fallback...", flush=True)
        
        fallback_result = self._attempt_fallback(action, target, result)
        if fallback_result:
            print(f"[RETRY ENGINE] Fallback succeeded.", flush=True)
            return fallback_result
        
        # No fallback available or fallback also failed
        return result
    
    def _is_failure(self, result) -> bool:
        """Determines if an action result indicates failure."""
        if isinstance(result, dict):
            return not result.get("success", True)
        if isinstance(result, str):
            failure_keywords = [
                "failed", "error", "couldn't", "unable", "cannot", 
                "not found", "don't have", "not registered"
            ]
            return any(kw in result.lower() for kw in failure_keywords)
        return False
    
    def _attempt_fallback(self, action: str, target: str, original_error: str):
        """Returns a fallback result or None if no strategy applies."""
        
        # --- CLOSE APP FALLBACK: Try window title matching ---
        if action == "close_app":
            try:
                import ctypes
                import ctypes.wintypes
                
                EnumWindows = ctypes.windll.user32.EnumWindows
                GetWindowText = ctypes.windll.user32.GetWindowTextW
                GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
                PostMessage = ctypes.windll.user32.PostMessageW
                WM_CLOSE = 0x0010
                closed_count = 0
                
                @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
                def _enum_callback(hwnd, lParam):
                    nonlocal closed_count
                    length = GetWindowTextLength(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        GetWindowText(hwnd, buff, length + 1)
                        if target.lower() in buff.value.lower():
                            PostMessage(hwnd, WM_CLOSE, 0, 0)
                            closed_count += 1
                    return True
                
                EnumWindows(_enum_callback, 0)
                if closed_count > 0:
                    return f"Retry successful. Closed {closed_count} window(s) matching '{target}' by title."
            except Exception:
                pass
        
        # --- LAUNCH APP FALLBACK: Try OS 'start' command ---
        if action == "launch_app":
            try:
                os.system(f'start "" "{target}"')
                return f"Retry successful. Attempted to launch '{target}' via OS start command."
            except Exception:
                pass
        
        # --- WEB SEARCH FALLBACK: Broaden the query ---
        if action == "web_search" and ("no relevant data" in original_error.lower() or "error" in original_error.lower()):
            try:
                broadened = f"{target} explained simply"
                result = self._web_search(broadened)
                if not self._is_failure(result):
                    return result
            except Exception:
                pass
        
        # --- TV FALLBACK: Reconnect and retry once ---
        if action in ["tv_control", "tv_type", "tv_search"]:
            try:
                self.adb_device = None  # Force reconnect
                if self._connect_tv():
                    if action == "tv_control":
                        return self._control_tv(target)
                    elif action == "tv_type":
                        return self._tv_type(target)
                    elif action == "tv_search":
                        return self._tv_search(target)
            except Exception:
                pass
        
        return None

    # ==========================================
    # PHASE 7: HEALTH INTEGRATION
    # ==========================================
    
    def _check_vitals(self) -> str:
        try:
            agent = HealthAgent()
            data = agent.get_today_health_data()
            if not data.get("configured"):
                return "The health module is offline or not configured, sir."
            steps = data.get("steps", 0)
            hr = data.get("heart_rate", 0)
            return f"Your current resting heart rate is {hr} BPM, and you have taken {steps} steps today."
        except Exception as e:
            print(f"[ACTION ENGINE] Health check failed: {e}")
            return "I am currently unable to interface with your health monitors."

    # ==========================================
    # PHASE 6: DIGITAL LIFE MANAGER
    # ==========================================
    
    def _check_email(self) -> str:
        try:
            agent = GmailAgent()
            return agent.get_unread_summary()
        except Exception as e:
            print(f"[ACTION ENGINE] Email check failed: {e}")
            return "I'm unable to access your email at the moment, sir."
    
    def _read_email(self, target: str) -> str:
        try:
            agent = GmailAgent()
            index = target if target else "latest"
            return agent.read_email(index)
        except Exception as e:
            print(f"[ACTION ENGINE] Email read failed: {e}")
            return "I couldn't read that email, sir."
    
    def _send_email(self, target: str) -> str:
        try:
            # Expected format: "to@email.com | Subject | Body text"
            parts = [p.strip() for p in target.split("|")]
            if len(parts) < 3:
                return "I need the recipient, subject, and body to send an email, sir. Format: 'to@email.com | Subject | Body'"
            agent = GmailAgent()
            return agent.send_email(parts[0], parts[1], parts[2])
        except Exception as e:
            print(f"[ACTION ENGINE] Email send failed: {e}")
            return "I couldn't send that email, sir."
    
    def _check_calendar(self) -> str:
        try:
            agent = CalendarAgent()
            return agent.get_today_schedule()
        except Exception as e:
            print(f"[ACTION ENGINE] Calendar check failed: {e}")
            return "I'm unable to access your calendar at the moment, sir."
    
    def _create_event(self, target: str) -> str:
        try:
            agent = CalendarAgent()
            return agent.create_event(target)
        except Exception as e:
            print(f"[ACTION ENGINE] Event creation failed: {e}")
            return "I couldn't create that event, sir."
            
    def _clear_schedule(self) -> str:
        try:
            agent = CalendarAgent()
            return agent.clear_today_schedule()
        except Exception as e:
            print(f"[ACTION ENGINE] Clear schedule failed: {e}")
            return "I couldn't clear your schedule, sir."
    
    def _find_file(self, target: str) -> str:
        try:
            agent = FileAgent()
            return agent.find_file(target)
        except Exception as e:
            print(f"[ACTION ENGINE] File search failed: {e}")
            return "I encountered an error searching for that file, sir."
    
    def _create_note(self, target: str) -> str:
        try:
            agent = FileAgent()
            return agent.create_note(target)
        except Exception as e:
            print(f"[ACTION ENGINE] Note creation failed: {e}")
            return "I couldn't create that note, sir."
    
    def _organize_downloads(self) -> str:
        try:
            agent = FileAgent()
            return agent.organize_downloads()
        except Exception as e:
            print(f"[ACTION ENGINE] Download organization failed: {e}")
            return "I couldn't organize your downloads, sir."

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
        import subprocess

        # 1. THE LIGHTNING CHECK: Only attempt a heavy connection if the port is physically open
        if not (self.adb_device and self.adb_device.available):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0) # Increased timeout for slow Android TV ADB ports
            try:
                # Try to knock on the exact ADB port
                s.connect((self.tv_ip, self.tv_port))
                s.close()
                
                # If the knock was answered, the TV woke up! Do the heavy connection.
                if not self._connect_tv():
                    return {"status": "online", "power": "standby", "app": "none"}
            except Exception:
                s.close()
                # Port is closed. Try a ping to see if the device is at least on the network.
                try:
                    ping_result = subprocess.run(
                        ["ping", "-n", "1", "-w", "1000", self.tv_ip],
                        capture_output=True, text=True, timeout=3
                    )
                    if ping_result.returncode == 0:
                        # Device is on the network but ADB port is closed (TV screen off but standby)
                        return {"status": "online", "power": "standby", "app": "none"}
                except Exception:
                    pass
                # Both ADB and ping failed. TV is truly offline.
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
        
        # --- FIX: Web-based apps that don't have .exe files ---
        web_apps = {
            "youtube": "https://www.youtube.com",
            "spotify": "https://open.spotify.com",
            "gmail": "https://mail.google.com",
            "google": "https://www.google.com",
        }
        for key, url in web_apps.items():
            if key in app_name_lower:
                webbrowser.open(url)
                return f"Opening {key.title()} in your browser, sir."
        
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
            # --- FIX: Close browser tabs by Window Title using Win32 API ---
            if "youtube" in app_name or "spotify" in app_name:
                search_term = "YouTube" if "youtube" in app_name else "Spotify"
                import ctypes
                import ctypes.wintypes
                
                EnumWindows = ctypes.windll.user32.EnumWindows
                GetWindowText = ctypes.windll.user32.GetWindowTextW
                GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
                PostMessage = ctypes.windll.user32.PostMessageW
                WM_CLOSE = 0x0010
                closed_count = 0
                
                @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
                def _enum_callback(hwnd, lParam):
                    nonlocal closed_count
                    length = GetWindowTextLength(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        GetWindowText(hwnd, buff, length + 1)
                        if search_term.lower() in buff.value.lower():
                            PostMessage(hwnd, WM_CLOSE, 0, 0)
                            closed_count += 1
                    return True
                
                EnumWindows(_enum_callback, 0)
                if closed_count > 0:
                    return f"Task terminated. {search_term} window closed."
                else:
                    return f"No {search_term} window found to close."

            exe_name = self.app_registry.get(app_name, f"{app_name}.exe")
            if "\\" in exe_name or "/" in exe_name:
                exe_name = Path(exe_name).name
            os.system(f'taskkill /IM {exe_name} /F')
            
            # --- FIX: Ensure modern Windows Calculator UWP is caught
            if app_name == "calculator":
                os.system('taskkill /IM CalculatorApp.exe /F')
                os.system('taskkill /IM win32calc.exe /F')
                
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

    def _play_music(self, target: str) -> str:
        print(f"[ACTION ENGINE] Playing music: {target}")
        target_lower = target.lower()
        chrome_path = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
        
        if "spotify" in target_lower:
            search_query = target_lower.replace("spotify", "").replace("on", "").strip().replace(" ", "%20")
            url = f"https://open.spotify.com/search/{search_query}" if search_query else "https://open.spotify.com"
            return {"success": True, "action_type": "play_youtube", "url": url}
        else:
            # Default to YouTube
            search_query = target_lower.replace("youtube", "").replace("on", "").strip()
            if search_query:
                # Try to get the direct video link instead of search results
                try:
                    with DDGS() as ddgs:
                        results = list(ddgs.text(f"site:youtube.com watch {search_query}", max_results=1))
                        if results and "href" in results[0]:
                            url = results[0]["href"]
                        else:
                            url = f"https://www.youtube.com/results?search_query={search_query.replace(' ', '+')}"
                except Exception:
                    url = f"https://www.youtube.com/results?search_query={search_query.replace(' ', '+')}"
            else:
                url = "https://www.youtube.com"
                
            return {"success": True, "action_type": "play_youtube", "url": url}

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

    def _read_screen(self) -> str:
        print("[ACTION ENGINE] Executing Screen Reader OCR...")
        text = read_active_screen()
        # The result goes back to the LLM as research data
        return f"SCREEN CONTENTS:\n{text}"