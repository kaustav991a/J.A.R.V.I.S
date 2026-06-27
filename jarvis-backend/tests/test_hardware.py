import pytest
from unittest.mock import patch, MagicMock
from brain import process_command
from action_engine import ActionEngine

def test_tv_intent():
    """
    Test TV Intent: Mock the LLM classification step to ensure a command like 
    "Turn on the living room TV" successfully triggers the TV Agent target.
    """
    with patch('brain.run_with_key_rotation') as mock_llm:
        mock_response = MagicMock()
        mock_response.choices[0].message.content = '{"actions": [{"action_type": "tv_power", "target": "living room tv"}]}'
        mock_llm.return_value = mock_response
        
        # Test the LLM pipeline
        result = process_command("Turn on the living room TV", "KAUSTAV")
        assert "tv_power" in result

def test_tv_unreachable_fallback():
    """
    Test Unreachable Fallback: Force the TVAgent to raise a ConnectionError (simulating unplugged hardware).
    Assert that the system catches the error and prepares a conversational failure payload, rather than crashing the main loop.
    """
    engine = ActionEngine()
    
    with patch('action_engine.TVAgent.tv_power_toggle', side_effect=ConnectionError("TV is unplugged or unreachable")):
        # The execute method wraps action calls and catches crashes
        result = engine.execute({"action_type": "tv_power"})
        
        # Assert ActionEngine catches the crash and returns conversational error
        assert "offline" in result.lower()
        assert "ConnectionError" in result or "unreachable" in result
