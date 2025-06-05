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
    ACS_WEBSOCKET_PATH
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

        cm = await ConversationManager.from_redis(
            session_id=call_id,
            redis_mgr=request.app.state.redis,
        )

        cm.update_context("target_number", call.target_number)
        await cm.persist_to_redis(request.app.state.redis)

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

            cm = await ConversationManager.from_redis(cid, redis_mgr)

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
                    await cm.persist_to_redis(request.app.state.redis)
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


# --------------------------------------------------------------------------- #
#  3. Media-streaming WebSocket  (WS /call/stream)
# --------------------------------------------------------------------------- #
call_user_raw_ids: Dict[str, str] = {}

# @router.websocket("/call/transcription")
@router.websocket(ACS_WEBSOCKET_PATH)
async def acs_transcription_ws(ws: WebSocket):
    await ws.accept()

    acs = ws.app.state.acs_caller
    cid = ws.headers.get("x-ms-call-connection-id", "UnknownCall")
    redis_mgr = ws.app.state.redis
    call_conn_client = acs.get_call_connection(cid)
    cm = await ConversationManager.from_redis(
        session_id=cid,
        redis_mgr=redis_mgr,
    )
    ws.state.lt = LatencyTool(cm)  # Initialize latency tool without context

    # Simple VAD PoC
    interrupt_count = int(cm.context.get("interrupt_count", 0))

    while True:
        try:
            target_phone_number = cm.get_context("target_number")
            if not target_phone_number:
                logger.debug(f"No target phone number found for session {cm.session_id}")
                continue
            target_participant = PhoneNumberIdentifier(target_phone_number)
            greeted = cm.get_context("greeted") or False
            print(f"Current greeted state: {greeted}")
            if ws.client_state != WebSocketState.CONNECTED:
                print("WebSocket is not connected. Closing handler loop.")
                break
            # Check if the session has been greeted
            if not greeted:
                logger.info(f"Greeting user for session {cm.session_id}")
                greeting_text = (
                    "Hello from XMYX Healthcare Company! Before I can assist you, "
                    "let's verify your identity. How may I address you?"
                )
                call_conn_client.play_media(
                    play_source=TextSource(
                        text=greeting_text,
                        source_locale="en-US",
                        voice_name="en-US-JennyNeural",
                    ),
                    interrupt_call_media_operations=True,
                    play_to=[
                        target_participant
                    ]
                )
                cm.context["greeted"] = True
                await cm.persist_to_redis(ws.app.state.redis)
                await broadcast_message(
                    ws.app.state.clients,
                    greeting_text,
                    "Assistant",
                )

            msg = json.loads(await ws.receive_text())
        
            if msg["kind"] == "TranscriptionData" and msg["transcriptionData"]["resultStatus"] == "Intermediate":
                text = msg["transcriptionData"]["text"]
                words = text.strip().split()

                # your debounce rules:
                ok = len(words) >= 2
                interrupt_count = interrupt_count + 1 if ok else 0

                if interrupt_count >= 3:
                    logger.info("🛑 Debounced user interruption")
                    call_conn_client.cancel_all_media_operations()
                    interrupt_count = 0
                    await broadcast_message(
                        ws.app.state.clients,
                        "User voice detected.",
                        "System",
                    )

                # persist back to Redis
                cm.update_context("interrupt_count", interrupt_count)
                await cm.persist_to_redis(redis_mgr)

            elif msg["kind"] == "TranscriptionData" and msg["transcriptionData"]["resultStatus"] == "Final":
                # clear counter on final
                cm.update_context("interrupt_count", 0)
                # conf = msg["transcriptionData"]["confidence"]

                await cm.persist_to_redis(redis_mgr)
                final_text = msg["transcriptionData"]["text"]
                await broadcast_message(
                    ws.app.state.clients,
                    final_text,
                    "User",
                )
                call_conn_client.cancel_all_media_operations()
                await route_turn(cm, final_text, ws, is_acs=True)
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected")
            break
        except Exception as e:
            logger.error(f"Error processing WebSocket message: {e}", exc_info=True)
            break

    #     except json.JSONDecodeError as e:
    #         try:
    #             should_greet = cm.get_context("should_greet") or False
    #             print(f"Current greeted state: {should_greet}")
    #             if ws.client_state != WebSocketState.CONNECTED:
    #                 print("WebSocket is not connected. Closing handler loop.")
    #                 break
    #             # Check if the session has been greeted

    #             message = await ws.receive()
    #             if message.get("type") == "websocket.disconnect":
    #                 print("WebSocket disconnect received. Closing connection.")
    #                 break
    #             if "text" in message:
    #                 await process_websocket_message_async(ws, cm, call_conn_client, message["text"])
    #             elif "bytes" in message:
    #                 await process_websocket_message_async(ws, cm, call_conn_client, message["bytes"].decode("utf-8"))
    #             else:
    #                 print("Received message with unknown format:", message)
    #         except Exception as e:
    #             print(f"Error while receiving message: {e}")
    #             break  # Close connection on error
    # except Exception as e:
    #     print(f"WebSocket connection closed: {e}")
    # finally:
    #     # Any cleanup or final logs can go here
    #     print("WebSocket connection closed")

from rtagents.RTAgent.backend.orchestration.gpt_flow import process_gpt_response
async def process_websocket_message_async(ws, cm, call_conn_client, message):
        print("Client connected")
        json_object = json.loads(message)
        kind = json_object['kind']
        print(kind)
        if kind == 'TranscriptionMetadata':
            print("Transcription metadata")
            print("-------------------------")
            print("Subscription ID:", json_object['transcriptionMetadata']['subscriptionId'])
            print("Locale:", json_object['transcriptionMetadata']['locale'])
            print("Call Connection ID:", json_object['transcriptionMetadata']['callConnectionId'])
            print("Correlation ID:", json_object['transcriptionMetadata']['correlationId'])
        if kind == 'TranscriptionData':
            participant = identifier_from_raw_id(json_object['transcriptionData']['participantRawID'])
            word_data_list = json_object['transcriptionData'].get('words', [])
            print("Transcription data")
            print("-------------------------")
            # Use .get() with defaults to avoid KeyError if fields are missing
            transcription_data = json_object.get('transcriptionData', {})
            print("Text:", transcription_data.get('text', ''))
            print("Format:", transcription_data.get('format', ''))
            print("Confidence:", transcription_data.get('confidence', 0.0))
            print("Offset:", transcription_data.get('offset', 0))
            print("Duration:", transcription_data.get('duration', 0))
            print("Participant:", getattr(participant, 'raw_id', ''))
            print("Result Status:", transcription_data.get('resultStatus', ''))
            for word in transcription_data.get('words', []):
                print("Word:", word.get('text', ''))
                print("Offset:", word.get('offset', 0))
                print("Duration:", word.get('duration', 0))

            # Core VAD logic
            # 1. IF intermediate data is received
            # 2. Cancel all media being output
            # 3. Wait until final result is received
            # 4. Process final result, add to the conversation state
            # 5. Play the processed result back to the user
            # 6. repeat.

            if transcription_data.get('resultStatus') == 'Intermediate':
                print("Intermediate transcription received.")
                sanitized_text = transcription_data.get('text', '').strip()
                sanitized_text = sanitized_text.translate(str.maketrans('', '', string.punctuation))
                # Play a dial tone to indicate interrupt has been detected
                try:
                    call_conn_client.cancel_all_media_operations()
                    call_conn_client.play_media(
                        play_source=TextSource(
                            text="VAD",
                            source_locale="en-US",
                            voice_name="en-US-JennyNeural"
                        )
                    )
                    print("VAD played to indicate interrupt.")
                except Exception as e:
                    print(f"Failed to play VAD: {e}")
                print("Transcription succeeded.")
            else:
                print("Transcription failed.")

            if transcription_data.get('resultStatus') == 'Final':
                words_list = transcription_data.get('words', [])
                print("Final transcription received.")
                # Combine the list of words into a single sentence
                user_prompt = " ".join(word.get('text', '') for word in words_list).strip()
                print(f"\tFinal Text: {user_prompt}")
                await cm.append_to_history("user", user_prompt)
                call_conn_client.cancel_all_media_operations()

                result = await process_gpt_response(
                    cm,
                    user_prompt=user_prompt,
                    ws=ws,
                    is_acs=True,
                )

                print("Result from GPT processing:", result)
                # if result:
                #     call_conn_client.cancel_all_media_operations()
                #     # Play the response back to the user
                #     await call_conn_client.play_media(
                #         play_source=TextSource(
                #             text=result,
                #             source_locale="en-US",
                #             voice_name="en-US-JennyNeural"
                #         )
                #     )
                #     print("Response played back to user:", result)


    # clients = ws.app.state.clients
    # greeted: set[str] = ws.app.state.greeted_call_ids
    # if cid not in greeted:
    #     greet = (
    #         "Hello from Transcription XMYX Healthcare Company! Before I can assist you, "
    #         "let's verify your identity. How may I address you?"
    #     )
    #     await broadcast_message(clients, greet, "Assistant")
    #     # participants_list = []
    #     # try:
    #     #     participants_data = cm.context.get("participants", "[]")
    #     #     if isinstance(participants_data, str):
    #     #         participants_list = json.loads(participants_data)
    #     #     else:
    #     #         participants_list = participants_data
    #     # except Exception as e:
    #     #     logger.warning(f"Could not parse participants for call {cid}: {e}")

    #     # logger.info(f"Current session participants for call {cid}: {participants_list}")
    #     acs.play_response(
    #         cid,
    #         response_text=greet,
    #     )
    #     cm.append_to_history("assistant", greet)
    #     greeted.add(cid)
    # try:
    #     while True:
    #         msg = await ws.receive_text()
    #         logger.info(f"Received message from {cid}: {msg}")
    #         # Process the message as needed
    # except Exception as e:
    #     logger.info(f"WebSocket connection closed for {cid}: {e}")
    # finally:
    #     await ws.close()

# @router.websocket("/call/stream")
# async def acs_media_ws(ws: WebSocket):
    # acs = ws.app.state.acs_caller
    # await ws.accept()
    # cid = ws.headers.get("x-ms-call-connection-id", "UnknownCall")
    # ws.state.lt = LatencyTool(cm)
    # logger.info("▶ media WS connected – %s", cid)
    # redis_mgr = ws.app.state.redis
    # cm = ConversationManager.from_redis(cid, redis_mgr)

    # clients = ws.app.state.clients
    # greeted: set[str] = ws.app.state.greeted_call_ids
    # if cid not in greeted:
    #     greet = (
    #         "Hello from XMYX Healthcare Company! Before I can assist you, "
    #         "let's verify your identity. How may I address you?"
    #     )
    #     await broadcast_message(clients, greet, "Assistant")
    #     await acs.play_response(cid, response_text=greet)
    #     await cm.append_to_history("assistant", greet)
    #     greeted.add(cid)
    #     try:
    #         async for message in ws:
    #             json_object = json.loads(message)
    #             kind = json_object.get("kind")
    #             if kind == "AudioData":
    #                 audio_data = json_object["audioData"]["data"]
    #                 logger.info(f"Received AudioData: {audio_data[:50]}... (truncated)")
    #             elif kind == "AudioMetadata":
    #                 audio_metadata = json_object.get("audioMetadata", {})
    #                 logger.info(f"Received AudioMetadata: {audio_metadata}")
    #     except Exception as e:
    #         logger.error(f"Error in WebSocket connection: {e}", exc_info=True)
    #         await ws.close(code=1011)
    #         return

    # # ----------------------------------------------------------------------- #
    # #  Local objects
    # # ----------------------------------------------------------------------- #
    # queue: asyncio.Queue[str] = asyncio.Queue()
    # push_stream = PushAudioInputStream(
    #     stream_format=AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
    # )
    # recogniser = speech.create_realtime_recognizer(
    #     push_stream=push_stream,
    #     loop=asyncio.get_event_loop(),
    #     message_queue=queue,
    #     language="en-US",
    #     vad_silence_timeout_ms=500,
    # )
    # recogniser.start_continuous_recognition_async()

    # redis_mgr = ws.app.state.redis
    # cm = ConversationManager.from_redis(cid, redis_mgr)

    # clients = ws.app.state.clients
    # greeted: set[str] = ws.app.state.greeted_call_ids
    # if cid not in greeted:
    #     greet = (
    #         "Hello from XMYX Healthcare Company! Before I can assist you, "
    #         "let’s verify your identity. How may I address you?"
    #     )
    #     await broadcast_message(clients, greet, "Assistant")
    #     await send_response_to_acs(ws, greet)
    #     cm.append_to_history("assistant", greet)
    #     greeted.add(cid)

    # user_raw_id = call_user_raw_ids.get(cid)

    # try:
    #     # --- inside acs_media_ws ---------------------------------------------------
    #     while True:
    #         spoken: str | None = None
    #         try:
    #             while True:
    #                 item = queue.get_nowait()
    #                 spoken = f"{spoken} {item}".strip() if spoken else item
    #                 queue.task_done()
    #         except asyncio.QueueEmpty:
    #             pass

    #         if spoken:
    #             ws.app.state.tts_client.stop_speaking()
    #             for t in list(getattr(ws.app.state, "tts_tasks", [])):
    #                 t.cancel()

    #             await broadcast_message(clients, spoken, "User")

    #             if check_for_stopwords(spoken):
    #                 await broadcast_message(clients, "Goodbye!", "Assistant")
    #                 await send_response_to_acs(ws, "Goodbye!", blocking=True)
    #                 await asyncio.sleep(1)
    #                 await acs.disconnect_call(cid)
    #                 break

    #             await route_turn(cm, spoken, ws, is_acs=True)
    #         try:
    #             raw = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
    #             data = json.loads(raw)
    #         except asyncio.TimeoutError:
    #             if ws.client_state != WebSocketState.CONNECTED:
    #                 break
    #             continue
    #         except (WebSocketDisconnect, json.JSONDecodeError):
    #             break

    #         kind = data.get("kind")
    #         if kind == "AudioData":
    #             # dynamically learn / confirm the caller’s participantRawID
    #             if not user_raw_id and cid in call_user_raw_ids:
    #                 user_raw_id = call_user_raw_ids[cid]

    #             if user_raw_id and data["audioData"]["participantRawID"] != user_raw_id:
    #                 continue        # discard bot’s own audio

    #             try:
    #                 push_stream.write(b64decode(data["audioData"]["data"]))
    #             except Exception:
    #                 # keep going even if decode glitches
    #                 continue

    #         elif kind == "CallConnected":
    #             pid = data["callConnected"]["participant"]["rawID"]
    #             call_user_raw_ids[cid] = pid
    #             user_raw_id = pid

    # finally:
    #     try:
    #         recogniser.stop_continuous_recognition_async()
    #     except Exception:  # pylint: disable=broad-except
    #         pass
    #     push_stream.close()
    #     await ws.close()
    #     call_user_raw_ids.pop(cid, None)
    #     cm.persist_to_redis(redis_mgr)
    #     logger.info("◀ media WS closed – %s", cid)


# @router.websocket(ACS_WEBSOCKET_PATH)
# async def acs_media_ws(ws: WebSocket):
#     acs = ws.app.state.acs_caller
#     if not acs:
#         await ws.close(code=1011)
#         return

#     await ws.accept()
#     cid = ws.headers.get("x-ms-call-connection-id", "UnknownCall")
#     logger.info("▶ media WS connected – %s", cid)

#     # ── per-call objects ────────────────────────────────────────────────────
#     redis_mgr = ws.app.state.redis
#     cm = ConversationManager.from_redis(cid, redis_mgr)
#     ws.state.cm = cm
#     ws.state.lt = LatencyTool(cm)

#     # greeting (once per call)
#     if cid not in ws.app.state.greeted_call_ids:
#         greet = (
#             "Hello from XMYX Healthcare Company! Before I can assist you, "
#             "let’s verify your identity. How may I address you?"
#         )
#         await broadcast_message(ws.app.state.clients, greet, "Assistant")
#         await send_response_to_acs(ws, greet)
#         cm.append_to_history("assistant", greet)
#         ws.app.state.greeted_call_ids.add(cid)

#     # ---------- AOAI Streaming STT ----------------------------------------
#     aoai_cfg = ws.app.state.aoai_stt_cfg
#     audio_q: asyncio.Queue[Optional[bytes]] = asyncio.Queue()

#     async def on_delta(d: str):
#         """
#         Handle partial speech detected by AOAI STT.

#         - Stop ACS playback (cancel any audio bot is sending to phone).
#         - Stop sending audio from browser to ACS.
#         - Stop any local TTS audio playback.
#         - Broadcast the partial transcription.
#         """
#         ws.app.state.tts_stop_flag = True
#         if not d.strip():
#             return

#         try:
#             call_connection = acs.call_automation_client.get_call_connection(cid)
#             await call_connection.cancel_all_media_operations()
#             logger.info(f"[🛑] Stopped ACS playback for call {cid}.")
#         except Exception as e:
#             logger.warning(f"[!] Could not stop ACS playback: {e}")

#         try:
#             await stop_audio(ws)
#             logger.info(f"[🛑] Stopped audio from browser to ACS for call {cid}.")
#         except Exception as e:
#             logger.warning(f"[!] Could not stop browser audio: {e}")

#         # Broadcast the partial transcription to connected dashboards
#         await broadcast_message(ws.app.state.clients, d, "User")

#     lt: LatencyTool = ws.state.lt

#     async def on_transcript(t: str):
#         logger.info(f"[AOAI-STT] {t}")
#         await broadcast_message(ws.app.state.clients, t, "User")
#         lt.stop("stt", ws.app.state.redis)

#         # Stop local TTS
#         ws.app.state.tts_client.stop_speaking()
#         for task in list(getattr(ws.app.state, "tts_tasks", [])):
#             task.cancel()

#         # Main dialog routing
#         await route_turn(cm, t, ws, is_acs=True)

#     transcriber = AudioTranscriber(
#         url=aoai_cfg["url"],
#         headers=aoai_cfg["headers"],
#         rate=aoai_cfg["rate"],
#         channels=aoai_cfg["channels"],
#         format_=aoai_cfg["format_"],
#         chunk=1024,
#     )

#     transcribe_task = asyncio.create_task(
#         transcriber.transcribe(
#             audio_queue=audio_q,
#             model="gpt-4o-transcribe",
#             prompt="Respond in English. This is a medical environment.",
#             noise_reduction="near_field",
#             vad_type="server_vad",
#             vad_config=aoai_cfg["vad"],
#             on_delta=lambda d: asyncio.create_task(on_delta(d)),
#             on_transcript=lambda t: asyncio.create_task(on_transcript(t)),
#         )
#     )

#     user_raw_id: Optional[str] = call_user_raw_ids.get(cid)
#     try:
#         while True:
#             try:
#                 raw = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
#                 data = json.loads(raw)
#             except asyncio.TimeoutError:
#                 if ws.client_state != WebSocketState.CONNECTED:
#                     break
#                 continue
#             except (WebSocketDisconnect, json.JSONDecodeError):
#                 break

#             kind = data.get("kind")
#             if kind == "AudioData":
#                 # dynamically learn / confirm the caller’s participantRawID
#                 if not user_raw_id and cid in call_user_raw_ids:
#                     user_raw_id = call_user_raw_ids[cid]
#                 # ignore our own TTS loop-back
#                 if user_raw_id and data["audioData"]["participantRawID"] != user_raw_id:
#                     continue
#                 lt.start("stt")
#                 await audio_q.put(b64decode(data["audioData"]["data"]))

#             elif kind == "CallConnected":
#                 pid = data["callConnected"]["participant"]["rawID"]
#                 call_user_raw_ids[cid] = pid
#                 user_raw_id = pid

#             elif kind in ("PlayCompleted", "PlayFailed", "PlayCanceled"):
#                 logger.info("%s from ACS (%s)", kind, cid)

#             # basic hang-up keywords (optional)
#             if kind == "AudioData" and check_for_stopwords(""):
#                 await broadcast_message(ws.app.state.clients, "Goodbye!", "Assistant")
#                 await send_response_to_acs(ws, "Goodbye!", blocking=True)
#                 await asyncio.sleep(1)
#                 await acs.disconnect_call(cid)
#                 break

#     finally:
#         await audio_q.put(None)  # flush / stop AOAI
#         with contextlib.suppress(Exception):
#             await transcribe_task
#         with contextlib.suppress(Exception):
#             await ws.close()
#         call_user_raw_ids.pop(cid, None)
#         cm.persist_to_redis(redis_mgr)
#         try:
#             cm = getattr(ws.state, "cm", None)
#             cosmos = getattr(ws.app.state, "cosmos", None)
#             if cm and cosmos:
#                 build_and_flush(cm, cosmos)
#         except Exception as e:
#             logger.error(f"Error persisting analytics: {e}", exc_info=True)
#         logger.info("◀ media WS closed – %s", cid)
