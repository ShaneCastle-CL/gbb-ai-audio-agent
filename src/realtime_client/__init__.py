# realtime_client/__init__.py

"""
Realtime Client Package.

Provides:
- RealtimeClient: Main orchestrator for audio and text interactions.
- Utilities for audio encoding and decoding.
- Realtime API websocket manager.
- Conversation state tracker.
- Event dispatching and handling system.
"""

from .client import RealtimeClient
from .api import RealtimeAPI
from .conversation import RealtimeConversation
from .event_handler import RealtimeEventHandler
from .utils import (
    float_to_16bit_pcm,
    base64_to_array_buffer,
    array_buffer_to_base64,
    merge_int16_arrays
)

__all__ = [
    "RealtimeClient",
    "RealtimeAPI",
    "RealtimeConversation",
    "RealtimeEventHandler",
    "float_to_16bit_pcm",
    "base64_to_array_buffer",
    "array_buffer_to_base64",
    "merge_int16_arrays",
]
