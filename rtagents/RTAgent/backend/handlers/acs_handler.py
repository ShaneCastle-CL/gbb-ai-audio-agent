"""
handlers/acs_handler.py
=======================
Business logic handlers for Azure Communication Services operations.

This module contains the core business logic extracted from the ACS router,
following the separation of concerns principle. The router handles HTTP/WebSocket
routing while this handler manages the actual ACS business operations.
"""

from __future__ import annotations

import asyncio
import json
from typing import Dict, Optional, Any

from azure.core.exceptions import HttpResponseError
from azure.core.messaging import CloudEvent
from azure.communication.callautomation import TextSource, PhoneNumberIdentifier
from fastapi import HTTPException, WebSocket
from fastapi.websockets import WebSocketDisconnect
from fastapi.responses import JSONResponse
import websocket

from rtagents.RTAgent.backend.orchestration.conversation_state import ConversationManager
from rtagents.RTAgent.backend.services.acs.acs_helpers import play_response
from rtagents.RTAgent.backend.latency.latency_tool import LatencyTool
from rtagents.RTAgent.backend.orchestration.orchestrator import route_turn
from shared_ws import broadcast_message
from utils.ml_logging import get_logger

logger = get_logger("handlers.acs_handler")

from azure.cognitiveservices.speech.audio import AudioStreamFormat, PushAudioInputStream
from base64 import b64decode
from fastapi.websockets import WebSocketState
from rtagents.RTAgent.backend.orchestration.orchestrator import route_turn
from rtagents.RTAgent.backend.helpers import check_for_stopwords
from shared_ws import broadcast_message, send_response_to_acs
from rtagents.RTAgent.backend.services.speech_services import SpeechSynthesizer
class ACSHandler:
    """
    Handles Azure Communication Services business logic operations.
    
    This class encapsulates the core business logic for:
    - Call initiation and management
    - Event processing from ACS callbacks
    - WebSocket transcription handling
    - Media event processing
    """

    @staticmethod
    async def initiate_call(acs_caller, target_number: str, redis_mgr, call_id: str = None) -> Dict[str, Any]:
        """
        Initiate an outbound call through Azure Communication Services.
        
        Args:
            acs_caller: The ACS caller instance
            target_number: The phone number to call
            redis_mgr: Redis manager instance
            call_id: Optional call ID for tracking
            
        Returns:
            Dict containing call initiation result
            
        Raises:
            HTTPException: If call initiation fails
        """
        if not acs_caller:
            raise HTTPException(503, "ACS Caller not initialised")

        try:
            # TODO: Add logic to reject multiple requests for the same target number
            result = await acs_caller.initiate_call(target_number)
            if result.get("status") != "created":
                return {"status": "failed", "message": "Call initiation failed"}
                
            call_id = result["call_id"]

            # Initialize conversation state
            cm = ConversationManager.from_redis(
                session_id=call_id,
                redis_mgr=redis_mgr,
            )
            cm.update_context("target_number", target_number)
            cm.persist_to_redis(redis_mgr)

            logger.info("Call initiated – ID=%s", call_id)
            return {"status": "success", "message": "Call initiated", "callId": call_id}
            
        except (HttpResponseError, RuntimeError) as exc:
            logger.error("ACS error: %s", exc, exc_info=True)
            raise HTTPException(500, str(exc)) from exc

    @staticmethod
    async def handle_inbound_call(request_body: Dict[str, Any], acs_caller) -> JSONResponse:
        """
        Handle inbound call events and subscription validation.
        
        Args:
            request_body: The request body containing events
            acs_caller: The ACS caller instance
            
        Returns:
            JSONResponse with appropriate status
        """
        if not acs_caller:
            raise HTTPException(503, "ACS Caller not initialised")

        try:
            for event in request_body:
                event_type = event.get("eventType")
                if event_type == "Microsoft.EventGrid.SubscriptionValidationEvent":
                    # Handle subscription validation event
                    validation_code = event.get("data", {}).get("validationCode")
                    if validation_code:
                        return JSONResponse(
                            {"validationResponse": validation_code},
                            status_code=200
                        )
                    else:
                        raise HTTPException(400, "Validation code not found in event data")
                else:
                    logger.info(f"Received event of type {event_type}: {event}")

            return JSONResponse({"status": "call answered"}, status_code=200)
            
        except (HttpResponseError, RuntimeError) as exc:
            logger.error("ACS error: %s", exc, exc_info=True)
            raise HTTPException(500, str(exc)) from exc
        except Exception as exc:
            logger.error("Error processing inbound call: %s", exc, exc_info=True)
            raise HTTPException(400, "Invalid request body") from exc

    @staticmethod
    async def process_callback_events(
        events: list, 
        acs_caller, 
        redis_mgr, 
        clients: list
    ) -> Dict[str, str]:
        """
        Process callback events from Azure Communication Services.
        
        Args:
            events: List of ACS events to process
            acs_caller: The ACS caller instance
            redis_mgr: Redis manager instance
            clients: List of connected WebSocket clients
            
        Returns:
            Dict with processing status
        """
        try:
            for raw in events:
                event = CloudEvent.from_dict(raw)
                await ACSHandler._process_single_event(
                    event, acs_caller, redis_mgr, clients
                )
            return {"status": "callback received"}
            
        except Exception as exc:
            logger.error("Callback error: %s", exc, exc_info=True)
            return {"error": str(exc)}

    @staticmethod
    async def _process_single_event(
        event: CloudEvent, 
        acs_caller, 
        redis_mgr, 
        clients: list
    ) -> None:
        """
        Process a single ACS event.
        
        Args:
            event: CloudEvent to process
            acs_caller: The ACS caller instance
            redis_mgr: Redis manager instance
            clients: List of connected WebSocket clients
        """
        etype = event.type
        cid = event.data.get("callConnectionId")
        cm = ConversationManager.from_redis(cid, redis_mgr)

        if etype == "Microsoft.Communication.ParticipantsUpdated":
            await ACSHandler._handle_participants_updated(event, cm, redis_mgr, clients, cid)
        elif etype == "Microsoft.Communication.CallConnected":
            await ACSHandler._handle_call_connected(event, cm, redis_mgr, clients, cid, acs_caller)
        elif etype == "Microsoft.Communication.TranscriptionFailed":
            await ACSHandler._handle_transcription_failed(event, cm, redis_mgr, cid, acs_caller)
        elif etype == "Microsoft.Communication.CallDisconnected":
            await ACSHandler._handle_call_disconnected(event, cm, redis_mgr, cid)
        elif etype in ["Microsoft.Communication.PlayStarted", "Microsoft.Communication.PlayCompleted", 
                       "Microsoft.Communication.PlayFailed", "Microsoft.Communication.PlayCanceled"]:
            await ACSHandler._handle_media_events(event, cm, redis_mgr, etype)

        elif etype == "Microsoft.Communication.MediaStreamingFailed":
            reason = event.data.get("resultInformation", "Unknown reason")
            logger.error("⚠️ MediaStreamingFailed for call %s: %s", cid, reason)
            cm.update_context("bot_speaking", False)
            logger.info("MediaStreamingFailed: Set bot_speaking=False for call %s", cid)
        elif "Failed" in etype:
            reason = event.data.get("resultInformation", "Unknown reason")
            logger.error("⚠️ %s for call %s: %s", etype, cid, reason)
        else:
            logger.info("%s %s", etype, cid)

        cm.persist_to_redis(redis_mgr)

    @staticmethod
    async def _handle_participants_updated(
        event: CloudEvent, 
        cm: ConversationManager, 
        redis_mgr, 
        clients: list, 
        cid: str
    ) -> None:
        """Handle participant updates in the call."""
        participants = event.data.get("participants", [])
        target_number = cm.get_context("target_number")
        target_joined = any(
            p.get("identifier", {}).get("rawId", "").endswith(target_number)
            for p in participants
        )
        cm.update_context("target_participant_joined", target_joined)
        cm.persist_to_redis(redis_mgr)
        
        logger.info(f"Target participant joined: {target_joined} for call {cid}")
        participants_info = [
            p.get("identifier", {}).get("rawId", "unknown") for p in participants
        ]
        await broadcast_message(
            clients,
            f"\tParticipants updated for call {cid}: {participants_info}",
            "System",
        )

    @staticmethod
    async def _handle_call_connected(
        event: CloudEvent, 
        cm: ConversationManager, 
        redis_mgr, 
        clients: list, 
        cid: str, 
        acs_caller,
        stream_mode: str = "media"
    ) -> None:
        """Handle call connected event and play greeting."""
        await broadcast_message(clients, f"Call Connected: {cid}", "System")

        greeting = (
            "Hello, thank you for calling XMYX Insurance Company. "
            "Before I can assist you, let's verify your identity. "
            "How may I address you today? Please state your full name clearly after the tone, "
            "and let me know how I can help you with your insurance needs."
        )
        
        try:
            text_source = TextSource(
                text=greeting,
                source_locale="en-US",
                voice_name="en-US-JennyNeural"
            )
            call_conn = acs_caller.get_call_connection(cid)
            if stream_mode == "transcription":
                call_conn.play_media(play_source=text_source)
                await cm.set_live_context_value(redis_mgr, "greeted", True)
                logger.info(f"Greeting played for call {cid}")
            # if stream_mode == "media":
            #     call_conn.start_media_streaming(
            #         operation_context="startMediaStreamingContext"
            #     )
        except Exception as e:
            logger.error(f"Error playing greeting for call {cid}: {e}", exc_info=True)

    @staticmethod
    async def _handle_transcription_failed(
        event: CloudEvent, 
        cm: ConversationManager, 
        redis_mgr, 
        cid: str, 
        acs_caller
    ) -> None:
        """Handle transcription failure events."""
        reason = event.data.get("resultInformation", "Unknown reason")
        logger.error(f"⚠️ Transcription failed for call {cid}: {reason}")
        
        # Log detailed error information
        if "transcriptionUpdate" in event.data:
            transcription_update = event.data["transcriptionUpdate"]
            logger.info(f"TranscriptionUpdate attributes for call {cid}:")
            for key, value in transcription_update.items():
                logger.info(f"  {key}: {value}")
        
        # Handle specific error codes
        if isinstance(reason, dict):
            error_code = reason.get('code', 'Unknown')
            sub_code = reason.get('subCode', 'Unknown')
            message = reason.get('message', 'No message')
            logger.error(f"   Error details - Code: {error_code}, SubCode: {sub_code}, Message: {message}")
            
            # Check for WebSocket URL issues
            if sub_code == 8581:
                logger.error("🔴 WebSocket connection issue detected!")
                logger.error("   This usually means:")
                logger.error("   1. Your WebSocket endpoint is not accessible from Azure")
                logger.error("   2. Your BASE_URL is incorrect or not publicly accessible")
                logger.error("   3. Your WebSocket server is not running or crashed")

        # Attempt to restart transcription
        try:
            if acs_caller and hasattr(acs_caller, "call_automation_client"):
                call_connection_client = acs_caller.get_call_connection(cid)
                if call_connection_client:
                    call_connection_client.start_transcription()
                    logger.info(f"✅ Attempted to restart transcription for call {cid}")
                else:
                    logger.error(f"❌ Could not get call connection for {cid} to restart transcription")
        except Exception as e:
            logger.error(f"❌ Failed to restart transcription for call {cid}: {e}", exc_info=True)

    @staticmethod
    async def _handle_call_disconnected(
        event: CloudEvent, 
        cm: ConversationManager, 
        redis_mgr, 
        cid: str
    ) -> None:
        """Handle call disconnection events."""
        logger.info(f"❌ Call disconnected for call {cid}")
        
        # Log additional details for debugging
        disconnect_reason = event.data.get("resultInformation", "No resultInformation provided")
        participants = event.data.get("participants", [])
        logger.info(f"Disconnect reason: {disconnect_reason}")
        logger.info(f"Participants at disconnect: {participants}")
        
        # Clean up conversation state
        try:
            cm.persist_to_redis(redis_mgr)
            logger.info(f"Persisted conversation state after disconnect for call {cid}")
        except Exception as e:
            logger.error(f"Failed to persist conversation state after disconnect for call {cid}: {e}")

    @staticmethod
    async def _handle_media_events(
        event: CloudEvent, 
        cm: ConversationManager, 
        redis_mgr, 
        etype: str
    ) -> None:
        """Handle media-related events (play started/completed/failed/canceled)."""
        if etype == "Microsoft.Communication.PlayStarted":
            cm.update_context("bot_speaking", True)
            logger.info(f"PlayStarted: Set bot_speaking=True for call {cm.session_id}")
        elif etype == "Microsoft.Communication.PlayCompleted":
            cm.update_context("bot_speaking", False)
            logger.info(f"PlayCompleted: Set bot_speaking=False for call {cm.session_id}")
        elif etype == "Microsoft.Communication.PlayFailed":
            reason = event.data.get("resultInformation", "Unknown reason")
            logger.error(f"⚠️ PlayFailed for call {cm.session_id}: {reason}")
            cm.update_context("bot_speaking", False)
            logger.info(f"PlayFailed: Set bot_speaking=False for call {cm.session_id}")
        elif etype == "Microsoft.Communication.PlayCanceled":
            cm.update_context("bot_speaking", False)
            logger.info(f"PlayCanceled: Set bot_speaking=False for call {cm.session_id}")

    @staticmethod
    async def process_media_callbacks(
        events: list, 
        cm: ConversationManager, 
        redis_mgr
    ) -> Dict[str, str]:
        """
        Process media callback events.
        
        Args:
            events: List of media events to process
            cm: ConversationManager instance
            redis_mgr: Redis manager instance
            
        Returns:
            Dict with processing status
        """
        try:
            for event in events:
                data = event.get("data", {})
                etype = event.get("type", "")
                logger.info("Media callback received: %s\n\tEventType: %s", data, etype)

                if etype == "Microsoft.Communication.PlayStarted":
                    cm.update_context("bot_speaking", True)
                    logger.info(f"PlayStarted: Set bot_speaking=True for call {cm.session_id}")
                elif etype == "Microsoft.Communication.PlayCompleted":
                    cm.update_context("bot_speaking", False)
                    logger.info(f"PlayCompleted: Set bot_speaking=False for call {cm.session_id}")
                elif etype == "Microsoft.Communication.PlayFailed":
                    reason = data.get("resultInformation", "Unknown reason")
                    logger.error(f"⚠️ PlayFailed for call {cm.session_id}: {reason}")
                    cm.update_context("bot_speaking", False)
                    logger.info(f"PlayFailed: Set bot_speaking=False for call {cm.session_id}")
                elif etype == "Microsoft.Communication.PlayCanceled":
                    cm.update_context("bot_speaking", False)
                    logger.info(f"PlayCanceled: Set bot_speaking=False for call {cm.session_id}")
                elif etype == "Microsoft.Communication.MediaStreamingFailed":
                    reason = data.get("resultInformation", "Unknown reason")
                    logger.error(f"⚠️ MediaStreamingFailed for call {cm.session_id}: {reason}")
                    cm.update_context("bot_speaking", False)
                    logger.info(f"MediaStreamingFailed: Set bot_speaking=False for call {cm.session_id}")
                else:
                    logger.info("Media callback event not handled: %s", etype)
                    
            await cm.persist_to_redis_async(redis_mgr)
            return {"status": "media callback processed"}
            
        except Exception as exc:
            logger.error("Media callback error: %s", exc, exc_info=True)
            return {"error": str(exc)}

    @staticmethod
    async def handle_websocket_transcription(
        ws: WebSocket,
        message: Dict[str, Any],
        cm: ConversationManager,
        redis_mgr,
        call_conn,
        clients: list
    ) -> None:
        """
        Handle WebSocket transcription messages.
        
        Args:
            ws: WebSocket connection
            message: Transcription message from ACS
            cm: ConversationManager instance
            redis_mgr: Redis manager instance
            call_conn: Call connection client
            clients: List of connected WebSocket clients
        """
        try:
            if message.get("kind") != "TranscriptionData":
                return

            bot_speaking = await cm.get_live_context_value(redis_mgr, "bot_speaking")
            td = message["transcriptionData"]
            text = td["text"].strip()
            words = text.split()
            status = td["resultStatus"]  # "Intermediate" or "Final"
            
            logger.info(
                "🎤📝 Transcription received : '%s' (status: %s, bot_speaking: %s)", 
                text, status, bot_speaking
            )

            # Handle interruptions during bot speech
            if status == "Intermediate" and bot_speaking:
                logger.info(
                    "🔊 Intermediate transcription received while bot is speaking, "
                    "cancelling queue and ongoing media: '%s'", text
                )
                call_conn.cancel_all_media_operations()
                await cm.reset_queue_on_interrupt()
                
                interrupt_cnt = cm.context.get("interrupt_count", 0)
                cm.update_context("interrupt_count", interrupt_cnt + 1)
                await cm.persist_to_redis_async(redis_mgr)

            # Handle final transcription
            if status == "Final":
                cm.update_context("interrupt_count", 0)
                await cm.persist_to_redis_async(redis_mgr)

                # Broadcast and route user text
                await broadcast_message(clients, text, "User")
                logger.info("🎤📝 Final transcription received: '%s'", text)
                
                # Route to orchestrator for processing
                await route_turn(cm, text, ws, is_acs=True)

        except Exception as e:
            logger.error(f"Error processing transcription message: {e}", exc_info=True)
            # Continue processing rather than breaking the connection




    @staticmethod
    async def handle_websocket_media_stream(
        ws: WebSocket,
        # acs_caller,
        # redis_mgr,
        # clients: list,
        # cid: str,
        # speech_client=None
    ) -> None:
        """
        Handle WebSocket media streaming for ACS calls.
        
        This method includes the core logic from the original acs_media_ws method but
        with cleaner error handling, better separation of concerns, and improved modularity.
        
        Args:
            ws: WebSocket connection
            acs_caller: ACS caller instance
            redis_mgr: Redis manager instance
            clients: List of connected WebSocket clients
            cid: Call connection ID
            speech_client: Speech-to-text client for audio processing
        """
        try:
            await ws.accept()
            # while True:
            #     msg = await ws.receive_text()
            #     await ws.send_text("ok")



            """Handle ACS WebSocket media streaming."""
            acs_caller = ws.app.state.acs_caller
            redis_mgr = ws.app.state.redis
            speech_client = ws.app.state.stt_client
            clients = ws.app.state.clients

            if not speech_client or not acs_caller:
                await ws.close(code=1011)
                return

            cid = ws.headers["x-ms-call-connection-id"]
            cm = ConversationManager.from_redis(cid, redis_mgr)
            target_phone_number = cm.get_context("target_number")
            ws.app.state.target_participant = PhoneNumberIdentifier(target_phone_number)

            # Global state for tracking call participants
            if not hasattr(ws.app.state, 'call_user_raw_ids'):
                ws.app.state.call_user_raw_ids = {}
            call_user_raw_ids = ws.app.state.call_user_raw_ids
            
            try:
                logger.info("▶ media WS connected - %s", cid)
                    
                    # Initialize conversation manager
                cm = ConversationManager.from_redis(cid, redis_mgr)
                
                # Initialize speech recognition components
                queue: asyncio.Queue[str] = asyncio.Queue()
                push_stream = None
                recognizer = None
                
                if speech_client:
                    try:
                        push_stream = PushAudioInputStream(
                            stream_format=AudioStreamFormat(
                                samples_per_second=16000, 
                                bits_per_sample=16, 
                                channels=1
                            )
                        )
                        recognizer = speech_client.create_realtime_recognizer(
                            push_stream=push_stream,
                            loop=asyncio.get_event_loop(),
                            message_queue=queue,
                            language="en-US",
                            vad_silence_timeout_ms=500,
                        )
                        recognizer.start_continuous_recognition_async()
                        logger.info(f"Speech recognition initialized for call {cid}")
                    except Exception as e:
                        logger.warning(f"Could not initialize speech recognition for call {cid}: {e}")

                # Handle greeting logic
                if not cm.get_context("greeted", False):
                    greeting = (
                        "Hello from XMYX Healthcare Company! Before I can assist you, "
                        "let's verify your identity. How may I address you?"
                    )
                    await broadcast_message(clients, greeting, "Assistant")
                    await send_response_to_acs(ws, greeting, stream_mode="media")
                    cm.append_to_history("wss_media_stream", "assistant", greeting)
                    cm.set_context("greeted", True)

                # Track user participant ID
                user_raw_id = call_user_raw_ids.get(cid)
                
                # Main processing loop
                while True:
                    # Process speech recognition queue
                    spoken_text = await ACSHandler._process_speech_queue(queue)
                    
                    if spoken_text:
                        # # Stop any ongoing TTS
                        # tts_client = getattr(ws.app.state, 'tts_client', None)
                        # if tts_client and hasattr(tts_client, 'stop_speaking'):
                        #     tts_client.stop_speaking()

                        # # Cancel any pending TTS tasks
                        # tts_tasks = getattr(ws.app.state, 'tts_tasks', [])
                        # for task in list(tts_tasks):
                        #     task.cancel()

                        # Broadcast the spoken text
                        await broadcast_message(clients, spoken_text, "User")

                        # Check for stop words
                        if check_for_stopwords(spoken_text):
                            goodbye_msg = "Goodbye!"
                            await broadcast_message(clients, goodbye_msg, "Assistant")
                            await send_response_to_acs(ws, goodbye_msg, blocking=True)
                            await asyncio.sleep(1)
                            if acs_caller:
                                await acs_caller.disconnect_call(cid)
                            break

                        # Route the turn to the orchestrator
                        await route_turn(cm, spoken_text, ws, is_acs=True)

                    # Handle WebSocket messages
                    try:
                        raw = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
                        data = json.loads(raw)
                    except asyncio.TimeoutError:
                        # Check if WebSocket is still connected
                        if ws.client_state != WebSocketState.CONNECTED:
                            break
                        continue
                    except (WebSocketDisconnect, json.JSONDecodeError):
                        logger.info(f"WebSocket disconnected or JSON decode error for call {cid}")
                        break
                    except Exception as e:
                        logger.error(f"Unexpected WebSocket error for call {cid}: {e}", exc_info=True)
                        break

                    # Process different message types
                    await ACSHandler._process_websocket_message(
                        data=data,
                        cid=cid,
                        user_raw_id=user_raw_id,
                        call_user_raw_ids=call_user_raw_ids,
                        push_stream=push_stream
                    )
                    
                    # Update user_raw_id if it was set during message processing
                    if cid in call_user_raw_ids:
                        user_raw_id = call_user_raw_ids[cid]
                        
            except Exception as e:
                logger.error(f"Error in media WebSocket handler for call {cid}: {e}", exc_info=True)
            finally:
                # Clean up resources
                try:
                    if recognizer:
                        recognizer.stop_continuous_recognition_async()
                    if push_stream:
                        push_stream.close()
                    # call_user_raw_ids.pop(cid, None)
                    # cm.persist_to_redis(redis_mgr)
                    logger.info(f"◀ media WS closed – {cid}")
                except Exception as e:
                    logger.error(f"Error during cleanup for call {cid}: {e}")
        except Exception as e:
            logger.error(f"❌ WebSocket error: {e}", exc_info=True)

    @staticmethod
    async def _process_speech_queue(queue: asyncio.Queue) -> Optional[str]:
        """
        Process items from the speech recognition queue.
        
        Args:
            queue: Speech recognition queue
            
        Returns:
            Combined spoken text or None if queue is empty
        """
        spoken_text = None
        try:
            while True:
                item = queue.get_nowait()
                spoken_text = f"{spoken_text} {item}".strip() if spoken_text else item
                queue.task_done()
        except asyncio.QueueEmpty:
            pass
        return spoken_text

    @staticmethod
    async def _process_websocket_message(
        data: Dict[str, Any],
        cid: str,
        user_raw_id: Optional[str],
        call_user_raw_ids: Dict[str, str],
        push_stream=None,
    ) -> Optional[str]:
        """
        Process a WebSocket message from ACS.
        
        Args:
            data: Message data from WebSocket
            cid: Call connection ID
            user_raw_id: Current user's raw participant ID
            call_user_raw_ids: Global mapping of call IDs to user raw IDs
            push_stream: Audio stream for processing
            
        Returns:
            Updated user_raw_id if changed
        """
        from base64 import b64decode
        
        kind = data.get("kind")
        
        if kind == "AudioData":
            # dynamically learn / confirm the caller's participantRawID
            if not user_raw_id and cid in call_user_raw_ids:
                user_raw_id = call_user_raw_ids[cid]

            participant_id = data.get("audioData", {}).get("participantRawID")
            if user_raw_id and participant_id != user_raw_id:
                # Discard bot's own audio
                return user_raw_id

            # Process audio data if push stream is available
            if push_stream:
                try:
                    audio_data = data.get("audioData", {}).get("data", "")
                    if audio_data:
                        push_stream.write(b64decode(audio_data))
                except Exception as e:
                    # Keep going even if decode glitches
                    logger.debug(f"Audio decode error for call {cid}: {e}")

        elif kind == "CallConnected":
            # Extract and store the user's participant ID
            participant_info = data.get("callConnected", {}).get("participant", {})
            participant_id = participant_info.get("rawID")
            if participant_id:
                call_user_raw_ids[cid] = participant_id
                user_raw_id = participant_id
                logger.info(f"Call connected - User participant ID: {participant_id}")
        else:
            logger.debug(f"Unhandled message kind: {kind} for call {cid}")
            
        return user_raw_id

    @staticmethod
    async def _send_response_to_acs(ws: WebSocket, text: str, blocking: bool = False) -> None:
        """
        Send a response to ACS via WebSocket.
        
        Args:
            ws: WebSocket connection
            text: Text to send
            blocking: Whether to wait for completion
        """
        try:
            from shared_ws import send_response_to_acs
            await send_response_to_acs(ws, text, blocking=blocking)
        except Exception as e:
            logger.error(f"Error sending response to ACS: {e}", exc_info=True)

    @staticmethod
    async def handle_speech_recognition_queue(
        queue: asyncio.Queue,
        ws: WebSocket,
        cm: ConversationManager,
        acs_caller,
        cid: str,
        clients: list
    ) -> None:
        """
        Handle speech recognition results from a queue.
        
        Args:
            queue: Queue containing speech recognition results
            ws: WebSocket connection
            cm: ConversationManager instance
            acs_caller: ACS caller instance
            cid: Call connection ID
            clients: List of connected WebSocket clients
        """
        try:
            spoken_text = None
            
            # Collect all available speech recognition results
            try:
                while True:
                    item = queue.get_nowait()
                    spoken_text = f"{spoken_text} {item}".strip() if spoken_text else item
                    queue.task_done()
            except asyncio.QueueEmpty:
                pass

            if spoken_text:
                # Stop any ongoing TTS
                tts_client = getattr(ws.app.state, 'tts_client', None)
                if tts_client and hasattr(tts_client, 'stop_speaking'):
                    tts_client.stop_speaking()

                # Cancel any pending TTS tasks
                tts_tasks = getattr(ws.app.state, 'tts_tasks', [])
                for task in list(tts_tasks):
                    task.cancel()

                # Broadcast the spoken text
                await broadcast_message(clients, spoken_text, "User")

                # Check for stop words
                if ACSHandler._check_for_stopwords(spoken_text):
                    goodbye_msg = "Goodbye!"
                    await broadcast_message(clients, goodbye_msg, "Assistant")
                    await ACSHandler._send_response_to_acs(ws, goodbye_msg)
                    await asyncio.sleep(1)
                    if acs_caller:
                        await acs_caller.disconnect_call(cid)
                    return

                # Route the turn to the orchestrator
                from rtagents.RTAgent.backend.orchestration.orchestrator import route_turn
                await route_turn(cm, spoken_text, ws, is_acs=True)
                
        except Exception as e:
            logger.error(f"Error handling speech recognition for call {cid}: {e}", exc_info=True)

    @staticmethod
    def _check_for_stopwords(text: str) -> bool:
        """
        Check if the text contains stop words indicating end of conversation.
        
        Args:
            text: Text to check
            
        Returns:
            True if stop words are found
        """
        try:
            # Try to import the helper function
            from helpers import check_for_stopwords
            return check_for_stopwords(text)
        except ImportError:
            # Fallback implementation
            stopwords = {"goodbye", "bye", "stop", "end call", "hang up", "disconnect"}
            return any(word in text.lower() for word in stopwords)
