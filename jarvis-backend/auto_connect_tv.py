#!/usr/bin/env python3
"""
auto_connect_tv.py

Automatically discovers Android TVs on the network and connects via ADB (handles dynamic ports with authentication).
"""

import time
from zeroconf import Zeroconf, ServiceBrowser, ServiceStateChange
from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

# Path to store ADB keys
PRIVATE_KEY_PATH = "adbkey"

# Create signer (generates keys if they don't exist)
signer = PythonRSASigner.FromRSAKeyPath(PRIVATE_KEY_PATH)

# Dictionary to store discovered devices
devices = {}

def on_service_state_change(zeroconf, service_type, name, state_change):
    if state_change is ServiceStateChange.Added:
        info = zeroconf.get_service_info(service_type, name)
        if info and info.addresses:
            ip = ".".join(map(str, info.addresses[0]))
            port = info.port
            devices[name] = (ip, port)
            print(f"[+] Discovered TV: {name} at {ip}:{port}")

def discover_tv(timeout=5):
    zeroconf = Zeroconf()
    print("[*] Searching for Android TV devices...")
    browser = ServiceBrowser(zeroconf, "_adb._tcp.local.", handlers=[on_service_state_change])
    time.sleep(timeout)  # Allow time for discovery
    zeroconf.close()
    return devices

def connect_tv(tv_ip, tv_port):
    device = AdbDeviceTcp(tv_ip, tv_port, default_transport_timeout_s=9.)

    try:
        device.connect(rsa_keys=[signer])
        print(f"[+] Connected to TV at {tv_ip}:{tv_port}")
        # Test command
        output = device.shell("echo Hello from Python with authentication!")
        print(f"[TV] {output}")
    except Exception as e:
        print(f"[!] Failed to connect: {e}")
        print("[!] Make sure you accept the debugging prompt on your TV.")

def main():
    discovered = discover_tv()
    if not discovered:
        print("[!] No Android TV devices found.")
        return

    # Pick the first discovered TV
    tv_name, (tv_ip, tv_port) = next(iter(discovered.items()))
    print(f"[*] Attempting connection to {tv_name} at {tv_ip}:{tv_port}")
    connect_tv(tv_ip, tv_port)

if __name__ == "__main__":
    main()