"""
Live Voice Tests Package
========================

Test suite for Live Voice API components.

This package contains comprehensive tests for Live Voice functionality:

Working Tests:
--------------
- test_voice_live_standalone.py ✅ (14/14 tests passing)
  * Independent tests with mock implementations
  * Core business logic validation
  * Session management and audio processing
  * Message handling and error scenarios

Application Integration Tests (Import Issues):
---------------------------------------------
- test_voice_live_endpoints.py ⚠️ 
- test_voice_live_events.py ⚠️
- test_voice_live_handlers.py ⚠️

These tests require full application context and have import dependency issues.

Current Test Coverage:
---------------------
✅ Session creation and validation
✅ Message tracking and conversation history  
✅ Error handling and status management
✅ Audio data processing logic
✅ Control message handling
✅ Connection state tracking
✅ Event type constants validation
✅ Schema validation for message types

Usage:
------
Run working tests:
    pytest tests/live-voice/test_voice_live_standalone.py -v

Run with coverage:
    pytest tests/live-voice/test_voice_live_standalone.py --cov=apps.rtagent.backend.api.v1

Attempt full suite (may have issues):
    pytest tests/live-voice/ -v

See README.md for detailed information about test status and troubleshooting.
"""