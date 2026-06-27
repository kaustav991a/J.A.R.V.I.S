import pytest
import datetime
from unittest.mock import patch, MagicMock
from background_monitor import ScheduleDaemon

def test_cache_deduplication():
    """
    Test Cache Deduplication: Mock calendar_agent.get_upcoming() to return a dummy event 6 minutes away.
    Run the daemon's core check logic twice.
    Assert that the mocked TTS engine is only called once and the event ID is added to notified_events.
    """
    dummy_loop = MagicMock()
    daemon = ScheduleDaemon(main_loop=dummy_loop, active_user="KAUSTAV")
    
    dummy_event = [{"id": "test_event_123", "summary": "Standup", "start": "2026-05-03T10:00:00", "minutes_until": 6}]
    
    with patch('modules.calendar_agent.is_calendar_available', return_value=True), \
         patch('modules.calendar_agent.CalendarAgent') as mock_calendar_class, \
         patch('background_monitor.speaker.is_system_speaking', False), \
         patch('asyncio.run_coroutine_threadsafe') as mock_run_coroutine:
         
        mock_calendar_instance = MagicMock()
        mock_calendar_instance.get_upcoming.return_value = dummy_event
        mock_calendar_class.return_value = mock_calendar_instance
         
        # Run core logic twice
        daemon._check_schedule()
        daemon._check_schedule()
        
        # Assert TTS called exactly once
        assert mock_run_coroutine.call_count == 1
        
        # Assert event ID is added to cache
        assert "test_event_123" in daemon.notified_events

def test_midnight_flush():
    """
    Test Midnight Flush: Pre-populate the notified_events set and manually set last_clear_date to yesterday.
    Run the check cycle. Assert the set is cleared to length 0 and the date updates to today.
    """
    dummy_loop = MagicMock()
    daemon = ScheduleDaemon(main_loop=dummy_loop, active_user="KAUSTAV")
    
    # Pre-populate state with dummy data and set date to yesterday
    daemon.notified_events.add("stale_event_456")
    daemon.last_clear_date = datetime.datetime.now().date() - datetime.timedelta(days=1)
    
    with patch('background_monitor.speaker.is_system_speaking', True):
        # We can just let it return early due to speaking, because the flush happens before that
        daemon._check_schedule()
        
    assert len(daemon.notified_events) == 0, "notified_events cache was not flushed"
    assert daemon.last_clear_date == datetime.datetime.now().date(), "last_clear_date did not update to today"
