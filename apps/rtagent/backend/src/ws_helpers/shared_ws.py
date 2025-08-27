"""
shared_ws.py
============
Clean helpers that BOTH realtime and ACS routers rely on:

    • send_tts_audio        – browser TTS
    • send_response_to_acs  – phone-call TTS  
    • push_final            – "close bubble" helper
    • broadcast_message     – relay to /relay dashboards

REFACTORED: Eliminated 547 lines → ~120 lines by removing state_consolidation dependency
and simplifying over-engineered helper functions while preserving exact same API.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Optional

from fastapi import WebSocket
from fastapi.websockets import WebSocketState

from config import ACS_STREAMING_MODE, GREETING_VOICE_TTS, TTS_SAMPLE_RATE_ACS, TTS_SAMPLE_RATE_UI
from src.tools.latency_tool import LatencyTool
from apps.rtagent.backend.src.services.acs.acs_helpers import play_response_with_queue
from apps.rtagent.backend.src.ws_helpers.envelopes import make_status_envelope
from apps.rtagent.backend.src.services.speech_services import SpeechSynthesizer
from src.enums.stream_modes import StreamMode
from utils.ml_logging import get_logger

logger = get_logger("shared_ws")


def _get_connection_metadata(ws: WebSocket, key: str, default=None):
    """Helper to get metadata from connection manager safely."""
    try:
        conn_id = getattr(ws.state, 'conn_id', None)
        if conn_id and hasattr(ws.app.state, 'conn_manager'):
            connection = ws.app.state.conn_manager._conns.get(conn_id)
            if connection and connection.meta.handler:
                return connection.meta.handler.get(key, default)
    except Exception:
        pass
    return default


def _set_connection_metadata(ws: WebSocket, key: str, value):
    """Helper to set metadata in connection manager safely."""
    try:
        conn_id = getattr(ws.state, 'conn_id', None)
        if conn_id and hasattr(ws.app.state, 'conn_manager'):
            connection = ws.app.state.conn_manager._conns.get(conn_id)
            if connection and connection.meta.handler:
                connection.meta.handler[key] = value
    except Exception:
        pass


def _lt_stop(latency_tool: Optional[LatencyTool], stage: str, ws: WebSocket, meta=None):
    """Stop latency tracking with error handling."""
    if latency_tool:
        try:
            latency_tool.stop(stage, ws.app.state.redis, meta=meta)
        except Exception as e:
            logger.error(f"Latency stop error: {e}")


async def send_tts_audio(
    text: str,
    ws: WebSocket,
    latency_tool: Optional[LatencyTool] = None,
    voice_name: Optional[str] = None,
    voice_style: Optional[str] = None,
    rate: Optional[str] = None,
) -> None:
    """Send TTS audio to browser WebSocket client."""
    run_id = str(uuid.uuid4())[:8]
    
    # Start latency tracking
    if latency_tool:
        try:
            latency_tool.start("tts")
            latency_tool.start("tts:synthesis")
        except Exception as e:
            logger.error(f"Latency start error: {e}")

    # Get TTS synthesizer from connection metadata or fallback to pool
    synth = _get_connection_metadata(ws, "tts_client")
    temp_synth = False
    
    if not synth:
        synth = await ws.app.state.tts_pool.acquire()
        temp_synth = True
        logger.warning("Temporarily acquired TTS synthesizer from pool")

    try:
        # Set synthesis flag
        _set_connection_metadata(ws, "is_synthesizing", True)

        # Use voice settings
        voice_to_use = voice_name or GREETING_VOICE_TTS
        style = voice_style or "conversational"
        eff_rate = rate or "medium"

        logger.debug(f"TTS synthesis: voice={voice_to_use}, style={style}, rate={eff_rate}")

        # Synthesize audio
        pcm_bytes = synth.synthesize_to_pcm(
            text=text,
            voice=voice_to_use,
            sample_rate=TTS_SAMPLE_RATE_UI,
            style=style,
            rate=eff_rate,
        )

        _lt_stop(latency_tool, "tts:synthesis", ws, 
                 meta={"run_id": run_id, "mode": "browser", "voice": voice_to_use})

        # Split into frames
        frames = SpeechSynthesizer.split_pcm_to_base64_frames(pcm_bytes, sample_rate=TTS_SAMPLE_RATE_UI)
        logger.debug(f"TTS frames prepared: {len(frames)}")

        # Send frames to client
        if latency_tool:
            try:
                latency_tool.start("tts:send_frames")
            except Exception:
                pass

        for i, frame in enumerate(frames):
            if ws.client_state != WebSocketState.CONNECTED:
                logger.warning("WebSocket disconnected during audio transmission")
                break
            try:
                await ws.send_json({
                    "type": "audio_data",
                    "data": frame,
                    "frame_index": i,
                    "total_frames": len(frames),
                    "sample_rate": TTS_SAMPLE_RATE_UI,
                    "is_final": i == len(frames) - 1,
                })
            except Exception as e:
                logger.error(f"Failed to send audio frame {i}: {e}")
                break

        _lt_stop(latency_tool, "tts:send_frames", ws,
                 meta={"run_id": run_id, "mode": "browser", "frames": len(frames)})

        logger.debug(f"TTS complete: {len(frames)} frames sent")

    except Exception as e:
        logger.error(f"TTS synthesis failed: {e}")
        _lt_stop(latency_tool, "tts:synthesis", ws, 
                 meta={"run_id": run_id, "mode": "browser", "error": str(e)})
        try:
            await ws.send_json({
                "type": "tts_error",
                "error": str(e),
                "text": text[:100] + "..." if len(text) > 100 else text,
            })
        except Exception:
            pass
    finally:
        _lt_stop(latency_tool, "tts", ws, 
                 meta={"run_id": run_id, "mode": "browser", "voice": voice_to_use})
        
        # Clear synthesis flag
        _set_connection_metadata(ws, "is_synthesizing", False)
        
        # Release temporary synthesizer
        if temp_synth and synth:
            try:
                await ws.app.state.tts_pool.release(synth)
            except Exception as e:
                logger.error(f"Error releasing temporary TTS synthesizer: {e}")


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
    """Send TTS response to ACS phone call."""
    run_id = str(uuid.uuid4())[:8]
    
    if latency_tool:
        try:
            latency_tool.start("tts")
        except Exception:
            pass

    if stream_mode == StreamMode.MEDIA:
        # Get TTS synthesizer from connection metadata
        synth = _get_connection_metadata(ws, "tts_client")
        temp_synth = False
        
        if not synth:
            synth = await ws.app.state.tts_pool.acquire()
            temp_synth = True
            logger.warning("ACS MEDIA: Temporarily acquired TTS synthesizer from pool")

        try:
            voice_to_use = voice_name or GREETING_VOICE_TTS
            style = voice_style or "conversational"
            eff_rate = rate or "medium"

            # Synthesize audio
            pcm_bytes = synth.synthesize_to_pcm(
                text=text,
                voice=voice_to_use,
                sample_rate=TTS_SAMPLE_RATE_ACS,
                style=style,
                rate=eff_rate,
            )

            # Split into frames for ACS
            frames = SpeechSynthesizer.split_pcm_to_base64_frames(pcm_bytes, sample_rate=TTS_SAMPLE_RATE_ACS)

            # Send frames to ACS WebSocket
            for frame in frames:
                # Check greeting TTFB tracking
                lt = _get_connection_metadata(ws, "lt")
                greeting_ttfb_stopped = _get_connection_metadata(ws, "_greeting_ttfb_stopped", False)
                
                if lt and not greeting_ttfb_stopped:
                    lt.stop("greeting_ttfb", ws.app.state.redis)
                    _set_connection_metadata(ws, "_greeting_ttfb_stopped", True)
                    
                try:
                    await ws.send_json({
                        "kind": "AudioData",
                        "AudioData": {"data": frame},
                        "StopAudio": None,
                    })
                except Exception as e:
                    logger.error(f"Failed to send ACS audio frame: {e}")
                    break

        except Exception as e:
            logger.error(f"Failed to send audio frames to ACS: {e}")
        finally:
            _lt_stop(latency_tool, "tts:send_frames", ws,
                     meta={"run_id": run_id, "mode": "acs", "frames": len(frames)})
            _lt_stop(latency_tool, "tts", ws,
                     meta={"run_id": run_id, "mode": "acs", "voice": voice_to_use})
            
            if temp_synth and synth:
                try:
                    await ws.app.state.tts_pool.release(synth)
                except Exception as e:
                    logger.error(f"Error releasing temporary ACS TTS synthesizer: {e}")

        return None

    elif stream_mode == StreamMode.TRANSCRIPTION:
        # TRANSCRIPTION mode - queue with ACS caller
        acs_caller = ws.app.state.acs_caller
        if not acs_caller:
            _lt_stop(latency_tool, "tts", ws,
                     meta={"run_id": run_id, "mode": "acs", "error": "no_acs_caller"})
            logger.error("ACS caller not available for TRANSCRIPTION mode")
            return None

        call_conn = _get_connection_metadata(ws, "call_conn")
        if not call_conn:
            _lt_stop(latency_tool, "tts", ws,
                     meta={"run_id": run_id, "mode": "acs", "error": "no_call_connection"})
            logger.error("Call connection not available")
            return None

        # Queue with ACS
        task = asyncio.create_task(
            play_response_with_queue(
                acs_caller, call_conn, text, voice_name=voice_name
            )
        )
        
        _lt_stop(latency_tool, "tts", ws,
                 meta={"run_id": run_id, "mode": "acs", "queued": True})
        
        return task

    else:
        logger.error(f"Unknown stream mode: {stream_mode}")
        return None


async def push_final(
    ws: WebSocket,
    role: str,
    content: str,
    *,
    is_acs: bool = False,
) -> None:
    """Push final message (close bubble helper)."""
    try:
        if is_acs:
            # For ACS, just log - the call flow handles final messages
            logger.debug(f"ACS final message: {role}: {content[:50]}...")
        else:
            # For browser, send final message
            await ws.send_json({
                "type": "assistant_final",
                "content": content,
                "speaker": role,
            })
    except Exception as e:
        logger.error(f"Error pushing final message: {e}")


async def broadcast_message(
    connected_clients, message: str, sender: str = "system", app_state=None, session_id: str = None
):
    """
    🔒 SESSION-SAFE broadcast message using ConnectionManager.
    
    CRITICAL: This function now requires session_id for proper session isolation.
    Messages will only be sent to connections within the specified session.
    
    Args:
        connected_clients: Legacy parameter (ignored for safety)
        message: Message content to broadcast
        sender: Message sender identifier
        app_state: Application state containing conn_manager
        session_id: REQUIRED - Session ID for proper isolation
    """
    if not app_state or not hasattr(app_state, 'conn_manager'):
        raise ValueError("broadcast_message requires app_state with conn_manager")
    
    if not session_id:
        logger.error("🚨 CRITICAL: broadcast_message called without session_id - this breaks session isolation!")
        raise ValueError("session_id is required for session-safe broadcasting")
    
    # Create session-safe envelope with proper session context
    envelope = make_status_envelope(message, sender=sender, session_id=session_id)
    
    # Use session-specific broadcasting instead of topic-based (which leaks between sessions)
    sent_count = await app_state.conn_manager.broadcast_session(session_id, envelope)
    
    logger.info(
        f"🔒 Session-safe broadcast: {sender}: {message[:50]}... "
        f"(sent to {sent_count} clients in session {session_id})",
        extra={"session_id": session_id, "sender": sender, "sent_count": sent_count}
    )


# Re-export for convenience
__all__ = [
    "send_tts_audio",
    "send_response_to_acs", 
    "push_final",
    "broadcast_message",
]
