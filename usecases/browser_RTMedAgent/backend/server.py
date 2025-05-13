"""
Real-time voice agent backend.

Exposes:
  • /realtime   – bi-directional WebSocket for STT/LLM/TTS
  • /health     – simple liveness probe
"""
import os
import json
import asyncio
import uuid
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.websockets import WebSocketState
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse 

from openai import AzureOpenAI
from base64 import b64decode, b64encode
from contextlib import asynccontextmanager
import numpy as np
from src.speech.text_to_speech import SpeechSynthesizer
from usecases.browser_RTMedAgent.backend.functions import (
    authenticate_user,
    escalate_emergency,
    evaluate_prior_authorization,
    lookup_medication_info,
    refill_prescription,
    schedule_appointment,
    fill_new_prescription,
    lookup_side_effects,
    get_current_prescriptions,
    check_drug_interactions,
)
from usecases.browser_RTMedAgent.backend.prompt_manager import PromptManager
from usecases.browser_RTMedAgent.backend.tools import available_tools
from utils.ml_logging import get_logger

# --- ACS Integration ---
from usecases.browser_RTMedAgent.backend.acs import AcsCaller # Import AcsCaller
from pydantic import BaseModel # For request body validation
from src.speech.speech_to_text import SpeechCoreTranslator
from azure.cognitiveservices.speech.audio import AudioStreamFormat, PushAudioInputStream
from azure.core.exceptions import HttpResponseError
from typing import Dict

# --- Constants ---
BASE_URL = os.getenv("BASE_URL", "https://<your local devtunnel>.use.devtunnels.ms")
ACS_CONNECTION_STRING = os.getenv("ACS_CONNECTION_STRING")
ACS_SOURCE_PHONE_NUMBER = os.getenv("ACS_SOURCE_PHONE_NUMBER")
ACS_CALLBACK_PATH = "/api/acs/callback" 
ACS_WEBSOCKET_PATH = "/realtime-acs" 
ACS_CALL_PATH = "/api/call"

# ----- websocket relay testintg ----
from fastapi import WebSocket, WebSocketDisconnect
from typing import List

# List to store connected WebSocket clients
connected_clients: List[WebSocket] = []

import asyncio

async def broadcast_message(message: str, sender: str = "system"):
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

# ----------------------------- App & Middleware -----------------------------
# --- Lifespan Management for Startup/Shutdown ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup Logic ---
    logger.info("Application startup...")

    # Initialize SpeechCoreTranslator
    try:
        app.state.stt_client = SpeechCoreTranslator()
        logger.info("SpeechCoreTranslator initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize SpeechCoreTranslator: {e}", exc_info=True)
        app.state.stt_client = None # Store None if failed

    # Initialize AcsCaller
    app.state.acs_caller = initialize_acs_caller_instance() # Call the modified function
    app.state.greeted_call_ids = set() # Initialize greeted call IDs set
    # Initialize potentially unused TTS client (consider removing if confirmed unused)
    try:
        app.state.tts_client = SpeechSynthesizer()
        logger.info("SpeechSynthesizer initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize SpeechSynthesizer: {e}", exc_info=True)
        app.state.tts_client = None

    logger.info("Startup complete.")
    yield # Application runs here
    # --- Shutdown Logic ---
    logger.info("Application shutting down...")
    # if hasattr(app.state, 'acs_caller') and app.state.acs_caller:
    #     try:
    #         await app.state.acs_caller.close() # Ensure close is async in AcsCaller
    #     except Exception as e:
    #         logger.error(f"Error closing AcsCaller: {e}", exc_info=True)
    # # Add other cleanup if needed
    logger.info("Shutdown complete.")

app = FastAPI(lifespan=lifespan) # Apply lifespan manager
allowed_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://localhost:5173",
    "https://127.0.0.1:5173",
    "http://localhost",
    "http://127.0.0.1",
    "https://localhost",
    "https://127.0.0.1",
    # Add any other origins if necessary
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins, # Use the defined list
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Global Clients (Initialized in lifespan) ---

# --- Mappings & Managers ---
STOP_WORDS: List[str] = ["goodbye", "exit", "see you later", "bye"]
TTS_END: List[str] = [".", "!", "?", ";", "。", "！", "？", "；", "\n"]
logger = get_logger()
prompt_manager = PromptManager()
az_openai_client = AzureOpenAI(
    api_version="2025-02-01-preview",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_KEY"),
)
az_speech_synthesizer_client = SpeechSynthesizer()

function_mapping: Dict[str, Callable[..., Any]] = {
    "schedule_appointment": schedule_appointment,
    "refill_prescription": refill_prescription,
    "lookup_medication_info": lookup_medication_info,
    "evaluate_prior_authorization": evaluate_prior_authorization,
    "escalate_emergency": escalate_emergency,
    "authenticate_user": authenticate_user,
    "fill_new_prescription": fill_new_prescription,
    "lookup_side_effects": lookup_side_effects,
    "get_current_prescriptions": get_current_prescriptions,
    "check_drug_interactions": check_drug_interactions,
}

# --- Instantiate SpeechCoreTranslator (STT) ---
# try:
#     stt_client = SpeechCoreTranslator()
# except Exception as e:
#     logger.error(f"Failed to initialize SpeechCoreTranslator: {e}")

# # --- Instantiate SpeechSynthesizer (TTS) ---
# try:
#     tts_client = SpeechSynthesizer()
# except Exception as e:
#     logger.error(f"Failed to initialize SpeechSynthesizer: {e}")
# -----------------------------------------

# --- Helper Functions for Initialization ---
def construct_websocket_url(base_url: str, path: str) -> Optional[str]:
    """Constructs a WebSocket URL from a base URL and path."""
    if not base_url: # Added check for empty base_url
        logger.error("BASE_URL is empty or not provided.")
        return None
    if "<your" in base_url: # Added check for placeholder
        logger.warning("BASE_URL contains placeholder. Please update environment variable.")
        return None

    base_url_clean = base_url.strip('/')
    path_clean = path.strip('/')

    if base_url.startswith("https://"):
        return f"wss://{base_url_clean}/{path_clean}"
    elif base_url.startswith("http://"):
        logger.warning("BASE_URL starts with http://. ACS Media Streaming usually requires wss://.")
        return f"ws://{base_url_clean}/{path_clean}"
    else:
        logger.error(f"Cannot determine WebSocket protocol (wss/ws) from BASE_URL: {base_url}")
        return None


def initialize_acs_caller_instance() -> Optional[AcsCaller]:
    """Initializes and returns the ACS Caller instance if configured, otherwise None."""
    if not all([ACS_CONNECTION_STRING, ACS_SOURCE_PHONE_NUMBER, BASE_URL]):
        logger.warning("ACS environment variables not fully configured. ACS calling disabled.")
        return None

    acs_callback_url = f"{BASE_URL.strip('/')}{ACS_CALLBACK_PATH}"
    acs_websocket_url = construct_websocket_url(BASE_URL, ACS_WEBSOCKET_PATH)

    if not acs_websocket_url:
        logger.error("Could not construct valid ACS WebSocket URL. ACS calling disabled.")
        return None

    logger.info(f"Attempting to initialize AcsCaller...")
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
    if not base_url: # Added check for empty base_url
        logger.error("BASE_URL is empty or not provided.")
        return None
    if "<your" in base_url: # Added check for placeholder
        logger.warning("BASE_URL contains placeholder. Please update environment variable.")
        return None

    base_url_clean = base_url.strip('/')
    path_clean = path.strip('/')

    if base_url.startswith("https://"):
        base_url_clean = base_url.replace("https://", "").strip('/')
        return f"wss://{base_url_clean}/{path_clean}"
    elif base_url.startswith("http://"):
        base_url_clean = base_url.replace("http://", "").strip('/')
        return f"ws://{base_url_clean}/{path_clean}"
    else:
        logger.error(f"Cannot determine WebSocket protocol (wss/ws) from BASE_URL: {base_url}")
        return None


def initialize_acs_caller_instance() -> Optional[AcsCaller]:
    """Initializes and returns the ACS Caller instance if configured, otherwise None."""
    if not all([ACS_CONNECTION_STRING, ACS_SOURCE_PHONE_NUMBER, BASE_URL]):
        logger.warning("ACS environment variables not fully configured. ACS calling disabled.")
        return None

    acs_callback_url = f"{BASE_URL.strip('/')}{ACS_CALLBACK_PATH}"
    acs_websocket_url = construct_websocket_url(BASE_URL, ACS_WEBSOCKET_PATH)

    if not acs_websocket_url:
        logger.error("Could not construct valid ACS WebSocket URL. ACS calling disabled.")
        return None

    logger.info(f"Attempting to initialize AcsCaller...")
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

# --- End ACS Caller Instance ---


class ConversationManager:
    """
    Manages conversation history and context for the voice agent.

    Attributes
    ----------
    pm : PromptManager
        Prompt factory.
    cid : str
        Short conversation ID.
    hist : List[Dict[str, Any]]
        OpenAI chat history.
    """

    def __init__(self, auth: bool = True) -> None:
        self.pm: PromptManager = PromptManager()
        self.cid: str = str(uuid.uuid4())[:8]
        prompt_key: str = (
            "voice_agent_authentication.jinja"
            if auth
            else "voice_agent_system.jinja"
        )
        if auth:
            # TODO: add dynamic prompt once patient metadata is supported
            system_prompt: str = self.pm.get_prompt(prompt_key)
        else:
            system_prompt: str = self.pm.create_prompt_system_main()

        self.hist: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]


def check_for_stopwords(prompt: str) -> bool:
    """Return ``True`` iff the message contains an exit keyword."""
    return any(stop in prompt.lower() for stop in STOP_WORDS)


def check_for_interrupt(prompt: str) -> bool:
    """Return ``True`` iff the message is an interrupt control frame."""
    return "interrupt" in prompt.lower()


async def send_tts_audio(text: str, websocket: WebSocket) -> None:
    """Fire-and-forget TTS synthesis and log enqueue latency."""
    start = time.perf_counter()
    try:
        app.state.tts_client.start_speaking_text(text)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Error synthesizing TTS: {exc}")
    logger.info(f"🗣️ TTS enqueue time: {(time.perf_counter() - start)*1000:.1f} ms")


async def receive_and_filter(websocket: WebSocket) -> Optional[str]:
    """Receive one WS frame; swallow interrupts; return raw payload."""
    start = time.perf_counter()
    raw: str = await websocket.receive_text()
    logger.info(f"📥 WS receive time: {(time.perf_counter() - start)*1000:.1f} ms")
    try:
        msg: Dict[str, Any] = json.loads(raw)
        if msg.get("type") == "interrupt":
            logger.info("🛑 Interrupt received – stopping TTS")
            app.state.tts_client.stop_speaking()
            return None
    except json.JSONDecodeError:
        pass
    return raw

# --------------------------------------------------------------------------- #
#  Helper to send final event
# --------------------------------------------------------------------------- #
async def push_final(websocket: WebSocket, role: str, content: str, is_acs: bool = False) -> None:
    """Emit a single non-streaming message so the UI can close the bubble."""
    if is_acs:
        # For ACS, we need to send the message in a different format
        await send_response_to_acs(websocket, content)
    else:
        await websocket.send_text(json.dumps({"type": role, "content": content}))

def _add_space(text: str) -> str:
    """
    Ensure the chunk ends with a single space or newline.

    This prevents “...assistance.Could” from appearing when we flush on '.'.
    """
    if text and text[-1] not in [" ", "\n"]:
        return text + " "
    return text
# --------------------------------------------------------------------------- #
#  WebSocket entry points
# --------------------------------------------------------------------------- #

# Standalone WebSocket endpoint
@app.websocket("/relay")
async def relay_websocket(websocket: WebSocket):
    if websocket not in connected_clients:
        await websocket.accept()
        connected_clients.append(websocket)
        # logger.info("WebSocket connected to relay.")
    try:
        while True:
            # Keep the connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        connected_clients.remove(websocket)
        logger.info("WebSocket disconnected from relay.")

@app.websocket("/realtime")
async def websocket_endpoint(websocket: WebSocket) -> None:  # noqa: D401
    """Handle authentication flow, then main conversation."""
    await websocket.accept()
    cm = ConversationManager(auth=True)
    caller_ctx = await authentication_conversation(websocket, cm)
    if caller_ctx:
        cm = ConversationManager(auth=False)
        await main_conversation(websocket, cm)

async def authentication_conversation(
    websocket: WebSocket, cm: ConversationManager
) -> Optional[Dict[str, Any]]:
    """Run the authentication sub-dialogue."""
    greeting = (
        "Hello from XMYX Healthcare Company! Before I can assist you, "
        "let’s verify your identity. How may I address you?"
    )
    await websocket.send_text(json.dumps({"type": "status", "message": greeting}))
    await send_tts_audio(greeting, websocket)
    cm.hist.append({"role": "assistant", "content": greeting})

    while True:
        raw = await receive_and_filter(websocket)
        if raw is None:
            continue
        try:
            prompt = json.loads(raw).get("text", raw)
        except json.JSONDecodeError:
            prompt = raw.strip()
        if not prompt:
            continue
        if check_for_stopwords(prompt):
            bye = "Thank you for calling. Goodbye."
            await websocket.send_text(json.dumps({"type": "exit", "message": bye}))
            await send_tts_audio(bye, websocket)
            return None

        auth_start = time.perf_counter()
        result = await process_gpt_response(cm, prompt, websocket)
        logger.info(
            f"[Latency Summary] phase:auth | cid:{cm.cid} | "
            f"total:{(time.perf_counter() - auth_start)*1000:.1f}ms"
        )
        if result and result.get("authenticated"):
            return result



# --- API Endpoint to Initiate Call ---\napp.post(ACS_CALL_PATH)(initiate_acs_phone_call)\napp.post(ACS_CALLBACK_PATH)(handle_acs_callbacks)
class CallRequest(BaseModel):
    target_number: str # Define expected request body
    
@app.post(ACS_CALL_PATH)
async def initiate_acs_phone_call(call_request: CallRequest, request: Request): # Inject request to access app.state
    acs_caller_instance = request.app.state.acs_caller
    if not acs_caller_instance:
        raise HTTPException(status_code=503, detail="ACS Caller not initialized or configured.")
    try:
        # Use the instance from app.state
        result = await acs_caller_instance.initiate_call(call_request.target_number)
        # Check if the call was successfully connected
        if result.get("status") == "created":
            # Notify the frontend about the call connection
            call_connection_id = result.get("call_id")
            if call_connection_id:
                logger.info(f"Call initiated successfully via API. Call ID: {call_connection_id}")
                return JSONResponse(content={"message": "Call initiated", "callId": call_connection_id}, status_code=200)
            else:
                logger.error("Call initiation succeeded but no callConnectionId returned.")
                raise HTTPException(status_code=500, detail="Call initiated but failed to get Call ID.")
            # Log the failure reason if available

        else:
            logger.warning(f"Call initiation failed: {result.get('detail', 'Unknown error')}")
            return JSONResponse(content={"status": "failed"}, status_code=400)


    except HttpResponseError as e:
        logger.error(f"ACS HTTP Error initiating call: {e}", exc_info=True)
        raise HTTPException(status_code=e.status_code or 500, detail=f"ACS Error: {e.message}")
    except RuntimeError as e:
        logger.error(f"Runtime error during call initiation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to initiate call: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to initiate call: {str(e)}")


from azure.core.messaging import CloudEvent

# --- ACS Callback Handler ---
@app.post(ACS_CALLBACK_PATH)
async def handle_acs_callbacks(request: Request):

    acs_caller_instance = request.app.state.acs_caller
    if not acs_caller_instance:
        logger.error("ACS Caller not initialized, cannot handle callback.")
        return JSONResponse(status_code=503, content={"error": "ACS Caller not initialized"})
    try:
        cloudevent = await request.json() 

        for event_dict in cloudevent:
            try:
                event = CloudEvent.from_dict(event_dict)
                if event.data is None or 'callConnectionId' not in event.data:
                    logger.warning(f"Received event without data or callConnectionId: {event_dict}")
                    continue

                call_connection_id = event.data['callConnectionId']

                logger.info(f"Processing event type: {event.type} for call connection id: {call_connection_id}")
                # Updated event handling logic with broadcasting and emojis
                if event.type == "Microsoft.Communication.CallConnected":
                    logger.info(f"📞 Call connected event received for call connection id: {call_connection_id}")
                    await broadcast_message("📞 Call connected")
                elif event.type == "Microsoft.Communication.ParticipantsUpdated":
                    logger.info(f"👥 Participants updated event received for call connection id: {call_connection_id}")
                    await broadcast_message("👥 Participants updated")
                elif event.type == "Microsoft.Communication.CallDisconnected":
                    logger.info(f"❌ Call disconnect event received for call connection id: {call_connection_id}")
                    await broadcast_message("❌ Call disconnected")
                elif event.type == "Microsoft.Communication.MediaStreamingStarted":
                    logger.info(f"🎙️ Media streaming started for call connection id: {call_connection_id}")
                    await broadcast_message("🎙️ Media streaming started")
                elif event.type == "Microsoft.Communication.MediaStreamingStopped":
                    logger.info(f"🛑 Media streaming stopped for call connection id: {call_connection_id}")
                    await broadcast_message("🛑 Media streaming stopped")
                elif event.type == "Microsoft.Communication.MediaStreamingFailed":
                    logger.error(f"⚠️ Media streaming failed for call connection id: {call_connection_id}. Details: {event.data}")
                    await broadcast_message("⚠️ Media streaming failed")
                else:
                    logger.info(f"ℹ️ Unhandled event type: {event.type} for call connection id: {call_connection_id}")
                    await broadcast_message(f"ℹ️ Unhandled event type: {event.type}")

            except Exception as e:
                logger.error(f"Error processing event: {event_dict}. Error: {e}", exc_info=True)
            # Decide if you want to continue processing other events or stop

        # Notify the frontend about the callback event
        return JSONResponse(content={"status": "callback received"}, status_code=200)
    except Exception as e:
        logger.error(f"Error processing ACS callback event: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": f"Failed to process callback: {str(e)}"})


# Map from callConnectionId → human caller’s raw ACS identifier
call_user_raw_ids: Dict[str, str] = {}
# Audio metadata storage for persisting configurations

@app.websocket(ACS_WEBSOCKET_PATH)
async def acs_websocket_endpoint(websocket: WebSocket):
    """Handles the bidirectional audio stream for an ACS call."""
    # Access initialized instances from app state
    speech_core_instance = app.state.stt_client
    acs_caller_instance = app.state.acs_caller

    if not speech_core_instance:
        logger.error("SpeechCoreTranslator not available. Cannot process ACS audio.")
        # Close connection gracefully if possible (though accept wasn't called yet)
        # await websocket.close(code=1011) # Cannot close before accept
        return
    if not acs_caller_instance:
         logger.error("ACS Caller not available. Cannot process ACS audio.")
         # await websocket.close(code=1011)
         return

    await websocket.accept()
    call_connection_id = websocket.headers.get("x-ms-call-connection-id", "UnknownCall")
    logger.info(f"▶ ACS media WebSocket accepted for call {call_connection_id}")

    loop = asyncio.get_event_loop()
    message_queue = asyncio.Queue()
    cm = ConversationManager(auth=False) # ACS calls usually start unauthenticated
    cm.cid = call_connection_id
    user_identifier = call_user_raw_ids.get(call_connection_id) # Get initial mapping if available

    try:
        # --- Setup Audio Stream and Recognizer ---
        fmt = AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1) # Corrected sample rate
        push_stream = PushAudioInputStream(stream_format=fmt)
        # Use the instance from app.state
        recognizer = speech_core_instance.create_realtime_recognizer(
            push_stream=push_stream,
            loop=loop,
            message_queue=message_queue,
            language="en-US"
        )
        recognizer.start_continuous_recognition_async()
        logger.info(f"🎙️ Continuous recognition started for call {call_connection_id}")

        # --- Play greeting only if not already played for this call ---
        # Note: Assumes 'greeted_call_ids' set is initialized in app.state during startup
        # and cleaned up (e.g., on CallDisconnected event).
        greeted_call_ids = app.state.greeted_call_ids

        if call_connection_id != "UnknownCall" and call_connection_id not in greeted_call_ids:
            initial_greeting = "Hello from XMYX Healthcare Company! Before I can assist you, let’s verify your identity. How may I address you?"
            logger.info(f"Playing initial greeting for call {call_connection_id}")
            # Don't await here, let it play while listening starts
            # Use the instance from app.state
            await broadcast_message(initial_greeting, "Assistant")
            await send_response_to_acs(websocket, initial_greeting)

            cm.hist.append({"role": "assistant", "content": initial_greeting})
            greeted_call_ids.add(call_connection_id) # Mark as greeted
        else:
             logger.info(f"Skipping initial greeting for already greeted call {call_connection_id}")


        # --- Main Loop ---
        while True:
            # --- Check for recognized speech ---
            try:
                try:
                    recognized_text = message_queue.get_nowait()
                except asyncio.QueueEmpty:
                    recognized_text = None
                if recognized_text:
                    logger.info(f"Processing recognized text for call {call_connection_id}: {recognized_text}")
                    await broadcast_message(recognized_text, "User")

                    if check_for_stopwords(recognized_text):
                        logger.info(f"Stop word detected in call {call_connection_id}. Ending conversation.")
                        # Optionally play a goodbye message
                        await broadcast_message("Goodbye!", "Assistant")

                        await send_response_to_acs(websocket, "Goodbye!")
                        await asyncio.sleep(1) # Allow time for TTS to potentially start

                        await acs_caller_instance.disconnect_call(call_connection_id) # Disconnect the call
                        break # Exit the main loop

                    await process_gpt_response(cm, recognized_text, websocket, is_acs=True)
                    message_queue.task_done()
            except asyncio.TimeoutError:
                pass
            except Exception as q_err: logger.error(f"Error getting from message queue for call {call_connection_id}: {q_err}", exc_info=True)


            # --- Receive and process incoming WebSocket data from ACS ---
            try:
                raw_data = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
                data = json.loads(raw_data)
            except asyncio.TimeoutError:
                # No data received from ACS for a while, check if connection is still alive
                if websocket.client_state != WebSocketState.CONNECTED:
                     logger.warning(f"ACS WebSocket {call_connection_id} disconnected while waiting for data.")
                     break
                continue # Continue loop if connected but no data
            except WebSocketDisconnect:
                 logger.info(f"ACS WebSocket disconnected for call {call_connection_id}")
                 break
            except json.JSONDecodeError:
                 logger.warning(f"Received invalid JSON from ACS for call {call_connection_id}")
                 continue
            except Exception as e:
                 logger.error(f"Error receiving from ACS WebSocket {call_connection_id}: {e}", exc_info=True)
                 break # Exit loop on unexpected error

            # --- Handle Different Message Kinds ---
            kind = data.get("kind")
            if kind == "AudioData":
                raw_id = data.get("audioData", {}).get("participantRawID")
                if not user_identifier and call_connection_id in call_user_raw_ids: user_identifier = call_user_raw_ids[call_connection_id]
                if user_identifier and raw_id != user_identifier: continue

                try:
                    b64 = data.get("audioData", {}).get("data")
                    if b64: push_stream.write(b64decode(b64))
                except Exception as e: logger.error(f"Error processing audio data chunk for call {call_connection_id}: {e}", exc_info=True)

            elif kind == "CallConnected":
                connected_participant_id = data.get("callConnected", {}).get("participant", {}).get("rawID")
                if connected_participant_id and call_connection_id not in call_user_raw_ids:
                    call_user_raw_ids[call_connection_id] = connected_participant_id
                    user_identifier = connected_participant_id

            elif kind == "PlayCompleted" or kind == "PlayFailed" or kind == "PlayCanceled":
                 logger.info(f"Received {kind} event via WebSocket for call {call_connection_id}")
                 # Handle media playback status if needed

    except WebSocketDisconnect:
        logger.info(f"ACS WebSocket {call_connection_id} disconnected.")
    except Exception as e:
        logger.error(f"Unhandled error in ACS WebSocket handler for call {call_connection_id}: {e}", exc_info=True)
    finally:
        logger.info(f"🧹 Cleaning up ACS WebSocket handler for call {call_connection_id}.")
        if recognizer:
            try:
                # Use wait_for to prevent hanging if stop takes too long
                await asyncio.wait_for(asyncio.to_thread(recognizer.stop_continuous_recognition_async), timeout=5.0)
                logger.info(f"🎙️ Continuous recognition stopped for call {call_connection_id}")
            except asyncio.TimeoutError:
                 logger.warning(f"Timeout stopping recognizer for call {call_connection_id}")
            except Exception as e:
                 logger.error(f"Error stopping recognizer for call {call_connection_id}: {e}", exc_info=True)
        if push_stream:
            push_stream.close()
            logger.info(f"Audio push stream closed for call {call_connection_id}")
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close()
            logger.info(f"ACS WebSocket connection closed for call {call_connection_id}")
        # Remove the call ID mapping on disconnect
        if call_connection_id in call_user_raw_ids:
            try: # Protect against potential KeyError if deleted elsewhere
                 del call_user_raw_ids[call_connection_id]
                 logger.info(f"Removed call ID mapping for {call_connection_id}")
            except KeyError:
                 logger.warning(f"Call ID mapping for {call_connection_id} already removed.")

# ----------------------------- Main Flow (Browser) -----------------------------
async def main_conversation(websocket: WebSocket, cm: ConversationManager) -> None:
    """Main multi-turn loop after authentication."""
    while True:
        raw = await receive_and_filter(websocket)
        if raw is None:
            continue
        try:
            prompt = json.loads(raw).get("text", raw)
        except json.JSONDecodeError:
            prompt = raw.strip()
        if not prompt:
            continue
        if check_for_stopwords(prompt):
            goodbye = "Thank you for using our service. Goodbye."
            await websocket.send_text(json.dumps({"type": "exit", "message": goodbye}))
            await send_tts_audio(goodbye, websocket)
            return

        total_start = time.perf_counter()
        await process_gpt_response(cm, prompt, websocket)
        logger.info(
            f"📊 phase:main | cid:{cm.cid} | "
            f"total:{(time.perf_counter() - total_start)*1000:.1f}ms"
        )

async def send_response_to_acs(websocket: WebSocket, response: str) -> None:
    """Send a response to the ACS WebSocket."""
    pcm = app.state.tts_client.synthesize_to_base64_frames(
        text=response, sample_rate=16000
    )
    await send_pcm_frames(websocket, pcm_bytes=pcm, sample_rate=16000)

async def send_pcm_frames(ws: WebSocket, pcm_bytes: bytes, sample_rate: int):
    packet_size = 640 if sample_rate == 16000 else 960
    for i in range(0, len(pcm_bytes), packet_size):
        frame = pcm_bytes[i : i + packet_size]
        # pad last frame
        if len(frame) < packet_size:
            frame += b"\x00" * (packet_size - len(frame))
        b64 = b64encode(frame).decode("ascii")

        payload = {
          "kind": "AudioData",
          "audioData": {"data": b64},
          "stopAudio": None
        }
        await ws.send_text(json.dumps(payload))

        # **This 20 ms delay makes it “real-time” instead of instant-playback**
        # await asyncio.sleep(0.02)

async def send_data(websocket, buffer):
    if websocket.client_state == WebSocketState.CONNECTED:
        data = {
            "Kind": "AudioData",
            "AudioData": {
                "data": buffer
            },
            "StopAudio": None
        }
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
        stop_payload = {
            "Kind": "StopAudio",
            "AudioData": None,
            "StopAudio": {}
        }
        await websocket.send_json(stop_payload)
        logger.info("🛑 Sent StopAudio command to ACS WebSocket.")

async def resume_audio(websocket):
    """
    Tells the ACS Media Streaming service to resume accepting incoming audio from client.
    (This resumes the stream without needing to reconnect.)
    """
    if websocket.client_state.name == "CONNECTED":
        start_payload = {
            "Kind": "StartAudio",
            "AudioData": None,
            "StartAudio": {}
        }
        await websocket.send_json(start_payload)
        logger.info("🎙️ Sent StartAudio command to ACS WebSocket.")
# ----------------------------- GPT Processing -----------------------------
async def process_gpt_response(
    cm: ConversationManager,
    user_prompt: str,
    websocket: WebSocket,
    is_acs: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Stream GPT response, TTS chunks, handle tools.

    Returns
    -------
    dict | None
        Tool output (only for ``authenticate_user``) or ``None``.
    """
    cm.hist.append({"role": "user", "content": user_prompt})
    logger.info(f"🎙️ Processing prompt: {user_prompt}")

    try:
        stream_start = time.perf_counter()
        response = az_openai_client.chat.completions.create(
            stream=True,
            messages=cm.hist,
            tools=available_tools,
            tool_choice="auto",
            max_tokens=4096,
            temperature=0.5,
            top_p=1.0,
            model=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_ID", ""),
        )

        collected: List[str] = []
        final_collected: List[str] = []
        prev_ts = stream_start
        tool_started = False
        tool_name = tool_id = args = ""

        for chunk in response:
            now = time.perf_counter()
            logger.info(f"🔸 Chunk arrived after: {(now - prev_ts)*1000:.1f} ms")
            prev_ts = now

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if delta.tool_calls:
                tc = delta.tool_calls[0]
                tool_id = tc.id or tool_id
                tool_name = tc.function.name or tool_name
                args += tc.function.arguments or ""
                tool_started = True
                continue

            if delta.content:
                collected.append(delta.content)
                if delta.content in TTS_END:
                    text_streaming = _add_space("".join(collected).strip())
                    if is_acs:
                        # Send TTS audio to ACS WebSocket
                        await broadcast_message(text_streaming, "Assistant")
                        send_response_to_acs(websocket, text_streaming)
                    else:
                        await send_tts_audio(text_streaming, websocket)
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "assistant_streaming",
                                    "content": text_streaming,
                                }
                            )
                        )
                    final_collected.append(text_streaming)
                    collected.clear()

        # ── flush any residual text ───────────────────────────────────────────
        if collected:
            pending = "".join(collected).strip()
            if is_acs:
                # Send TTS audio to ACS WebSocket
                await broadcast_message(text_streaming, "Assistant")
                send_response_to_acs(websocket, pending)
            else:
                await send_tts_audio(pending, websocket)
                await websocket.send_text(
                    json.dumps({"type": "assistant_streaming", "content": pending})
                )
            final_collected.append(pending)

        logger.info(
            f"💬 GPT full stream time: "
            f"{(time.perf_counter() - stream_start)*1000:.1f} ms"
        )
        text = "".join(final_collected).strip()
        if text:
            cm.hist.append({"role": "assistant", "content": text})
            await push_final(websocket, "assistant", text, is_acs)
            logger.info(f"🧠 Assistant responded: {text}")

        if tool_started:
            cm.hist.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tool_id,
                            "type": "function",
                            "function": {"name": tool_name, "arguments": args},
                        }
                    ],
                }
            )
            return await handle_tool_call(tool_name, tool_id, args, cm, websocket)

    except asyncio.CancelledError:
        logger.info(
            f"🔚 process_gpt_response cancelled for input: '{user_prompt[:40]}'"
        )
        raise

    return None


# --------------------------------------------------------------------------- #
#  Tool life-cycle helpers
# --------------------------------------------------------------------------- #
async def push_tool_start(
    ws: WebSocket,
    call_id: str,
    name: str,
    args: dict,
) -> None:
    """Notify UI that a tool just kicked off."""
    await ws.send_text(json.dumps({
        "type": "tool_start",
        "callId": call_id,
        "tool": name,
        "args": args,          # keep it PHI-free
        "ts": time.time(),
    }))


async def push_tool_progress(
    ws: WebSocket,
    call_id: str,
    pct: int,
    note: str | None = None,
) -> None:
    """Optional: stream granular progress for long-running tools."""
    await ws.send_text(json.dumps({
        "type": "tool_progress",
        "callId": call_id,
        "pct": pct,     # 0-100
        "note": note,
        "ts": time.time(),
    }))


async def push_tool_end(
    ws: WebSocket,
    call_id: str,
    name: str,
    status: str,
    elapsed_ms: float,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    """Finalise the life-cycle (status = success|error)."""
    await ws.send_text(json.dumps({
        "type": "tool_end",
        "callId": call_id,
        "tool": name,
        "status": status,
        "elapsedMs": round(elapsed_ms, 1),
        "result": result,
        "error": error,
        "ts": time.time(),
    }))


async def handle_tool_call(          # unchanged signature
    tool_name: str,
    tool_id: str,
    function_call_arguments: str,
    cm: ConversationManager,
    websocket: WebSocket,
    is_acs: bool = False,
) -> Any:
    """
    Execute the mapped function tool, stream life-cycle events, preserve
    legacy timing logs, and follow up with GPT.
    """
    call_id = str(uuid.uuid4())[:8]                          # for UI tracking

    try:
        # -------- arguments & lookup -------------------------------------------------
        params = json.loads(function_call_arguments.strip() or "{}")
        fn = function_mapping.get(tool_name)
        if fn is None:
            raise ValueError(f"Unknown tool '{tool_name}'")

        # -------- notify UI that we’re starting --------------------------------------
        await push_tool_start(websocket, call_id, tool_name, params)

        # -------- run the tool (your original timing log preserved) -----------------
        t0 = time.perf_counter()
        result_json = await fn(params)                  # async/await OK
        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000

        logger.info(f"⚙️ Tool '{tool_name}' exec time: {elapsed_ms:.1f} ms")

        result = (
            json.loads(result_json) if isinstance(result_json, str) else result_json
        )

        # -------- record in chat history --------------------------------------------
        cm.hist.append(
            {
                "tool_call_id": tool_id,
                "role": "tool",
                "name": tool_name,
                "content": json.dumps(result),
            }
        )

        # -------- notify UI that we’re done ------------------------------------------
        await push_tool_end(
            websocket,
            call_id,
            tool_name,
            "success",
            elapsed_ms,
            result=result,
        )

        # -------- ask GPT to follow up with the result ------------------------------
        await process_tool_followup(cm, websocket, is_acs)
        return result

    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - t0) * 1000 if "t0" in locals() else 0.0
        logger.error(f"Tool '{tool_name}' error: {exc}")

        # tell the UI the tool failed
        await push_tool_end(
            websocket,
            call_id,
            tool_name,
            "error",
            elapsed_ms,
            error=str(exc),
        )
        return {}

async def process_tool_followup(
    cm: ConversationManager, websocket: WebSocket, is_acs: bool
) -> None:
    """Stream follow-up after tool execution."""
    collected: List[str] = []
    final_collected: List[str] = []

    try:
        response = az_openai_client.chat.completions.create(
            stream=True,
            messages=cm.hist,
            temperature=0.5,
            top_p=1.0,
            max_tokens=4096,
            model=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_ID"),
        )

        for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                collected.append(delta.content)
                if delta.content in TTS_END:
                    text_streaming = _add_space("".join(collected).strip())
                    await send_tts_audio(text_streaming, websocket)
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "assistant_streaming",
                                "content": text_streaming,
                            }
                        )
                    )
                    final_collected.append(text_streaming)
                    collected.clear()

        # ── flush tail chunk ─────────────────────────────────────────────────
        if collected:
            pending = "".join(collected).strip()
            if is_acs:
                # Send TTS audio to ACS WebSocket
                await broadcast_message(pending, "Assistant")

                send_response_to_acs(websocket, pending)
            else:
                await send_tts_audio(pending, websocket)
                await websocket.send_text(
                    json.dumps({"type": "assistant_streaming", "content": pending})
                )
            final_collected.append(pending)

        final_text = "".join(final_collected).strip()
        if final_text:
            cm.hist.append({"role": "assistant", "content": final_text})
            if is_acs:
                try:
                    # Use the instance from app.state
                    pcm = app.state.tts_client.synthesize_to_base64_frames(text=final_text, sample_rate=16000)
                    await send_pcm_frames(websocket, pcm, sample_rate=16000) # Send PCM frames to ACS WebSocket
                except Exception as e:
                    logger.error(f"Failed to play final TTS via ACS for call {cm.cid}: {e}", exc_info=True)
            else:
                await push_final(websocket, "assistant", final_text)
                logger.info(f"🧠 Assistant said: {final_text}")

    except asyncio.CancelledError:
        logger.info("🔚 process_tool_followup cancelled")
        raise


# --------------------------------------------------------------------------- #
#  Health probe
# --------------------------------------------------------------------------- #
@app.get("/health")
async def read_health() -> Dict[str, str]:
    """Kubernetes-friendly liveness endpoint."""
    return {"message": "Server is running!"}


# --------------------------------------------------------------------------- #
#  local dev entry-point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8010)
