import pyautogui
import pytesseract
import os

# Automatically find Tesseract executable on Windows
tesseract_paths = [
    r"F:\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"C:\Users\Kaustav\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
]

for path in tesseract_paths:
    if os.path.exists(path):
        pytesseract.pytesseract.tesseract_cmd = path
        break

def read_active_screen() -> str:
    """Captures the primary monitor and extracts text using OCR."""
    try:
        # Take a screenshot of the primary monitor
        screenshot = pyautogui.screenshot()
        
        # Extract text
        text = pytesseract.image_to_string(screenshot)
        
        # Clean up the text
        cleaned_text = " ".join(text.split())
        
        if not cleaned_text.strip():
            return "No readable text found on the screen."
            
        # We don't want to flood the prompt with thousands of words.
        # Limit to roughly 300 words
        if len(cleaned_text) > 2000:
            return cleaned_text[:2000] + "... [Text truncated]"
            
        return cleaned_text
        
    except pytesseract.pytesseract.TesseractNotFoundError:
        return "Tesseract OCR engine is not found or not configured in system PATH."
    except Exception as e:
        return f"Failed to read screen: {e}"
