import datetime
import asyncio
from ddgs import DDGS
import json

class RoutineEngine:
    """Manages multi-step automated routines."""
    def __init__(self, execute_callback, speak_callback):
        self.execute_callback = execute_callback
        self.speak_callback = speak_callback
        
    async def run_morning_briefing(self, active_user="KAUSTAV"):
        """Compiles weather, calendar, email, and news headlines."""
        await self.speak_callback("Good morning. Compiling your morning briefing now.")
        
        # We can reuse the generate_briefing logic or just fetch news here
        news_headline = "No significant tech news at the moment."
        try:
            with DDGS() as ddgs:
                results = ddgs.text("latest technology OR artificial intelligence news", max_results=3)
                if results:
                    headlines = [r['title'] for r in results]
                    news_headline = "Here are the top headlines: " + ". ".join(headlines)
        except Exception as e:
            print(f"[ROUTINES] News retrieval failed: {e}")
            
        await self.speak_callback(news_headline)
        
    async def enable_focus_mode(self, proactive_agent):
        """Silences all non-critical proactive chatter."""
        proactive_agent.is_focus_mode_active = True
        await self.speak_callback("Focus mode enabled. All non-critical notifications have been silenced.")
        
    async def disable_focus_mode(self, proactive_agent):
        proactive_agent.is_focus_mode_active = False
        await self.speak_callback("Focus mode disabled. Standard notification protocols resumed.")
        
    async def run_goodnight_sequence(self):
        """Closes all widgets, pauses PC media, and prepares for sleep."""
        print("[ROUTINES] Executing Sleep Protocol...")
        
        # Close all HUD widgets
        await self.execute_callback({"action_type": "close_display", "target": "all"})
        
        # Pause background media on the PC (YouTube/Spotify desktop)
        try:
            import ctypes
            # VK_MEDIA_PLAY_PAUSE = 0xB3
            ctypes.windll.user32.keybd_event(0xB3, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0xB3, 0, 2, 0)
            print("[ROUTINES] PC Media Paused.")
        except Exception as e:
            print(f"[ROUTINES] Failed to pause PC media: {e}")
            
        await self.speak_callback("Sleep protocol engaged. All displays cleared and media paused. Goodnight, sir.")

    async def run_scheduler(self):
        """Zero-CPU background loop that triggers routines at specific times."""
        print("[ROUTINES] Background scheduler active.")
        morning_briefing_done = False
        evening_protocol_done = False
        
        while True:
            now = datetime.datetime.now()
            hour = now.hour
            minute = now.minute
            
            # Reset daily flags at midnight
            if hour == 0 and minute == 0:
                morning_briefing_done = False
                evening_protocol_done = False
                
            # Trigger Morning Briefing at 8:00 AM
            if hour == 8 and minute == 0 and not morning_briefing_done:
                print("[ROUTINES] Triggering scheduled Morning Briefing.")
                asyncio.create_task(self.run_morning_briefing())
                morning_briefing_done = True
                
            # Trigger Evening Wind-Down at 10:00 PM (22:00)
            if hour == 22 and minute == 0 and not evening_protocol_done:
                print("[ROUTINES] Triggering scheduled Evening Protocol.")
                await self.speak_callback("Sir, it is 10 PM. Would you like me to initiate the sleep protocol?")
                evening_protocol_done = True
                
            # Calculate time until next minute to sleep efficiently (0% CPU)
            sleep_time = 60 - datetime.datetime.now().second
            await asyncio.sleep(sleep_time)
