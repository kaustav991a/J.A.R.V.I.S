import requests
import asyncio
import os
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv(override=True)
API_KEY = os.getenv("OPENWEATHER_API_KEY")
CITY = "Ichhapur" # Your primary base of operations
BASE_URL = "http://api.openweathermap.org/data/2.5/weather"

async def get_weather_data():
    """Fetches real-time weather for the dashboard and brain."""
    try:
        # Running the request in a thread so it doesn't block the async loop
        params = {
            "q": CITY,
            "appid": API_KEY,
            "units": "metric" # For Celsius
        }
        
        # We use asyncio.to_thread because 'requests' is a blocking library
        response = await asyncio.to_thread(requests.get, BASE_URL, params=params)
        data = response.json()
        
        if response.status_code == 200:
            weather = {
                "temp": round(data["main"]["temp"]),
                "condition": data["weather"][0]["main"],
                "humidity": data["main"]["humidity"],
                "city": CITY
            }
            return weather
        else:
            print(f"[SENSORS] Weather API Error: {data.get('message')}")
            return None
    except Exception as e:
        print(f"[SENSORS] Connection Error: {e}")
        return None

def get_system_telemetry():
    """Fetches real-time local system telemetry using psutil."""
    import psutil
    import time
    
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage('C:\\')
        uptime_seconds = time.time() - psutil.boot_time()
        uptime_hours = round(uptime_seconds / 3600, 1)
        
        # Dynamic status based on system health
        if cpu > 90 or ram.percent > 90:
            status = "CRITICAL"
        elif cpu > 70 or ram.percent > 75:
            status = "ELEVATED"
        else:
            status = "NOMINAL"
        
        return {
            "cpu_percent": round(cpu, 1),
            "ram_percent": round(ram.percent, 1),
            "ram_used_gb": round(ram.used / (1024**3), 1),
            "ram_total_gb": round(ram.total / (1024**3), 1),
            "disk_percent": round(disk.percent, 1),
            "disk_free_gb": round(disk.free / (1024**3), 1),
            "uptime_hours": uptime_hours,
            "status": status
        }
    except Exception as e:
        print(f"[SENSORS] Telemetry Error: {e}")
        return {
            "cpu_percent": 0, "ram_percent": 0, "ram_used_gb": 0,
            "ram_total_gb": 0, "disk_percent": 0, "disk_free_gb": 0,
            "uptime_hours": 0, "status": "OFFLINE"
        }