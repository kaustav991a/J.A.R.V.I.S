import requests
import asyncio

# --- CONFIGURATION ---
API_KEY = "848b0d63657a612bed69b03ccfe5dbfc"
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

async def get_system_status():
    """Fetches local system telemetry (Placeholder for now)."""
    # Later we can add CPU/RAM usage here using the 'psutil' library
    return {
        "uptime": "10h",
        "status": "Optimal"
    }