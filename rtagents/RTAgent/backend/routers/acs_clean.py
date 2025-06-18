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

    try:
        events = await request.json()
        result = await ACSHandler.process_callback_events(
            events=events,
            acs_caller=request.app.state.acs_caller,
            redis_mgr=request.app.state.redis,
            clients=request.app.state.clients
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
@router.websocket(ACS_WEBSOCKET_PATH)
async def acs_media_ws(ws: WebSocket):
    """Handle ACS WebSocket media streaming."""
    speech = ws.app.state.stt_client
    acs = ws.app.state.acs_caller
    if not speech or not acs:
        await ws.close(code=1011)
        return

    await ws.accept()
    cid = ws.headers.get("x-ms-call-connection-id", "UnknownCall")
    
    # Delegate to handler
    await ACSHandler.handle_websocket_media_stream(
        ws=ws,
        acs_caller=acs,
        redis_mgr=ws.app.state.redis,
        clients=ws.app.state.clients,
        cid=cid,
        speech_client=speech
    )


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
