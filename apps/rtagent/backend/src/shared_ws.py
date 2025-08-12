"""
shared_ws.py
============
Helpers that BOTH realtime and ACS routers rely on:

    • send_tts_audio        – browser TTS
    • send_response_to_acs  – phone-call TTS
    • push_final            – “close bubble” helper
    • broadcast_message     – relay to /relay dashboards
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Optional, Set

from fastapi import WebSocket
from fastapi.websockets import WebSocketState

from apps.rtagent.backend.settings import ACS_STREAMING_MODE, GREETING_VOICE_TTS
from apps.rtagent.backend.src.latency.latency_tool import LatencyTool
from apps.rtagent.backend.src.services.acs.acs_helpers import (
    broadcast_message,
    play_response_with_queue,
)
from apps.rtagent.backend.src.services.speech_services import SpeechSynthesizer
from src.enums.stream_modes import StreamMode
from src.stateful.state_managment import MemoManager
from utils.ml_logging import get_logger

logger = get_logger("shared_ws")


async def send_tts_audio(
    text: str, 
    ws: WebSocket, 
    latency_tool: Optional[LatencyTool] = None,
    voice_name: Optional[str] = None,
    voice_style: Optional[str] = None,
    rate: Optional[str] = None
) -> None:
    """
    Synthesize speech and send audio data to browser WebSocket client.

    Uses the synthesizer cached on FastAPI `app.state.tts_client`.
    Adds latency tracking for TTS step and sends audio frames to React frontend.
    
    Args:
        text: Text to synthesize
        ws: WebSocket connection
        latency_tool: Optional latency tracking tool
        voice_name: Optional agent-specific voice name (overrides default)
        voice_style: Optional agent-specific voice style
    """
    # Validate WebSocket connection
    if ws.client_state != WebSocketState.CONNECTED:
        logger.error("WebSocket is not connected, cannot send TTS audio")
        return

    if not text or not text.strip():
        logger.warning("Empty text provided for TTS synthesis")
        return

    if latency_tool:
        latency_tool.start("tts")
        latency_tool.start("tts:synthesis")

    try:
        synth: SpeechSynthesizer = ws.app.state.tts_client
        ws.state.is_synthesizing = True  # type: ignore[attr-defined]
        logger.info(f"Synthesizing text: {ws.state.is_synthesizing}...")
        synth.start_speaking_text(text)

        # Use agent voice if provided, otherwise fallback to default
        voice_to_use = voice_name or GREETING_VOICE_TTS
        
        # Synthesize text to PCM bytes for browser playback
        logger.debug(f"Synthesizing text (voice: {voice_to_use}): {text[:100]}...")
        pcm_bytes = synth.synthesize_to_pcm(
            text=text, voice=voice_to_use, sample_rate=16000, style=voice_style or "chat", rate=rate or "+3%"
        )

        if latency_tool:
            latency_tool.stop("tts:synthesis", ws.app.state.redis)

        # Convert PCM to base64 frames for WebSocket transmission
        frames = SpeechSynthesizer.split_pcm_to_base64_frames(
            pcm_bytes, sample_rate=16000
        )
        try:
            from opentelemetry import trace as _t
            _t.get_current_span().set_attribute("pipeline.stage", "tts -> websocket (acs)")
        except Exception:
            pass
        logger.debug(f"Generated {len(frames)} audio frames for WebSocket transmission")

        # Send audio frames to React frontend
        for i, frame in enumerate(frames):
            if ws.client_state != WebSocketState.CONNECTED:
                logger.warning("WebSocket disconnected during audio transmission")
                break

            try:
                # Send audio data in format expected by React frontend
                await ws.send_json(
                    {
                        "type": "audio_data",
                        "data": frame,
                        "frame_index": i,
                        "total_frames": len(frames),
                        "sample_rate": 16000,
                        "is_final": i == len(frames) - 1,
                    }
                )
            except Exception as e:
                logger.error(f"Failed to send audio frame {i}: {e}")
                break

        logger.debug("TTS audio transmission completed successfully")

    except Exception as e:
        logger.error(f"TTS synthesis failed: {e}")
        # Send error message to frontend
        try:
            await ws.send_json(
                {
                    "type": "tts_error",
                    "error": str(e),
                    "text": text[:100] + "..." if len(text) > 100 else text,
                }
            )
        except Exception as send_error:
            logger.error(f"Failed to send error message to frontend: {send_error}")


async def send_response_to_acs(
    ws: WebSocket,
    text: str,
    *,
    blocking: bool = False,
    latency_tool: Optional[LatencyTool] = None,
    stream_mode: StreamMode = ACS_STREAMING_MODE,
    voice_name: Optional[str] = None,
    voice_style: Optional[str] = None,
    rate: Optional[str] = None,
) -> Optional[asyncio.Task]:
    """
    Synthesizes speech and sends it as audio data to the ACS WebSocket.

    Adds latency tracking for TTS step.
    
    Args:
        ws: WebSocket connection
        text: Text to synthesize
        blocking: Whether to wait for completion
        latency_tool: Optional latency tracking tool
        stream_mode: Streaming mode for ACS
        voice_name: Optional agent-specific voice name (overrides default)
        voice_style: Optional agent-specific voice style
    """

    if latency_tool:
        latency_tool.start("tts")
        latency_tool.start("tts:synthesis")

    async def stop_latency(task):
        if latency_tool:
            latency_tool.stop("tts", ws.app.state.redis)
        ws.app.state.tts_tasks.discard(task)

    if stream_mode == StreamMode.MEDIA:
        synth: SpeechSynthesizer = ws.app.state.tts_client

        try:
            # Use agent voice if provided, otherwise fallback to default
            voice_to_use = voice_name or GREETING_VOICE_TTS
            
            # Add timeout and retry logic for TTS synthesis
            pcm_bytes = synth.synthesize_to_pcm(
                text=text, voice=voice_to_use, sample_rate=16000, style=voice_style or "chat", rate=rate or "+15%"
            )
            frames = SpeechSynthesizer.split_pcm_to_base64_frames(
                pcm_bytes, sample_rate=16000
            )
            
            if latency_tool:
                latency_tool.stop("tts:synthesis", ws.app.state.redis)

        except asyncio.TimeoutError:
            logger.error(f"TTS synthesis timed out for text: {text[:50]}...")
            if latency_tool:
                latency_tool.stop("tts", ws.app.state.redis)
            raise RuntimeError("TTS synthesis timed out")
        except Exception as e:
            logger.error(f"TTS synthesis failed: {e}")
            if latency_tool:
                latency_tool.stop("tts", ws.app.state.redis)
            raise RuntimeError(f"TTS synthesis failed: {e}")

        # Send audio frames to ACS WebSocket
        try:
            for frame in frames:
                if (
                    hasattr(ws.state, "lt")
                    and ws.state.lt
                    and not getattr(ws.state, "_greeting_ttfb_stopped", False)
                ):
                    ws.state.lt.stop("greeting_ttfb", ws.app.state.redis)
                    ws.state._greeting_ttfb_stopped = True
                try:
                    await ws.send_json(
                        {"kind": "AudioData", "AudioData": {"data": frame}, "StopAudio": None}
                    )
                except Exception as e:
                    logger.error(f"Failed to send ACS audio frame: {e}")
                    break
        except Exception as e:
            logger.error(f"Failed to send audio frames to ACS: {e}")
        finally:
            if latency_tool:
                latency_tool.stop("tts", ws.app.state.redis)
        
        # Return None for MEDIA mode (synchronous completion)
        return None

    elif stream_mode == StreamMode.TRANSCRIPTION:
        acs_caller = ws.app.state.acs_caller
        if not acs_caller:
            raise RuntimeError("ACS caller is not initialized in WebSocket state.")

        coro = play_response_with_queue(
            ws=ws, response_text=text, participants=[ws.app.state.target_participant]
        )

        if not hasattr(ws.app.state, "tts_tasks"):
            ws.app.state.tts_tasks = set()

        task = asyncio.create_task(coro)
        ws.app.state.tts_tasks.add(task)
        task.add_done_callback(stop_latency)

        return task


async def push_final(
    ws: WebSocket,
    role: str,
    content: str,
    *,
    is_acs: bool = False,
) -> None:
    """
    Close the streaming bubble on the front-end.

    • Browser/WebRTC – we already streamed TTS, just send the final JSON.
    • ACS            – same; streaming audio is finished, no repeat playback.
    """
    try:
        await ws.send_text(json.dumps({"type": role, "content": content}))
    except Exception as e:
        logger.error(f"Failed to send final message to WebSocket: {e}")

# --------------------------------------------------------------------------- #
# Re-export for convenience
# --------------------------------------------------------------------------- #
__all__ = [
    "send_tts_audio",
    "send_response_to_acs",
    "push_final",
    "broadcast_message",
]
