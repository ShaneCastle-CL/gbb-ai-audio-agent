"""
services/acs_caller.py
----------------------
Thin wrapper that creates (or returns) the AcsCaller helper you already
have in `src.acs.acs_helper`.  Splitting it out lets `main.py`
initialise it once during startup and any router import it later.
"""

from __future__ import annotations
from typing import Optional
from rtagents.RTAgent.backend.settings import (
    ACS_CONNECTION_STRING,
    ACS_SOURCE_PHONE_NUMBER,
    BASE_URL,
    ACS_CALLBACK_PATH,
    ACS_WEBSOCKET_PATH,
    AZURE_SPEECH_ENDPOINT,
    AZURE_STORAGE_CONTAINER_URL
)
from utils.ml_logging import get_logger
from rtagents.RTAgent.backend.services.acs.acs_helpers import construct_websocket_url
from src.acs.acs_helper import AcsCaller

logger = get_logger("services.acs_caller")

# Singleton instance (created on first call)
_instance: Optional[AcsCaller] = None


def initialize_acs_caller_instance() -> Optional[AcsCaller]:
    """Create and cache an `AcsCaller` if env vars are set; otherwise return None."""
    global _instance  # noqa: PLW0603
    if _instance:
        return _instance

    if not all([ACS_CONNECTION_STRING, ACS_SOURCE_PHONE_NUMBER, BASE_URL]):
        logger.warning("ACS env vars not fully configured – outbound calling disabled")
        return None

    callback_url = f"{BASE_URL.rstrip('/')}{ACS_CALLBACK_PATH}"
    ws_url = construct_websocket_url(BASE_URL, ACS_WEBSOCKET_PATH)
    if not ws_url:
        logger.error(
            "Could not build ACS media WebSocket URL; disabling outbound calls"
        )
        return None

    try:
        _instance = AcsCaller(
            source_number=ACS_SOURCE_PHONE_NUMBER,
            acs_connection_string=ACS_CONNECTION_STRING,
            callback_url=callback_url,
            websocket_url=ws_url,
            cognitive_services_endpoint=AZURE_SPEECH_ENDPOINT,
            recording_storage_container_url=AZURE_STORAGE_CONTAINER_URL
        )
        logger.info("AcsCaller initialised")
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to initialise AcsCaller: %s", exc, exc_info=True)
        _instance = None
    return _instance

# def handle_acs_ws(ws: WebSocket) -> None:
#     """Handle incoming WebSocket connections for ACS."""
#     global _instance  # noqa: PLW0603
#     if not _instance:
#         logger.error("AcsCaller instance not initialized; cannot handle WebSocket")
#         return

#     try:
#         _instance.handle_websocket(ws)
#     except Exception as exc:  # pylint: disable=broad-except
#         logger.error("Error handling ACS WebSocket: %s", exc, exc_info=True)