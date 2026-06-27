import os
import base64
from io import BytesIO

# Configuration toggle: Set to "ollama" down the line to use local VLM.
# Currently set to "groq" for high-speed ephemeral contextual logic.
VLM_PROVIDER = os.getenv("JARVIS_VLM_PROVIDER", "ollama").lower()
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llava")

def _call_groq_vision(img_b64: str) -> str:
    """Sends the base64 image to Groq's Llama 3.2 Vision model."""
    from modules.groq_key_manager import run_with_key_rotation
    
    def _api_call(client):
        response = client.chat.completions.create(
            model="llama-3.2-90b-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text", 
                            "text": "Analyze this screenshot of my computer screen. Describe the active applications, any visible text, the overall context, and what I am currently doing. Be concise, highly descriptive, and focus on the most important elements."
                        },
                        {
                            "type": "image_url", 
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                        }
                    ]
                }
            ],
            max_tokens=1024,
            temperature=0.2
        )
        return response.choices[0].message.content

    return run_with_key_rotation(_api_call)

def _call_ollama_vision(img_b64: str) -> str:
    """Sends the base64 image to a local Ollama Vision model (e.g., llava)."""
    import requests
    
    url = "http://localhost:11434/api/generate"
    payload = {
        "model": OLLAMA_VISION_MODEL,
        "prompt": "Analyze this screenshot of my computer screen. Describe the active applications, any visible text, the overall context, and what I am currently doing. Be concise, highly descriptive, and focus on the most important elements.",
        "images": [img_b64],
        "stream": False
    }
    
    response = requests.post(url, json=payload, timeout=120)
    response.raise_for_status()
    return response.json().get("response", "")

def read_active_screen() -> str:
    """Captures the primary monitor and extracts context using a Vision-Language Model (VLM)."""
    try:
        import pyautogui
        
        # 1. Capture screenshot
        screenshot = pyautogui.screenshot()
        
        # 2. Compress and convert to base64
        buffered = BytesIO()
        screenshot.save(buffered, format="JPEG", quality=75) # Optimize size for fast API transfer
        img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        
        print(f"[SCREEN READER] Uploading screenshot to {VLM_PROVIDER.upper()} Vision Model...")
        
        # 3. Route to the configured VLM provider
        if VLM_PROVIDER == "ollama":
            description = _call_ollama_vision(img_b64)
        else:
            description = _call_groq_vision(img_b64)
        
        if not description or not description.strip():
            return "No description could be generated from the screen."
            
        return description

    except ImportError as e:
        if "pyautogui" in str(e):
            return "Screen reading offline: pyautogui is not installed."
        raise e
    except Exception as e:
        return f"Screen reading VLM offline: {e}"
