# filepath: /Users/jinle/Repos/_AIProjects/gbb-ai-audio-agent/usecases/browser_RTMedAgent/backend/ws_utils.py
import json
import asyncio
from base64 import b64encode
from fastapi import WebSocket
from fastapi.websockets import WebSocketState
from utils.ml_logging import get_logger

logger = get_logger()

async def send_pcm_frames_acs(ws: WebSocket, pcm_bytes: bytes, sample_rate: int = 16000, chunk_duration_ms: int = 20):
    """
    Sends PCM audio data over WebSocket in the format expected by ACS Media Streaming.

    Args:
        ws: The WebSocket connection.
        pcm_bytes: The raw PCM audio bytes (16-bit mono).
        sample_rate: The sample rate of the audio (default 16000 Hz).
        chunk_duration_ms: The duration of each audio chunk in milliseconds (default 20ms).
                           ACS expects 20ms chunks for 16kHz audio.
    """
    bytes_per_sample = 2  # 16-bit audio
    samples_per_chunk = int(sample_rate * (chunk_duration_ms / 1000))
    packet_size = samples_per_chunk * bytes_per_sample # e.g., 16000 * 0.020 * 2 = 640 bytes for 16kHz/20ms

    logger.debug(f"Sending PCM frames. Total bytes: {len(pcm_bytes)}, Sample Rate: {sample_rate}, Chunk Duration: {chunk_duration_ms}ms, Packet Size: {packet_size} bytes")

    for i in range(0, len(pcm_bytes), packet_size):
        chunk = pcm_bytes[i:i + packet_size]
        if not chunk:
            continue

        # Base64 encode the chunk
        encoded_chunk = b64encode(chunk).decode('utf-8')

        # Construct the JSON message for ACS
        message = {
            "kind": "audioData",
            "audioData": {
                "timestamp": "0", # ACS might ignore this, but it's part of the expected structure
                "participantRawID": "None", # Will be filled later if needed
                "data": encoded_chunk,
                "silent": False # Assuming this chunk contains audio
            }
        }

        await send_data(ws, json.dumps(message))
        # Optional: Add a small delay if needed, though usually not required for 20ms chunks
        # await asyncio.sleep(0.01) # Sleep slightly less than chunk duration

    logger.debug("Finished sending PCM frames.")


async def send_data(websocket: WebSocket, data: str):
    """Sends data over the WebSocket if connected."""
    if websocket.client_state == WebSocketState.CONNECTED:
        try:
            await websocket.send_text(data)
            # logger.debug(f"Sent data: {data[:100]}...") # Log truncated data
        except Exception as e:
            logger.error(f"Error sending data via WebSocket: {e}")
    else:
        logger.warning("WebSocket is not connected. Cannot send data.")


async def stop_audio(websocket: WebSocket):
    """
    Sends a 'stopMedia' message to ACS Media Streaming service.
    (This does not close the WebSocket; it just pauses the stream.)
    """
    if websocket.client_state == WebSocketState.CONNECTED:
        message = {
            "kind": "stopMedia",
            "stopMedia": {
                "mediaType": "audio"
            }
        }
        logger.info("Sending stopMedia message to ACS.")
        await send_data(websocket, json.dumps(message))
    else:
        logger.warning("WebSocket is not connected. Cannot send stopMedia.")


async def resume_audio(websocket: WebSocket):
    """
    Sends a 'startMedia' message to ACS Media Streaming service to resume.
    (This resumes the stream without needing to reconnect.)
    Note: ACS documentation primarily focuses on starting media initially.
          Resuming might implicitly happen when new audioData is sent after a stop.
          This function sends a generic start message, adjust if ACS requires specific resume logic.
    """
    if websocket.client_state == WebSocketState.CONNECTED:
        # ACS might not have an explicit "resume" kind. Sending audioData might suffice.
        # If a specific "startMedia" is needed after "stopMedia", use this structure.
        # Check ACS documentation for the exact behavior after stopMedia.
        # For now, we'll log and assume sending audioData resumes.
        logger.info("Attempting to resume audio stream (Note: ACS might resume automatically on next audioData).")
        # Example if a startMedia message IS needed:
        # message = {
        #     "kind": "startMedia",
        #     "startMedia": {
        #         "mediaType": "audio",
        #         # Add other necessary parameters if required by ACS for resuming
        #     }
        # }
        # await send_data(websocket, json.dumps(message))
    else:
        logger.warning("WebSocket is not connected. Cannot resume audio.")

