"""
acs_helpers.py

This module provides helper functions and utilities for integrating with Azure Communication Services (ACS) in the context of real-time media streaming and WebSocket communication. It includes initialization routines, WebSocket URL construction, message broadcasting, and audio data handling for ACS media streaming scenarios.

"""

import json
from base64 import b64encode
from typing import List, Optional

from fastapi import WebSocket
from fastapi.websockets import WebSocketState
from azure.core.exceptions import HttpResponseError
from azure.communication.callautomation import TextSource, SsmlSource

from src.acs.acs_helper import AcsCaller
from rtagents.RTInsuranceAgent.backend.settings import (
    ACS_CALLBACK_PATH,
    ACS_CONNECTION_STRING,
    ACS_SOURCE_PHONE_NUMBER,
    ACS_WEBSOCKET_PATH,
    BASE_URL,
)
from utils.ml_logging import get_logger

# --- Init Logger ---
logger = get_logger()


# --- Helper Functions for Initialization ---
def construct_websocket_url(base_url: str, path: str) -> Optional[str]:
    """Constructs a WebSocket URL from a base URL and path."""
    if not base_url:  # Added check for empty base_url
        logger.error("BASE_URL is empty or not provided.")
        return None
    if "<your" in base_url:  # Added check for placeholder
        logger.warning(
            "BASE_URL contains placeholder. Please update environment variable."
        )
        return None

    base_url_clean = base_url.strip("/")
    path_clean = path.strip("/")

    if base_url.startswith("https://"):
        return f"wss://{base_url_clean}/{path_clean}"
    elif base_url.startswith("http://"):
        logger.warning(
            "BASE_URL starts with http://. ACS Media Streaming usually requires wss://."
        )
        return f"ws://{base_url_clean}/{path_clean}"
    else:
        logger.error(
            f"Cannot determine WebSocket protocol (wss/ws) from BASE_URL: {base_url}"
        )
        return None


def initialize_acs_caller_instance() -> Optional[AcsCaller]:
    """Initializes and returns the ACS Caller instance if configured, otherwise None."""
    if not all([ACS_CONNECTION_STRING, ACS_SOURCE_PHONE_NUMBER, BASE_URL]):
        logger.warning(
            "ACS environment variables not fully configured. ACS calling disabled."
        )
        return None

    acs_callback_url = f"{BASE_URL.strip('/')}{ACS_CALLBACK_PATH}"
    acs_websocket_url = construct_websocket_url(BASE_URL, ACS_WEBSOCKET_PATH)

    if not acs_websocket_url:
        logger.error(
            "Could not construct valid ACS WebSocket URL. ACS calling disabled."
        )
        return None

    logger.info("Attempting to initialize AcsCaller...")
    logger.info(f"ACS Callback URL: {acs_callback_url}")
    logger.info(f"ACS WebSocket URL: {acs_websocket_url}")

    try:
        caller_instance = AcsCaller(
            source_number=ACS_SOURCE_PHONE_NUMBER,
            acs_connection_string=ACS_CONNECTION_STRING,
            callback_url=acs_callback_url,
            acs_media_streaming_websocket_path=acs_websocket_url,
        )
        logger.info("AcsCaller initialized successfully.")
        return caller_instance
    except Exception as e:
        logger.error(f"Failed to initialize AcsCaller: {e}", exc_info=True)
        return None


# --- Helper Functions for Initialization ---
def construct_websocket_url(base_url: str, path: str) -> Optional[str]:
    """Constructs a WebSocket URL from a base URL and path."""
    if not base_url:  # Added check for empty base_url
        logger.error("BASE_URL is empty or not provided.")
        return None
    if "<your" in base_url:  # Added check for placeholder
        logger.warning(
            "BASE_URL contains placeholder. Please update environment variable."
        )
        return None

    base_url_clean = base_url.strip("/")
    path_clean = path.strip("/")

    if base_url.startswith("https://"):
        base_url_clean = base_url.replace("https://", "").strip("/")
        return f"wss://{base_url_clean}/{path_clean}"
    elif base_url.startswith("http://"):
        base_url_clean = base_url.replace("http://", "").strip("/")
        return f"ws://{base_url_clean}/{path_clean}"
    else:
        logger.error(
            f"Cannot determine WebSocket protocol (wss/ws) from BASE_URL: {base_url}"
        )
        return None


def initialize_acs_caller_instance() -> Optional[AcsCaller]:
    """Initializes and returns the ACS Caller instance if configured, otherwise None."""
    if not all([ACS_CONNECTION_STRING, ACS_SOURCE_PHONE_NUMBER, BASE_URL]):
        logger.warning(
            "ACS environment variables not fully configured. ACS calling disabled."
        )
        return None

    acs_callback_url = f"{BASE_URL.strip('/')}{ACS_CALLBACK_PATH}"
    acs_websocket_url = construct_websocket_url(BASE_URL, ACS_WEBSOCKET_PATH)

    if not acs_websocket_url:
        logger.error(
            "Could not construct valid ACS WebSocket URL. ACS calling disabled."
        )
        return None

    logger.info("Attempting to initialize AcsCaller...")
    logger.info(f"ACS Callback URL: {acs_callback_url}")
    logger.info(f"ACS WebSocket URL: {acs_websocket_url}")

    try:
        caller_instance = AcsCaller(
            source_number=ACS_SOURCE_PHONE_NUMBER,
            acs_connection_string=ACS_CONNECTION_STRING,
            callback_url=acs_callback_url,
            acs_media_streaming_websocket_path=acs_websocket_url,
        )
        logger.info("AcsCaller initialized successfully.")
        return caller_instance
    except Exception as e:
        logger.error(f"Failed to initialize AcsCaller: {e}", exc_info=True)
        return None


async def broadcast_message(
    connected_clients: List[WebSocket], message: str, sender: str = "system"
):
    """
    Send a message to all connected WebSocket clients without duplicates.

    Parameters:
    - message (str): The message to broadcast.
    - sender (str): Indicates the sender of the message. Can be 'agent', 'user', or 'system'.
    """
    sent_clients = set()  # Track clients that have already received the message
    payload = {"message": message, "sender": sender}  # Include sender in the payload
    for client in connected_clients:
        if client not in sent_clients:
            try:
                await client.send_text(json.dumps(payload))
                sent_clients.add(client)  # Mark client as sent
            except Exception as e:
                logger.error(f"Failed to send message to a client: {e}")


async def send_pcm_frames(ws: WebSocket, pcm_bytes: bytes, sample_rate: int):
    packet_size = 640 if sample_rate == 16000 else 960
    for i in range(0, len(pcm_bytes), packet_size):
        frame = pcm_bytes[i : i + packet_size]
        # pad last frame
        if len(frame) < packet_size:
            frame += b"\x00" * (packet_size - len(frame))
        b64 = b64encode(frame).decode("ascii")

        payload = {"kind": "AudioData", "audioData": {"data": b64}, "stopAudio": None}
        await ws.send_text(json.dumps(payload))


async def send_data(websocket, buffer):
    if websocket.client_state == WebSocketState.CONNECTED:
        data = {"Kind": "AudioData", "AudioData": {"data": buffer}, "StopAudio": None}
        # Serialize the server streaming data
        serialized_data = json.dumps(data)
        print(f"Out Streaming Data ---> {serialized_data}")
        # Send the chunk over the WebSocket
        await websocket.send_json(data)


async def stop_audio(websocket):
    """
    Tells the ACS Media Streaming service to stop accepting incoming audio from client.
    (This does not close the WebSocket; it just pauses the stream.)
    """
    if websocket.client_state.name == "CONNECTED":
        stop_payload = {"Kind": "StopAudio", "AudioData": None, "StopAudio": {}}
        await websocket.send_json(stop_payload)
        logger.info("🛑 Sent StopAudio command to ACS WebSocket.")


async def resume_audio(websocket):
    """
    Tells the ACS Media Streaming service to resume accepting incoming audio from client.
    (This resumes the stream without needing to reconnect.)
    """
    if websocket.client_state.name == "CONNECTED":
        start_payload = {"Kind": "StartAudio", "AudioData": None, "StartAudio": {}}
        await websocket.send_json(start_payload)
        logger.info("🎙️ Sent StartAudio command to ACS WebSocket.")

# --- Helper Functions for Initialization ---
def construct_websocket_url(base_url: str, path: str) -> Optional[str]:
    """Constructs a WebSocket URL from a base URL and path."""
    if not base_url:  # Added check for empty base_url
        logger.error("BASE_URL is empty or not provided.")
        return None
    if "<your" in base_url:  # Added check for placeholder
        logger.warning(
            "BASE_URL contains placeholder. Please update environment variable."
        )
        return None

    base_url_clean = base_url.strip("/")
    path_clean = path.strip("/")

    if base_url.startswith("https://"):
        return f"wss://{base_url_clean}/{path_clean}"
    elif base_url.startswith("http://"):
        logger.warning(
            "BASE_URL starts with http://. ACS Media Streaming usually requires wss://."
        )
        return f"ws://{base_url_clean}/{path_clean}"
    else:
        logger.error(
            f"Cannot determine WebSocket protocol (wss/ws) from BASE_URL: {base_url}"
        )
        return None


def initialize_acs_caller_instance() -> Optional[AcsCaller]:
    """Initializes and returns the ACS Caller instance if configured, otherwise None."""
    if not all([ACS_CONNECTION_STRING, ACS_SOURCE_PHONE_NUMBER, BASE_URL]):
        logger.warning(
            "ACS environment variables not fully configured. ACS calling disabled."
        )
        return None

    acs_callback_url = f"{BASE_URL.strip('/')}{ACS_CALLBACK_PATH}"
    acs_websocket_url = construct_websocket_url(BASE_URL, ACS_WEBSOCKET_PATH)

    if not acs_websocket_url:
        logger.error(
            "Could not construct valid ACS WebSocket URL. ACS calling disabled."
        )
        return None

    logger.info("Attempting to initialize AcsCaller...")
    logger.info(f"ACS Callback URL: {acs_callback_url}")
    logger.info(f"ACS WebSocket URL: {acs_websocket_url}")

    try:
        caller_instance = AcsCaller(
            source_number=ACS_SOURCE_PHONE_NUMBER,
            acs_connection_string=ACS_CONNECTION_STRING,
            acs_callback_path=acs_callback_url,
            acs_media_streaming_websocket_path=acs_websocket_url,
        )
        logger.info("AcsCaller initialized successfully.")
        return caller_instance
    except Exception as e:
        logger.error(f"Failed to initialize AcsCaller: {e}", exc_info=True)
        return None


# --- Helper Functions for Initialization ---
def construct_websocket_url(base_url: str, path: str) -> Optional[str]:
    """Constructs a WebSocket URL from a base URL and path."""
    if not base_url:  # Added check for empty base_url
        logger.error("BASE_URL is empty or not provided.")
        return None
    if "<your" in base_url:  # Added check for placeholder
        logger.warning(
            "BASE_URL contains placeholder. Please update environment variable."
        )
        return None

    base_url_clean = base_url.strip("/")
    path_clean = path.strip("/")

    if base_url.startswith("https://"):
        base_url_clean = base_url.replace("https://", "").strip("/")
        return f"wss://{base_url_clean}/{path_clean}"
    elif base_url.startswith("http://"):
        base_url_clean = base_url.replace("http://", "").strip("/")
        return f"ws://{base_url_clean}/{path_clean}"
    else:
        logger.error(
            f"Cannot determine WebSocket protocol (wss/ws) from BASE_URL: {base_url}"
        )
        return None


def initialize_acs_caller_instance() -> Optional[AcsCaller]:
    """Initializes and returns the ACS Caller instance if configured, otherwise None."""
    if not all([ACS_CONNECTION_STRING, ACS_SOURCE_PHONE_NUMBER, BASE_URL]):
        logger.warning(
            "ACS environment variables not fully configured. ACS calling disabled."
        )
        return None

    acs_callback_url = f"{BASE_URL.strip('/')}{ACS_CALLBACK_PATH}"
    acs_websocket_url = construct_websocket_url(BASE_URL, ACS_WEBSOCKET_PATH)

    if not acs_websocket_url:
        logger.error(
            "Could not construct valid ACS WebSocket URL. ACS calling disabled."
        )
        return None

    logger.info("Attempting to initialize AcsCaller...")
    logger.info(f"ACS Callback URL: {acs_callback_url}")
    logger.info(f"ACS WebSocket URL: {acs_websocket_url}")

    try:
        caller_instance = AcsCaller(
            source_number=ACS_SOURCE_PHONE_NUMBER,
            acs_connection_string=ACS_CONNECTION_STRING,
            acs_callback_path=acs_callback_url,
            acs_media_streaming_websocket_path=acs_websocket_url,
        )
        logger.info("AcsCaller initialized successfully.")
        return caller_instance
    except Exception as e:
        logger.error(f"Failed to initialize AcsCaller: {e}", exc_info=True)
        return None


async def broadcast_message(
    connected_clients: List[WebSocket], message: str, sender: str = "system"
):
    """
    Send a message to all connected WebSocket clients without duplicates.

    Parameters:
    - message (str): The message to broadcast.
    - sender (str): Indicates the sender of the message. Can be 'agent', 'user', or 'system'.
    """
    sent_clients = set()  # Track clients that have already received the message
    payload = {"message": message, "sender": sender}  # Include sender in the payload
    for client in connected_clients:
        if client not in sent_clients:
            try:
                await client.send_text(json.dumps(payload))
                sent_clients.add(client)  # Mark client as sent
            except Exception as e:
                logger.error(f"Failed to send message to a client: {e}")


async def send_pcm_frames(ws: WebSocket, pcm_bytes: bytes, sample_rate: int):
    packet_size = 640 if sample_rate == 16000 else 960
    for i in range(0, len(pcm_bytes), packet_size):
        frame = pcm_bytes[i : i + packet_size]
        # pad last frame
        if len(frame) < packet_size:
            frame += b"\x00" * (packet_size - len(frame))
        b64 = b64encode(frame).decode("ascii")

        payload = {"kind": "AudioData", "audioData": {"data": b64}, "stopAudio": None}
        await ws.send_text(json.dumps(payload))


async def send_data(websocket, buffer):
    if websocket.client_state == WebSocketState.CONNECTED:
        data = {"Kind": "AudioData", "AudioData": {"data": buffer}, "StopAudio": None}
        # Serialize the server streaming data
        serialized_data = json.dumps(data)
        print(f"Out Streaming Data ---> {serialized_data}")
        # Send the chunk over the WebSocket
        await websocket.send_json(data)


async def stop_audio(websocket):
    """
    Tells the ACS Media Streaming service to stop accepting incoming audio from client.
    (This does not close the WebSocket; it just pauses the stream.)
    """
    if websocket.client_state.name == "CONNECTED":
        stop_payload = {"Kind": "StopAudio", "AudioData": None, "StopAudio": {}}
        await websocket.send_json(stop_payload)
        logger.info("🛑 Sent StopAudio command to ACS WebSocket.")



async def resume_audio(websocket):
    """
    Tells the ACS Media Streaming service to resume accepting incoming audio from client.
    (This resumes the stream without needing to reconnect.)
    """
    if websocket.client_state.name == "CONNECTED":
        start_payload = {"Kind": "StartAudio", "AudioData": None, "StartAudio": {}}
        await websocket.send_json(start_payload)
        logger.info("🎙️ Sent StartAudio command to ACS WebSocket.")


async def play_response(
    ws: WebSocket,
    response_text: str,
    use_ssml: bool = False,
    voice_name: str = "en-US-JennyNeural",  # Fixed: Use valid Azure TTS voice
    locale: str = "en-US",
    participants: list = None,
    max_retries: int = 5,
    initial_backoff: float = 0.5,
):
    """
    Plays `response_text` into the given ACS call, using the SpeechConfig.
    Sets bot_speaking=True at start, False when done or on error.
    
    :param ws:                 WebSocket connection with app state
    :param response_text:      Plain text or SSML to speak
    :param use_ssml:           If True, wrap in SsmlSource; otherwise TextSource
    :param voice_name:         Valid Azure TTS voice name (default: en-US-JennyNeural)
    :param locale:             Voice locale (default: en-US)
    :param participants:       List of call participants for target identification
    :param max_retries:        Maximum retry attempts for 8500 errors
    :param initial_backoff:    Initial backoff time in seconds
    """
    # 1) Get the call-specific client
    call_connection_id = ws.headers.get("x-ms-call-connection-id")
    acs_caller = ws.app.state.acs_caller
    call_conn = acs_caller.get_call_connection(call_connection_id=call_connection_id)
    cm = ws.app.state.cm
    
    if not call_conn:
        logger.error(
            f"Could not get call connection object for {call_connection_id}. Cannot play media."
        )
        return

    # 2) Validate and sanitize response text
    if not response_text or not response_text.strip():
        logger.info(
            f"Skipping media playback for call {call_connection_id} because response_text is empty."
        )
        return

    # 3) Set bot_speaking flag at start
    if cm:
        cm.update_context("bot_speaking", True)
        cm.persist_to_redis(ws.app.state.redis)

    try:
        # Sanitize and prepare the response text
        sanitized_text = response_text.strip().replace('\n', ' ').replace('\r', ' ')
        sanitized_text = ' '.join(sanitized_text.split())
        
        # Log the sanitized text (first 100 chars) for debugging
        text_preview = sanitized_text[:100] + "..." if len(sanitized_text) > 100 else sanitized_text
        logger.info(f"🔧 Playing text: '{text_preview}'")

        # 4) Build the correct play_source object
        if use_ssml:
            source = SsmlSource(ssml_text=sanitized_text)
            logger.debug(f"Created SsmlSource for call {call_connection_id}")
        else:
            source = TextSource(
                text=sanitized_text,
                voice_name=voice_name,
                source_locale=locale
            )
            logger.debug(f"Created TextSource for call {call_connection_id} with voice {voice_name}")

        # 5) Retry loop for 8500 errors
        for attempt in range(max_retries):
            try:
                response = call_conn.play_media(
                    play_source=source,
                    play_to=participants,
                    interrupt_call_media_operation=True,
                )
                logger.info(
                    f"✅ Successfully played media on attempt {attempt + 1} for call {call_connection_id}"
                )
                return response
                
            except HttpResponseError as e:
                if e.status_code == 8500 or "Media operation is already active" in str(e.message):
                    if attempt < max_retries - 1:  # Don't wait on the last attempt
                        wait_time = initial_backoff * (2 ** attempt)
                        logger.warning(
                            f"⏳ Media active (8500) error on attempt {attempt + 1} for call {call_connection_id}. "
                            f"Retrying after {wait_time:.1f}s..."
                        )
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        logger.error(
                            f"🚨 Failed to play media after {max_retries} retries for call {call_connection_id}"
                        )
                        raise RuntimeError(
                            f"Failed to play media after {max_retries} retries for call {call_connection_id}"
                        )
                else:
                    logger.error(f"❌ Unexpected ACS error during play_media: {e}")
                    raise
            except Exception as e:
                logger.error(f"❌ Unexpected exception during play_media: {e}")
                raise

        # If we reach here, all retries failed
        logger.error(
            f"🚨 Failed to play media after {max_retries} retries for call {call_connection_id}"
        )
        raise RuntimeError(
            f"Failed to play media after {max_retries} retries for call {call_connection_id}"
        )

    except Exception as e:
        logger.error(f"❌ Error in play_response for call {call_connection_id}: {e}")
        raise
    finally:
        # 6) Always clear bot_speaking flag when done (success or error)
        if cm:
            cm.update_context("bot_speaking", False)
            cm.persist_to_redis(ws.app.state.redis)
            logger.debug(f"🔄 Cleared bot_speaking flag for call {call_connection_id}")


