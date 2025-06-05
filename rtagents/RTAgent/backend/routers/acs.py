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
import time
from base64 import b64decode
from typing import Dict, Optional
import contextlib

from azure.core.exceptions import HttpResponseError
from azure.core.messaging import CloudEvent
from rtagents.RTAgent.backend.services.acs.acs_helpers import stop_audio
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

# Import AudioStreamFormat from the appropriate SDK
from azure.cognitiveservices.speech.audio import AudioStreamFormat, PushAudioInputStream
from fastapi.responses import JSONResponse
from fastapi.websockets import WebSocketState
from pydantic import BaseModel

from rtagents.RTAgent.backend.orchestration.conversation_state import (
    ConversationManager,
)
from src.aoai.manager_transcribe import AudioTranscriber
from rtagents.RTAgent.backend.helpers import check_for_stopwords
from rtagents.RTAgent.backend.latency.latency_tool import LatencyTool
from rtagents.RTAgent.backend.orchestration.orchestrator import route_turn
from rtagents.RTAgent.backend.shared_ws import (
    broadcast_message,
    send_response_to_acs,
)
from rtagents.RTAgent.backend.postcall.push import build_and_flush
from rtagents.RTAgent.backend.settings import (
    ACS_CALLBACK_PATH,
    ACS_WEBSOCKET_PATH,
    VOICE_TTS
)
from utils.ml_logging import get_logger
from azure.communication.callautomation import TextSource, PhoneNumberIdentifier
from azure.core.credentials import AzureKeyCredential

from azure.communication.callautomation._shared.models import identifier_from_raw_id
import string

logger = get_logger("routers.acs")

router = APIRouter()

class CallRequest(BaseModel):
    target_number: str

# --------------------------------------------------------------------------- #
#  1. Make Call  (POST /api/call)
# --------------------------------------------------------------------------- #
@router.post("/api/call")
async def initiate_call(call: CallRequest, request: Request):
    acs = request.app.state.acs_caller

    if not acs:
        raise HTTPException(503, "ACS Caller not initialised")

    try:
        # TODO: Add logic to reject multiple requests for the same target number
        result = await acs.initiate_call(call.target_number)
        if result.get("status") != "created":
            return JSONResponse({"status": "failed"}, status_code=400)
        call_id = result["call_id"]

        cm = ConversationManager.from_redis(
            session_id=call_id,
            redis_mgr=request.app.state.redis,
        )

        cm.update_context("target_number", call.target_number)
        cm.persist_to_redis(request.app.state.redis)

        logger.info("Call initiated – ID=%s", call_id)
        return {"message": "Call initiated", "callId": call_id}
    except (HttpResponseError, RuntimeError) as exc:
        logger.error("ACS error: %s", exc, exc_info=True)
        raise HTTPException(500, str(exc)) from exc
    

# --------------------------------------------------------------------------- #
#  Answer Call  (POST /api/call/inbound)
# --------------------------------------------------------------------------- #
@router.post("/api/call/inbound")
async def answer_call(request: Request):
    acs = request.app.state.acs_caller
    if not acs:
        raise HTTPException(503, "ACS Caller not initialised")

    try:
        body = await request.json()
        for event in body:
            eventType = event.get("eventType")
            if eventType == "Microsoft.EventGrid.SubscriptionValidationEvent":
                # Handle subscription validation event
                validation_code = event.get("data", {}).get("validationCode")
                if validation_code:
                    return JSONResponse(
                        {
                            "validationResponse": validation_code
                        },
                        status_code=200
                    )
                else:
                    raise HTTPException(400, "Validation code not found in event data")
            else:
                logger.info(f"Received event of type {eventType}: {event}")
                # Handle other event types as needed
                # For now, just acknowledge receipt

        return JSONResponse({"status": "call answered"}, status_code=200)
       
    except (HttpResponseError, RuntimeError) as exc:
        logger.error("ACS error: %s", exc, exc_info=True)
        raise HTTPException(500, str(exc)) from exc
    except Exception as exc:
        logger.error("Error parsing request body: %s", exc, exc_info=True)
        raise HTTPException(400, "Invalid request body") from exc


# --------------------------------------------------------------------------- #
#  2. Callback events  (POST /call/callbacks)
# --------------------------------------------------------------------------- #
@router.post(ACS_CALLBACK_PATH)
async def callbacks(request: Request):
    if not request.app.state.acs_caller:
        return JSONResponse({"error": "ACS not initialised"}, status_code=503)

    try:
        events = await request.json()
        for raw in events:
            event = CloudEvent.from_dict(raw)
            etype = event.type
            cid = event.data.get("callConnectionId")
            redis_mgr = request.app.state.redis

            cm = ConversationManager.from_redis(cid, redis_mgr)


            # if etype == "Microsoft.Communication.ParticipantsUpdated":
            #     # Update the redis cache for the call connection id with participant info
            #     await broadcast_message(
            #         request.app.state.clients,
            #         f"\tParticipants updated for call {cid}",
            #         "System",
            #     )
            if etype == "Microsoft.Communication.CallConnected":
                participants = event.data.get("participants", [])
                participant_count = len(participants)
                # Check if the target participant has joined
                acs_caller = request.app.state.acs_caller
                target_joined = False
                if acs_caller and hasattr(acs_caller, "target_participant"):
                    target_raw_id = getattr(acs_caller.target_participant, "raw_id", None)
                    target_joined = any(
                        p.get("rawId") == target_raw_id for p in participants
                    )
                await broadcast_message(
                    request.app.state.clients,
                    f"Participants updated for call {cid} (count: {participant_count}, target joined: {target_joined})",
                    "System",
                )

            if etype == "Microsoft.Communication.TranscriptionFailed":
                # Attempt to restart transcription if it fails
                try:
                    acs_caller = request.app.state.acs_caller
                    if acs_caller and hasattr(acs_caller, "call_automation_client"):
                        call_connection_client = acs_caller.get_call_connection(cid)
                        call_connection_client.start_transcription()
                        logger.info(f"Attempted to restart transcription for call {cid}")
                except Exception as e:
                    logger.error(f"Failed to restart transcription for call {cid}: {e}")
                reason = event.data.get("resultInformation", "Unknown reason")
                logger.error(f"⚠️ {etype} for call {cid}: {reason}")

            if etype == "Microsoft.Communication.CallDisconnected":
                logger.info(f"❌ Call disconnected for call {cid}")
                # Log additional details for debugging
                disconnect_reason = event.data.get("resultInformation", "No resultInformation provided")
                participants = event.data.get("participants", [])
                logger.info(f"Disconnect reason: {disconnect_reason}")
                logger.info(f"Participants at disconnect: {participants}")
                # Optionally, clean up conversation state or resources
                try:
                    cm.persist_to_redis(request.app.state.redis)
                    logger.info(f"Persisted conversation state after disconnect for call {cid}")
                except Exception as e:
                    logger.error(f"Failed to persist conversation state after disconnect for call {cid}: {e}")

            if etype == "Microsoft.Communication.TranscriptionFailed":
                reason = event.data.get("resultInformation", "Unknown reason")
                logger.error(f"⚠️ Transcription failed for call {cid}: {reason}")

            elif "Failed" in etype:
                reason = event.data.get("resultInformation", "Unknown reason")
                logger.error("⚠️ %s for call %s: %s", etype, cid, reason)

            else:
                logger.info("%s %s", etype, cid)
        return {"status": "callback received"}
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Callback error: %s", exc, exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)

from rtagents.RTAgent.backend.services.acs.acs_helpers import play_response
# --------------------------------------------------------------------------- #
#  3. Media-streaming WebSocket  (WS /call/stream)
# --------------------------------------------------------------------------- #
call_user_raw_ids: Dict[str, str] = {}
@router.websocket(ACS_WEBSOCKET_PATH)
async def acs_transcription_ws(ws: WebSocket):
    await ws.accept()
    acs = ws.app.state.acs_caller
    
    cid = ws.headers["x-ms-call-connection-id"]
    cm = ConversationManager.from_redis(cid, ws.app.state.redis)
    target_phone_number = cm.get_context("target_number")
    
    if not target_phone_number:
        logger.debug(f"No target phone number found for session {cm.session_id}")

    ws.app.state.target_participant = PhoneNumberIdentifier(target_phone_number)
    ws.app.state.cm = cm
    ws.state.lt = LatencyTool(cm)  # Initialize latency tool without context

    # 1) seed flags from Redis
    greeted       = cm.context.get("greeted", False)
    bot_speaking  = cm.context.get("bot_speaking", False)
    interrupt_cnt = cm.context.get("interrupt_count", 0)

    call_conn = acs.get_call_connection(cid)    # 2) optional greeting
    if not greeted:
        greeting = (
            "Hello, thank you for calling XMYX Healthcare Company. "
            "Before I can assist you, let's verify your identity. "
            "How may I address you today? Please state your full name clearly after the tone, "
            "and let me know how I can help you with your healthcare needs."
        )
        # await call_conn.play_media(play_source=TextSource(text=greeting), interrupt_call_media_operations=True)
        await play_response(
            ws,
            response_text=greeting,
            participants=[ws.app.state.target_participant]
        )

        # play_response handles bot_speaking flag, just mark as greeted
        greeted = True
        cm.update_context("greeted", True)
        cm.persist_to_redis(ws.app.state.redis)

    while True:
        try:
            text_data = await ws.receive_text()
            msg = json.loads(text_data)
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected by client")
            break
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from WebSocket: {e}")
            continue
        except Exception as e:
            logger.error(f"Unexpected error in WebSocket receive loop: {e}", exc_info=True)
            break

        try:
            if msg.get("kind") != "TranscriptionData":
                continue

            td     = msg["transcriptionData"]
            text   = td["text"].strip()
            words  = text.split()
            status = td["resultStatus"]   # "Intermediate" or "Final"
            conf   = td.get("confidence", 0.0)            # 3) debounce only if bot is speaking
            if status == "Intermediate" and bot_speaking:
                ok = len(words) >= 2 and conf >= 0.75
                interrupt_cnt = interrupt_cnt + 1 if ok else 0
                logger.info("🔊 Intermediate transcription: '%s' (status: %s, conf: %.2f)", text, status, conf)
                cm.update_context("interrupt_count", interrupt_cnt)
                cm.persist_to_redis(ws.app.state.redis)

                if interrupt_cnt >= 3:
                    logger.info("🔊 User interruption detected – stopping TTS")
                    try:
                        await call_conn.cancel_all_media_operations()
                    except Exception as cancel_error:
                        logger.error(f"Error canceling media operations: {cancel_error}")
                    bot_speaking  = False
                    interrupt_cnt = 0
                    cm.update_context("bot_speaking", False)
                    cm.update_context("interrupt_count", 0)
                    cm.persist_to_redis()
                continue

            # 4) on final, reset counter and handle user turn
            if status == "Final":
                interrupt_cnt = 0
                cm.update_context("interrupt_count", 0)
                cm.persist_to_redis()

                # broadcast and route user text
                await broadcast_message(ws.app.state.clients, text, "User")
                # ensure any lingering TTS is stopped
                if bot_speaking:
                    try:
                        await call_conn.cancel_all_media_operations()
                    except Exception as cancel_error:
                        logger.error(f"Error canceling media operations: {cancel_error}")
                    bot_speaking = False
                    cm.update_context("bot_speaking", False)
                    cm.persist_to_redis(ws.app.state.redis)

                # finally hand off to your orchestrator
                # when it TTS's again, wrap that call with setting bot_speaking=True
                response = await route_turn(cm, text, ws, is_acs=True)

        except Exception as e:
            logger.error(f"Error processing transcription message: {e}", exc_info=True)
            # Continue processing other messages rather than breaking
            continue
