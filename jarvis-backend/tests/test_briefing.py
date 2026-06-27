import pytest
import time
from unittest.mock import patch
from action_engine import ActionEngine

def test_morning_briefing_concurrency():
    """
    Test Concurrency: Mock the health, calendar, and gmail workers to take 0.5 seconds each.
    Assert that ActionEngine._morning_briefing() completes in < 0.6 seconds, proving parallel execution.
    """
    engine = ActionEngine()
    
    def mock_sleep(*args, **kwargs):
        time.sleep(0.5)
        return "MOCKED_DATA"

    with patch('action_engine.HealthAgent.get_summary_string', side_effect=mock_sleep), \
         patch('action_engine.CalendarAgent.get_today_schedule', side_effect=mock_sleep), \
         patch('action_engine.GmailAgent.get_unread_emails', side_effect=mock_sleep):
         
        start_time = time.time()
        result = engine._morning_briefing()
        elapsed = time.time() - start_time
        
        assert elapsed < 0.6, f"Expected concurrent execution < 0.6s, took {elapsed}s"
        assert "[BRIEFING_DATA]" in result
        assert "MOCKED_DATA" in result

def test_morning_briefing_graceful_degradation():
    """
    Test Graceful Degradation: Mock gmail_agent.get_unread_emails() to raise an Exception.
    Assert the returned [BRIEFING_DATA] payload contains the fallback string "Comms server unreachable"
    and does not crash.
    """
    engine = ActionEngine()
    
    with patch('action_engine.HealthAgent.get_summary_string', return_value="Health OK"), \
         patch('action_engine.CalendarAgent.get_today_schedule', return_value="Calendar OK"), \
         patch('action_engine.GmailAgent.get_unread_emails', side_effect=Exception("API limits exceeded")):
         
        result = engine._morning_briefing()
        
        assert "[BRIEFING_DATA]" in result
        assert "Comms server unreachable" in result
        assert "Health OK" in result
