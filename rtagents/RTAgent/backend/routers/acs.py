"""
routers/acs.py
==============
Outbound phone-call flow via Azure Communication Services.

• POST  /call             – start a phone call
• POST  /call/callbacks   – receive ACS events
• WS    /call/stream      – bidirectional PCM audio stream
"""

from __future__ import annotations

import asyncio
import json
from typing import Dict, Optional

from azure.communication.callautomation import PhoneNumberIdentifier
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from rtagents.RTAgent.backend.orchestration.conversation_state import ConversationManager
from rtagents.RTAgent.backend.handlers.acs_handler import ACSHandler
from rtagents.RTAgent.backend.latency.latency_tool import LatencyTool
from rtagents.RTAgent.backend.settings import (
    ACS_CALLBACK_PATH,
    ACS_WEBSOCKET_PATH,
)
from utils.ml_logging import get_logger

logger = get_logger("routers.acs")
router = APIRouter()

class CallRequest(BaseModel):
    target_number: str

# --------------------------------------------------------------------------- #
#  1. Make Call  (POST /api/call)
# --------------------------------------------------------------------------- #
@router.post("/api/call")
async def initiate_call(call: CallRequest, request: Request):
    """Initiate an outbound call through ACS."""
    result = await ACSHandler.initiate_call(
        acs_caller=request.app.state.acs_caller,
        target_number=call.target_number,
        redis_mgr=request.app.state.redis
    )
    
    if result["status"] == "success":
        return {"message": result["message"], "callId": result["callId"]}
    else:
        return JSONResponse(result, status_code=400)


# --------------------------------------------------------------------------- #
#  Answer Call  (POST /api/call/inbound)
# --------------------------------------------------------------------------- #
@router.post("/api/call/inbound")
async def answer_call(request: Request):
    """Handle inbound call events and subscription validation."""
    try:
        body = await request.json()
        return await ACSHandler.handle_inbound_call(
            request_body=body,
            acs_caller=request.app.state.acs_caller
        )
    except Exception as exc:
        logger.error("Error parsing request body: %s", exc, exc_info=True)
        raise HTTPException(400, "Invalid request body") from exc


# --------------------------------------------------------------------------- #
#  2. Callback events  (POST /call/callbacks)
# --------------------------------------------------------------------------- #
@router.post(ACS_CALLBACK_PATH)
async def callbacks(request: Request):
    """Handle ACS callback events."""
    if not request.app.state.acs_caller:
        return JSONResponse({"error": "ACS not initialised"}, status_code=503)

    if not request.app.state.stt_client:
        return JSONResponse({"error": "STT client not initialised"}, status_code=503)

    try:
        events = await request.json()
        result = await ACSHandler.process_callback_events(
            events=events,
            request=request,
        )
        
        if "error" in result:
            return JSONResponse(result, status_code=500)
        return result
        
    except Exception as exc:
        logger.error("Callback error: %s", exc, exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)


# --------------------------------------------------------------------------- #
#  3. Media callback events  (POST /api/media/callbacks)
# --------------------------------------------------------------------------- #
@router.post("/api/media/callbacks")
async def media_callbacks(request: Request):
    """Handle media callback events."""
    try:
        events = await request.json()
        cm = request.app.state.cm
        result = await ACSHandler.process_media_callbacks(
            events=events,
            cm=cm,
            redis_mgr=request.app.state.redis
        )
        
        if "error" in result:
            return JSONResponse(result, status_code=500)
        return result
        
    except Exception as exc:
        logger.error("Media callback error: %s", exc, exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)


# --------------------------------------------------------------------------- #
#  4. Media-streaming WebSocket  (WS /call/stream)
# --------------------------------------------------------------------------- #
# @router.websocket(ACS_WEBSOCKET_PATH)
# async def acs_media_streaming_ws(ws: WebSocket):
#     """Handle ACS WebSocket media streaming."""
#     await ws.accept()
#     acs = ws.app.state.acs_caller
#     redis_mgr = ws.app.state.redis

#     cid = ws.headers["x-ms-call-connection-id"]
#     cm = ConversationManager.from_redis(cid, redis_mgr)
#     target_phone_number = cm.get_context("target_number")
    
#     if not target_phone_number:
#         logger.debug(f"No target phone number found for session {cm.session_id}")

#     ws.app.state.target_participant = PhoneNumberIdentifier(target_phone_number)
#     ws.app.state.cm = cm
#     ws.state.lt = LatencyTool(cm)

#     call_conn = acs.get_call_connection(cid)

#     while True:
#         try:
#             data = await ws.receive_bytes()
#         except asyncio.TimeoutError:
#             continue
#         except WebSocketDisconnect:
#             logger.info("WebSocket disconnected by client")
#             break
#         except Exception as e:
#             logger.error(f"Unexpected error in WebSocket receive loop: {e}", exc_info=True)
#             break

#         # Process media data using handler
#         try:
#             await ACSHandler.handle_websocket_media(
#                 ws=ws,
#                 data=data,
#                 cm=cm,
#                 redis_mgr=redis_mgr,
#                 call_conn=call_conn,
#                 clients=ws.app.state.clients
#             )
#         except Exception as e:
#             logger.error(f"Error processing media data: {e}", exc_info=True)
#             continue

from rtagents.RTAgent.backend.orchestration.orchestrator import route_turn
from base64 import b64decode
# @router.websocket(ACS_WEBSOCKET_PATH)
@router.websocket("/call/stream")
async def acs_media_ws(ws: WebSocket):
    """
    Handle WebSocket media streaming for ACS calls.

    Args:
        ws: WebSocket connection
        recognizer: Speech-to-text recognizer instance
        cm: ConversationManager instance
        redis_mgr: Redis manager instance
        clients: List of connected WebSocket clients
        cid: Call connection ID
    """
    try:
        await ws.accept()

        recognizer = ws.app.state.stt_client
        redis_mgr = ws.app.state.redis
        cid = ws.headers.get("x-ms-call-connection-id")
        cm = ConversationManager.from_redis(cid, redis_mgr)

        # Define handlers for partial and final results
        def on_partial_result(text, lang):
            """Handle partial transcription."""
            logger.info(f"🗣️ User (partial) in {lang}: {text}")
            # Set flag to interrupt
            cm.set_tts_interrupted(True)
            cm.persist_to_redis(redis_mgr)



        def on_final_result(text, lang):
            """Handle final transcription."""
            logger.info(f"🧾 User (final) in {lang}: {text}")
            # Reset interrupt flag
            cm.set_tts_interrupted(False)
            cm.persist_to_redis(redis_mgr)

            # Route the final text to the gpt orchestrator
            asyncio.create_task(route_turn(cm, text, ws, is_acs=True))

        # Initialize recognizer with handlers
        recognizer.set_partial_result_callback(on_partial_result)
        recognizer.set_final_result_callback(on_final_result)

        # Start recognition
        recognizer.start()

        # Main processing loop
        while True:
            try:
                raw_data = await ws.receive_text()
                data = json.loads(raw_data)

                if data.get("kind") == "AudioMetadata":
                    # Log metadata attributes cleanly
                    logger.info(f"📊 Metadata: {json.dumps(data, indent=2)}")
                elif data.get("kind") == "AudioData":
                    # Extract and decode audio data
                    audioData = data.get("audioData", "")
                    if not audioData:
                        logger.warning("Received empty audio data")
                        continue
                    audio_bytes = audioData.get("data", "")
                    target_participant = audioData.get("participantRawID", "")
                    timestamp = audioData.get("timestamp", None)
                    # Write audio data to recognizer queue
                    # Convert audio_bytes from base64 string to bytes if needed
                    if isinstance(audio_bytes, str):
                        try:
                            audio_bytes = b64decode(audio_bytes)
                        except Exception as e:
                            logger.error(f"Failed to decode base64 audio data: {e}")
                            continue
                    recognizer.write_bytes(audio_bytes)
                else:
                    # Handle other data types
                    logger.debug(f"Received unknown data type: {data.get('kind', 'unknown')}")
                # Ensure audio_data is bytes

            except WebSocketDisconnect:
                logger.info("WebSocket disconnected")
                break
            except Exception as e:
                logger.error(f"Error in WebSocket media stream: {e}", exc_info=True)
                break

    finally:
        recognizer.stop()
        logger.info("Recognition stopped")


    # from fastapi import FastAPI, WebSocket
    # await ws.accept()
    # print("✅ WebSocket connected")
    # # try:
    # #     while True:
    # #         data = await ws.receive_text()
    # #         await ws.send_text(f"Echo: {data}")
    # # except Exception as e:
    # #     print(f"WebSocket closed: {e}")

    # """Handle ACS WebSocket media streaming."""
    # acs = ws.app.state.acs_caller
    # redis_mgr = ws.app.state.redis
    # speech = ws.app.state.stt_client

    # if not speech or not acs:
    #     await ws.close(code=1011)
    #     return

    # cid = ws.headers["x-ms-call-connection-id"]
    # cm = ConversationManager.from_redis(cid, redis_mgr)
    # target_phone_number = cm.get_context("target_number")
    # ws.app.state.target_participant = PhoneNumberIdentifier(target_phone_number)

    # # Delegate to handler
    # await ACSHandler.handle_websocket_media_stream(
    #     ws=ws,
    #     acs_caller=acs,
    #     redis_mgr=ws.app.state.redis,
    #     clients=ws.app.state.clients,
    #     cid=cid,
    #     speech_client=speech
    # )

@router.websocket("/call/transcription")
async def acs_transcription_ws(ws: WebSocket):
    """Handle ACS WebSocket transcription stream."""
    await ws.accept()
    acs = ws.app.state.acs_caller
    redis_mgr = ws.app.state.redis

    cid = ws.headers["x-ms-call-connection-id"]
    cm = ConversationManager.from_redis(cid, redis_mgr)
    target_phone_number = cm.get_context("target_number")
    
    if not target_phone_number:
        logger.debug(f"No target phone number found for session {cm.session_id}")

    ws.app.state.target_participant = PhoneNumberIdentifier(target_phone_number)
    ws.app.state.cm = cm
    ws.state.lt = LatencyTool(cm)  # Initialize latency tool

    call_conn = acs.get_call_connection(cid)
    
    # Main WebSocket processing loop
    while True:
        try:
            text_data = await ws.receive_text()
            msg = json.loads(text_data)
        except asyncio.TimeoutError:
            continue
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected by client")
            break
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from WebSocket: {e}")
            continue
        except Exception as e:
            logger.error(f"Unexpected error in WebSocket receive loop: {e}", exc_info=True)
            break

        # Process transcription message using handler
        try:
            await ACSHandler.handle_websocket_transcription(
                ws=ws,
                message=msg,
                cm=cm,
                redis_mgr=redis_mgr,
                call_conn=call_conn,
                clients=ws.app.state.clients
            )
        except Exception as e:
            logger.error(f"Error processing transcription message: {e}", exc_info=True)
            continue

