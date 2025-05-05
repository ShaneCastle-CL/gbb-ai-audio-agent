import os
import json
import asyncio
import uuid
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.websockets import WebSocketState
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse 

from openai import AzureOpenAI
from base64 import b64decode, b64encode
from contextlib import asynccontextmanager
import numpy as np
from src.speech.text_to_speech import SpeechSynthesizer
from usecases.browser_RTMedAgent.backend.tools import available_tools
from usecases.browser_RTMedAgent.backend.functions import (
    schedule_appointment,
    refill_prescription,
    lookup_medication_info,
    evaluate_prior_authorization,
    escalate_emergency,
    authenticate_user,
)
from usecases.browser_RTMedAgent.backend.prompt_manager import PromptManager
from utils.ml_logging import get_logger

# --- ACS Integration ---
from usecases.browser_RTMedAgent.backend.acs import AcsCaller # Import AcsCaller
from pydantic import BaseModel # For request body validation
from src.speech.speech_to_text import SpeechCoreTranslator
from azure.cognitiveservices.speech.audio import AudioStreamFormat, PushAudioInputStream
from azure.communication.callautomation import TextSource, SsmlSource
from azure.core.exceptions import HttpResponseError
from typing import Dict

# --- Constants ---
BASE_URL = os.getenv("BASE_URL", "https://<your local devtunnel>.use.devtunnels.ms")
ACS_CONNECTION_STRING = os.getenv("ACS_CONNECTION_STRING")
ACS_SOURCE_PHONE_NUMBER = os.getenv("ACS_SOURCE_PHONE_NUMBER")
ACS_CALLBACK_PATH = "/api/acs/callback" 
ACS_WEBSOCKET_PATH = "/realtime-acs" 
ACS_CALL_PATH = "/api/call"

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
    if hasattr(app.state, 'acs_caller') and app.state.acs_caller:
        try:
            await app.state.acs_caller.close() # Ensure close is async in AcsCaller
        except Exception as e:
            logger.error(f"Error closing AcsCaller: {e}", exc_info=True)
    # Add other cleanup if needed
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

az_openai_client = AzureOpenAI(
    api_version="2025-02-01-preview",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_KEY"),
)

# --- Mappings & Managers ---
STOP_WORDS = ["goodbye", "exit", "see you later", "bye"]
logger = get_logger()
prompt_manager = PromptManager()
function_mapping = {
    "schedule_appointment": schedule_appointment,
    "refill_prescription": refill_prescription,
    "lookup_medication_info": lookup_medication_info,
    "evaluate_prior_authorization": evaluate_prior_authorization,
    "escalate_emergency": escalate_emergency,
    "authenticate_user": authenticate_user,
}

# --- Instantiate SpeechCoreTranslator (STT) ---
try:
    stt_client = SpeechCoreTranslator()
except Exception as e:
    logger.error(f"Failed to initialize SpeechCoreTranslator: {e}")

# --- Instantiate SpeechSynthesizer (TTS) ---
try:
    tts_client = SpeechSynthesizer()
except Exception as e:
    logger.error(f"Failed to initialize SpeechSynthesizer: {e}")
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

# --- End ACS Caller Instance ---

# ----------------------------- Conversation Manager -----------------------------
class ConversationManager:
    def __init__(self, auth: bool = True):
        self.pm = PromptManager()
        self.cid = str(uuid.uuid4())[:8]
        prompt = self.pm.get_prompt("voice_agent_authentication.jinja" if auth else "voice_agent_system.jinja")
        self.hist = [{"role": "system", "content": prompt}]

# ----------------------------- Utils -----------------------------
def check_for_stopwords(prompt: str) -> bool:
    return any(stop_word in prompt.lower() for stop_word in STOP_WORDS)

def check_for_interrupt(prompt: str) -> bool:
    return any(interrupt in prompt.lower() for interrupt in ["interrupt"])

async def send_tts_audio(text: str, websocket: WebSocket):
    try:
        tts_client.start_speaking_text(text)
    except Exception as e:
        logger.error(f"Error synthesizing TTS: {e}")

async def receive_and_filter(websocket: WebSocket) -> Optional[str]:
    """
    Receive one WebSocket frame, stop TTS & return None if it's an interrupt.
    Otherwise return raw text.
    """
    raw = await websocket.receive_text()
    try:
        msg = json.loads(raw)
        if msg.get("type") == "interrupt":
            logger.info("🛑 Interrupt received, stopping TTS")
            tts_client.stop_speaking()
            return None
    except json.JSONDecodeError:
        pass
    return raw

# ----------------------------- WebSocket Flow -----------------------------
@app.websocket("/realtime")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    cm = ConversationManager(auth=True)
    caller_ctx = await authentication_conversation(websocket, cm)
    if caller_ctx:
        cm = ConversationManager(auth=False)
        await main_conversation(websocket, cm)

# ----------------------------- Auth Flow -----------------------------
async def authentication_conversation(websocket: WebSocket, cm: ConversationManager) -> Optional[Dict[str, Any]]:
    greeting = "Hello from XMYX Healthcare Company! Before I can assist you, let’s verify your identity. How may I address you?"
    await websocket.send_text(json.dumps({"type": "status", "message": greeting}))
    await send_tts_audio(greeting, websocket)
    cm.hist.append({"role": "assistant", "content": greeting})

    while True:
        try:
            # <-- receive one frame raw
            prompt_raw = await websocket.receive_text()
        except WebSocketDisconnect:
            return

        # <-- interrupt filter
        try:
            msg = json.loads(prompt_raw)
            if msg.get("type") == "interrupt":
                logger.info("🛑 Interrupt received; stopping TTS and skipping GPT")
                tts_client.stop_speaking()
                continue
        except json.JSONDecodeError:
            pass

        # <-- now parse true user text
        try:
            prompt = json.loads(prompt_raw).get("text", prompt_raw)
        except json.JSONDecodeError:
            prompt = prompt_raw.strip()

        if not prompt:
            continue
        if check_for_stopwords(prompt):
            bye = "Thank you for calling. Goodbye."
            await websocket.send_text(json.dumps({"type": "exit", "message": bye}))
            await send_tts_audio(bye, websocket)
            return None

        result = await process_gpt_response(cm, prompt, websocket)
        if result and result.get("authenticated"):
            return result


# --- API Endpoint to Initiate Call ---
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
            call_connection_id = result.get("call_connection_id")
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

                # Existing event handling logic (logging)
                if event.type == "Microsoft.Communication.CallConnected":
                    logger.info(f"Call connected event received for call connection id: {call_connection_id}")
                    # asyncio.create_task(manager.broadcast({
                    #     "channel":"acs",
                    #     "type":"logs",
                    #     "text": "Call connected",
                    # }))
                elif event.type == "Microsoft.Communication.ParticipantsUpdated":
                    logger.info(f"Participants updated event received for call connection id: {call_connection_id}")
                    # asyncio.create_task(manager.broadcast({
                    #     "channel":"acs",
                    #     "type":"logs",
                    #     "text": "Participants Updated",
                    # }))
                elif event.type == "Microsoft.Communication.CallDisconnected":
                    logger.info(f"Call disconnect event received for call connection id: {call_connection_id}")
                    # asyncio.create_task(manager.broadcast({
                    #     "channel":"acs",
                    #     "type":"logs",
                    #     "text": "Call Disconnected",
                    # }))
                    # await acs_caller.disconnect_call(call_connection_id)
    
                else:
                    logger.info(f"Unhandled event type: {event.type} for call connection id: {call_connection_id}")

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
    speech_core_instance = websocket.app.state.speech_core
    acs_caller_instance = websocket.app.state.acs_caller

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
    recognizer = None
    push_stream = None
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
        greeted_call_ids = websocket.app.state.greeted_call_ids

        if call_connection_id != "UnknownCall" and call_connection_id not in greeted_call_ids:
            initial_greeting = "Hello, thank you for calling. How can I help you today?"
            logger.info(f"Playing initial greeting for call {call_connection_id}")
            # Don't await here, let it play while listening starts
            # Use the instance from app.state
            asyncio.create_task(acs_caller_instance.play_response(call_connection_id, initial_greeting))
            cm.hist.append({"role": "assistant", "content": initial_greeting})
            greeted_call_ids.add(call_connection_id) # Mark as greeted
        else:
             logger.info(f"Skipping initial greeting for already greeted call {call_connection_id}")


        # --- Main Loop ---
        while True:
            # --- Check for recognized speech ---
            try:
                recognized_text = await asyncio.wait_for(message_queue.get(), timeout=0.1)
                if recognized_text:
                    logger.info(f"STT Final Result for call {call_connection_id}: {recognized_text}")
                    # await manager.broadcast({"type": "finalTranscript", "text": recognized_text, "callId": call_connection_id})
                    if check_for_stopwords(recognized_text): break
                    # Use instance for GPT processing
                    await process_gpt_response(cm, recognized_text, websocket, is_acs=True, call_id=call_connection_id, acs_caller_override=acs_caller_instance)
                message_queue.task_done()
            except asyncio.TimeoutError: pass
            except Exception as q_err: logger.error(f"Error getting from message queue for call {call_connection_id}: {q_err}", exc_info=True)


            # --- Receive and process incoming WebSocket data from ACS ---
            try:
                raw_data = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
                data = json.loads(raw_data)
            except asyncio.TimeoutError:
                if websocket.client_state != WebSocketState.CONNECTED: break
                continue
            except WebSocketDisconnect: break
            except json.JSONDecodeError: continue
            except Exception as e:
                logger.error(f"Error receiving from ACS WebSocket {call_connection_id}: {e}", exc_info=True)
                break


            # --- Handle Different Message Kinds ---
            kind = data.get("kind")
            if kind == "AudioData":
                raw_id = data.get("audioData", {}).get("participantRawID")
                if not user_identifier and call_connection_id in call_user_raw_ids: user_identifier = call_user_raw_ids[call_connection_id]
                if user_identifier and raw_id != user_identifier: continue

                try:
                    # Use instance for barge-in
                    asyncio.create_task(acs_caller_instance.play_response(call_connection_id, ""))
                    b64 = data.get("audioData", {}).get("data")
                    if b64: push_stream.write(b64decode(b64))
                except Exception as e: logger.error(f"Error processing audio data chunk for call {call_connection_id}: {e}", exc_info=True)

            elif kind == "CallConnected":
                connected_participant_id = data.get("callConnected", {}).get("participant", {}).get("rawID")
                if connected_participant_id and call_connection_id not in call_user_raw_ids:
                    call_user_raw_ids[call_connection_id] = connected_participant_id
                    user_identifier = connected_participant_id


    except WebSocketDisconnect:
        logger.info(f"ACS WebSocket {call_connection_id} disconnected.")
    except Exception as e:
        logger.error(f"Unhandled error in ACS WebSocket handler for call {call_connection_id}: {e}", exc_info=True)
    finally:
        logger.info(f"🧹 Cleaning up ACS WebSocket handler for call {call_connection_id}.")
        if recognizer:
            try: await asyncio.wait_for(recognizer.stop_continuous_recognition_async(), timeout=5.0)
            except asyncio.TimeoutError: logger.warning(f"Timeout stopping recognizer for call {call_connection_id}")
            except Exception as e: logger.error(f"Error stopping recognizer for call {call_connection_id}: {e}", exc_info=True)
        if push_stream: push_stream.close()
        if websocket.client_state == WebSocketState.CONNECTED: await websocket.close()
        if call_connection_id in call_user_raw_ids:
            try: del call_user_raw_ids[call_connection_id]
            except KeyError: pass


# ----------------------------- Main Flow (Browser) -----------------------------
async def main_conversation(websocket: WebSocket, cm: ConversationManager):
    while True:
        try:
            # <-- receive one frame raw
            prompt_raw = await websocket.receive_text()
        except WebSocketDisconnect:
            return

        # <-- interrupt filter
        try:
            msg = json.loads(prompt_raw)
            if msg.get("type") == "interrupt":
                logger.info("🛑 Interrupt received; stopping TTS and skipping GPT")
                tts_client.stop_speaking()
                continue
        except json.JSONDecodeError:
            pass

        # <-- now parse true user text
        try:
            prompt = json.loads(prompt_raw).get("text", prompt_raw)
        except json.JSONDecodeError:
            prompt = prompt_raw.strip()

        if not prompt:
            continue
        if check_for_stopwords(prompt):
            goodbye = "Thank you for using our service. Goodbye."
            await websocket.send_text(json.dumps({"type": "exit", "message": goodbye}))
            await send_tts_audio(goodbye, websocket)
            return

        await process_gpt_response(cm, prompt, websocket)


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
):
    """
    Process GPT response and send output to websocket.
    If is_acs is True, format the response as ACS-compatible AudioData JSON.
    """
    cm.hist.append({"role": "user", "content": user_prompt})
    logger.info(f"🎙️ User input received: {user_prompt}")
    tool_name = tool_call_id = function_call_arguments = ""
    collected_messages = []

    try:
        response = az_openai_client.chat.completions.create(
            stream=True,
            messages=cm.hist,
            tools=available_tools,
            tool_choice="auto",
            max_tokens=4096,
            temperature=0.5,
            top_p=1.0,
            model=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_ID"),
        )

        full_response = ""
        tool_call_started = False

        for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if delta.tool_calls:
                tool_call = delta.tool_calls[0]
                tool_call_id = tool_call.id or tool_call_id
                if tool_call.function.name:
                    tool_name = tool_call.function.name
                if tool_call.function.arguments:
                    function_call_arguments += tool_call.function.arguments
                tool_call_started = True
                continue

            if delta.content:
                chunk_text = delta.content
                collected_messages.append(chunk_text)
                full_response += chunk_text
                pass # No change needed here for collecting messages


        final_text = "".join(collected_messages).strip()
        if final_text:
            if is_acs:
                try:
                    #---------- Play the complete message via ACS TTS ----------
                    ## Current Issue: Consistent 8500 error when playing TTS
                    ## This is likely due to the media operation being busy.
                    # asyncio.create_task(acs_caller.play_response(call_connection_id=call_id, response_text=final_text))
                    # call_conn = acs_caller.call_automation_client.get_call_connection(cm.cid)

                    # source = TextSource(
                    #     text=final_text,
                    #     voice_name="en-US-JennyNeural",
                    #     source_locale="en-US"
                    # )

                    # await play_tts_safely(websocket, call_conn, source, cm.cid)
                    # ========================
                    pcm = tts_client.synthesize_to_base64_frames(text=final_text, sample_rate=16000)
                    await send_pcm_frames(websocket, pcm, sample_rate=16000) # Send PCM frames to ACS WebSocket
                except Exception as e:
                    logger.error(f"Failed to play final TTS via ACS for call {call_id}: {e}", exc_info=True)
            else: # Browser - final text already streamed
                 pass

            # Append final assistant response to history
            cm.hist.append({"role": "assistant", "content": final_text})
            logger.info(f"🧠 Assistant final response generated: {final_text}")

        if tool_call_started and tool_call_id and tool_name and function_call_arguments:
            cm.hist.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": function_call_arguments
                        }
                    }
                ]
            })
            tool_result = await handle_tool_call(tool_name, tool_call_id, function_call_arguments, cm, websocket)
            return tool_result if tool_name == "authenticate_user" else None

    except asyncio.CancelledError:
        logger.info("GPT processing task cancelled.")
    except Exception as e:
        logger.error(f"Error processing GPT response: {e}", exc_info=True)
        # Send error message to client if possible
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_text(json.dumps({"type": "error", "message": f"Error processing request: {e}"}))
            except Exception as send_err:
                 logger.error(f"Failed to send error to client: {send_err}")

    return None

# ----------------------------- Tool Handler -----------------------------
async def handle_tool_call(tool_name, tool_id, function_call_arguments, cm: ConversationManager, websocket: WebSocket, is_acs: bool = False, call_id: Optional[str] = None):
    try:
        parsed_args = json.loads(function_call_arguments.strip() or "{}")
        function_to_call = function_mapping.get(tool_name)
        if function_to_call:
            result_json = await function_to_call(parsed_args)
            result = json.loads(result_json) if isinstance(result_json, str) else result_json

            cm.hist.append({
                "tool_call_id": tool_id,
                "role": "tool",
                "name": tool_name,
                "content": json.dumps(result),
            })

            await process_tool_followup(cm, websocket, is_acs, call_id)
            return result
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing function arguments: {e}")
    return {}

# ----------------------------- Follow-Up -----------------------------
async def process_tool_followup(cm: ConversationManager, websocket: WebSocket, is_acs: bool = False, call_id: Optional[str] = None):
    collected_messages = []
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
        if hasattr(delta, "content") and delta.content:
            chunk_message = delta.content
            collected_messages.append(chunk_message)

    final_text = "".join(collected_messages).strip()
    if final_text:
        if is_acs:
             # Ensure acs_caller and call_id are available for followup TTS
            pcm = tts_client.synthesize_to_base64_frames(text=final_text, sample_rate=16000)
            await send_pcm_frames(websocket, pcm, sample_rate=16000) # Send PCM frames to ACS WebSocket

        else: # Not ACS
            await websocket.send_text(json.dumps({"type": "assistant", "content": final_text}))
            await send_tts_audio(final_text, websocket)

        # Append assistant response regardless of successful TTS playback
        cm.hist.append({"role": "assistant", "content": final_text})
        logger.info(f"🧠 Assistant followup response generated: {final_text}") # Log generation

# ----------------------------- Health -----------------------------
@app.get("/health")
async def read_health():
    return {"message": "Server is running!"}

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8010)
