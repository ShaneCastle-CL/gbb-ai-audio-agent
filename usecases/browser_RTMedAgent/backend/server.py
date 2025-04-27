import os
import json
import asyncio
import uuid
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.websockets import WebSocketState
from fastapi.middleware.cors import CORSMiddleware
from openai import AzureOpenAI
from base64 import b64decode
import contextlib
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
from typing import Dict

# --- Constants ---
BASE_URL = os.getenv("BASE_URL", "https://<your local devtunnel>.use.devtunnels.ms")
ACS_CONNECTION_STRING = os.getenv("ACS_CONNECTION_STRING")
ACS_SOURCE_PHONE_NUMBER = os.getenv("ACS_SOURCE_PHONE_NUMBER")
ACS_CALLBACK_PATH = "/api/acs/callback" 
ACS_WEBSOCKET_PATH = "/realtime-acs" 
ACS_CALL_PATH = "/api/call"

# ----------------------------- App & Middleware -----------------------------
app = FastAPI()
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


STOP_WORDS = ["goodbye", "exit", "see you later", "bye"]
logger = get_logger()
prompt_manager = PromptManager()
az_openai_client = AzureOpenAI(
    api_version="2025-02-01-preview",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_KEY"),
)
az_speech_synthesizer_client = SpeechSynthesizer()

function_mapping = {
    "schedule_appointment": schedule_appointment,
    "refill_prescription": refill_prescription,
    "lookup_medication_info": lookup_medication_info,
    "evaluate_prior_authorization": evaluate_prior_authorization,
    "escalate_emergency": escalate_emergency,
    "authenticate_user": authenticate_user,
}

# --- Instantiate SpeechCoreTranslator ---
try:
    speech_core = SpeechCoreTranslator()
except Exception as e:
    logger.error(f"Failed to initialize SpeechCoreTranslator: {e}")
# -----------------------------------------

# --- ACS Caller Instance ---
acs_caller = None
if ACS_CONNECTION_STRING and ACS_SOURCE_PHONE_NUMBER:
    acs_callback_url = f"{BASE_URL.strip('/')}{ACS_CALLBACK_PATH}" # Ensure no double slashes

    # Construct WebSocket URL safely
    if BASE_URL.startswith("https://"):
        acs_websocket_url = f"{BASE_URL.replace('https://', 'wss://', 1).strip('/')}{ACS_WEBSOCKET_PATH}"
    elif BASE_URL.startswith("http://"):
         # Note: ACS typically requires wss. Using ws might only work in specific network configurations.
        logger.warning("BASE_URL starts with http://. ACS Media Streaming usually requires wss://.")
        acs_websocket_url = f"{BASE_URL.replace('http://', 'ws://', 1).strip('/')}{ACS_WEBSOCKET_PATH}"
    else:
        logger.error(f"Cannot determine WebSocket protocol (wss/ws) from BASE_URL: {BASE_URL}")
        acs_websocket_url = None # Indicate failure

    if acs_websocket_url:
        logger.info(f"ACS Callback URL: {acs_callback_url}")
        logger.info(f"ACS WebSocket URL: {acs_websocket_url}")
        try:
            acs_caller = AcsCaller(
                source_number=ACS_SOURCE_PHONE_NUMBER,
                acs_connection_string=ACS_CONNECTION_STRING,
                acs_callback_path=acs_callback_url,
                acs_media_streaming_websocket_path=acs_websocket_url,
                # tts_translator=speech_core, # Pass the SpeechCoreTranslator instance if needed later
            )
        except Exception as e:
            logger.error(f"Failed to initialize AcsCaller: {e}", exc_info=True)
            acs_caller = None # Ensure acs_caller is None if initialization fails
    else:
         logger.error("Could not construct valid ACS WebSocket URL. ACS calling disabled.")
else:
    logger.warning("ACS environment variables (ACS_CONNECTION_STRING, ACS_SOURCE_PHONE_NUMBER) not fully configured. ACS calling disabled.")
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
        az_speech_synthesizer_client.start_speaking_text(text)
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
            az_speech_synthesizer_client.stop_speaking()
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
                az_speech_synthesizer_client.stop_speaking()
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
def initiate_outbound_call(call_request: CallRequest):
    """Initiates an outbound call using ACS."""
    if not acs_caller:
        logger.error("ACS Caller not initialized, cannot initiate call.")

    target_phone_number = call_request.target_number
    if not target_phone_number:
        logger.error("Target phone number is required.")

    try:
        logger.info(f"Initiating call to {target_phone_number} from {ACS_SOURCE_PHONE_NUMBER}")
        call_id = acs_caller.initiate_call(call_request.target_number)
        # Store that mapping for later
        call_user_raw_ids[call_id] = call_request.target_number
        return {"message": "Call initiated", "callConnectionId": call_id}
    except Exception as e:
        logger.error(f"Failed to initiate call: {e}", exc_info=True)
# --- End API Endpoint ---

# --- ACS Callback Handler ---
@app.post(ACS_CALLBACK_PATH)
async def handle_callbacks(request: Request): # Remove context_id, keep request
    """Handles incoming ACS event callbacks."""
    if not acs_caller:
         logger.error("ACS Caller not initialized, cannot handle callback.")
         return {"error": "ACS Caller not initialized"}
    try:
        await acs_caller.outbound_call_handler(request)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error processing ACS callback: {e}", exc_info=True)
        return {"error": str(e)}
# --- End ACS Callback Handler ---

# High level flow to emulate:
#     ACS_WS-->>Backend: PCM audio chunks
#     Backend-->>STT: push_stream.write(bytes)
#     STT-->>Backend: recognizing (partials)
#     Backend-->>UI: partial transcript
#     STT-->>Backend: recognized (final)
#     Backend->>Backend: append to convo buffer
#     Backend-->>LLM: stream chat(buffer)
#     LLM-->>Backend: token chunks
#     Backend-->>ACS_Play: play_media(TextSource chunk)
#     ACS_Play-->>Caller: TTS audio
#     Caller-->>ACS_WS: continues speaking…


# Map from callConnectionId → human caller’s raw ACS identifier
call_user_raw_ids: Dict[str, str] = {}
@app.websocket(ACS_WEBSOCKET_PATH)
async def acs_websocket_endpoint(websocket: WebSocket):
    await websocket.accept() # Accept client connection
    
    loop = asyncio.get_event_loop() # Get the asyncio event loop
    message_queue = asyncio.Queue() # Create a message queue to store the results of speech recognition
    call_connection_id = websocket.headers.get("x-ms-call-connection-id", "UnknownCall")
    recognizer = None # Initialize recognizer to None
    push_stream = None # Initialize push_stream to None

    # --- Instantiate Conversation Manager ---
    cm = ConversationManager(auth=True) # Create a new conversation manager instance
    cm.cid = call_connection_id # Set the conversation ID to the call connection ID
    user_identifier = call_user_raw_ids.get(call_connection_id)
    if not user_identifier:
        logger.warning(f"No caller rawId for call {call_connection_id} yet; audio will be ignored until we see CallConnected.")

    try:
        # --- Setup Audio Stream and Recognizer ---
        fmt = AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
        push_stream = PushAudioInputStream(stream_format=fmt)

        # Use the new method to create and configure the recognizer
        recognizer = speech_core.create_realtime_recognizer(
            push_stream=push_stream,
            loop=loop,
            message_queue=message_queue,
            language="en-US" # Or dynamically set if needed
        )
        # -----------------------------------------

        logger.info(f"▶ ACS media WebSocket accepted for call {call_connection_id}")
        recognizer.start_continuous_recognition_async()
        logger.info(f"🎙️ Continuous recognition started for call {call_connection_id}")

        # --- Main Loop ---
        while True:
            # Check for recognized text from the queue (non-blocking)
            try:
                recognized_text = message_queue.get_nowait()
                if recognized_text: # Process only if text is not None/empty
                    logger.info(f"Processing recognized text: {recognized_text}")

                    # --- Stopword Check ---
                    if check_for_stopwords(recognized_text):
                        goodbye = "Thank you for calling. Goodbye."
                        logger.info(f"Stopword detected. Playing goodbye message for call {call_connection_id}.")
                        if acs_caller:
                            await acs_caller.play_media(goodbye, call_connection_id)
                        # Optionally send a signal via websocket if needed for other systems
                        # await websocket.send_text(json.dumps({"type": "exit", "message": goodbye}))
                        break # Exit the loop after goodbye

                    # --- Process with GPT ---
                    await process_gpt_response(cm, recognized_text, websocket, is_acs=True, call_id=call_connection_id)
                    # ----------------------
                    
                message_queue.task_done()
            except asyncio.QueueEmpty:
                pass # No new text recognized yet

            # Receive message from WebSocket (with timeout to allow checking the queue)
            try:
                raw_data = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                data = json.loads(raw_data)
            except asyncio.TimeoutError:
                continue # No message received, loop back to check queue/receive again
            except WebSocketDisconnect as e:
                logger.info(f"⚡️ ACS media WebSocket disconnected during message receive for call {call_connection_id}. Code: {e.code}, Reason: {e.reason}")
                break
            except json.JSONDecodeError:
                logger.warning(f"Received non-JSON message: {raw_data}")
                continue
            except Exception as e:
                 logger.error(f"Error receiving or parsing message: {e}")
                 break # Or handle more gracefully

            # --- Handle Different Message Kinds ---
            if data.get("kind") == "AudioMetadata":
                metadata = data.get("audioMetadata")
                if metadata:
                    logger.info(f"Received audio metadata: {metadata}")

            elif data.get("kind") == "AudioData":
                raw_id = data.get("audioData", {}).get("participantRawID")

                if user_identifier and raw_id != user_identifier:
                    logger.debug(f"Ignoring audio from agent (rawId={raw_id})")
                    continue

                try:
                    # Pseudocode after recognizing user speech
                    await websocket.send_text(json.dumps({
                        "Kind": "StopAudio",
                        "AudioData": None,
                        "StopAudio": {}
                    }))
 
                    b64 = data.get("audioData", {}).get("data")
                    pcm = b64decode(b64)
                    push_stream.write(pcm)
                except Exception as e:
                    logger.error(f"Error processing audio data chunk: {e}")
                    # Consider if this error is fatal

            elif data.get("kind") == "CallConnected":
                 logger.info(f"Received CallConnected event: {data}")
            # Add handling for other kinds if necessary

    except WebSocketDisconnect as e:
        logger.info(f"⚡️ ACS media WebSocket disconnected during main loop for call {call_connection_id}. Code: {e.code}, Reason: {e.reason}")
    except Exception as e:
        logger.exception(f"⚠️ Unexpected error during WebSocket handling for call {call_connection_id}: {e}")
    finally:
        logger.info(f"🧹 Cleaning up WebSocket handler for call {call_connection_id}.")

        # Stop the recognizer if it was initialized
        if recognizer:
            logger.info("Stopping continuous recognition...")
            try:
                # Use .get() to wait for the async operation to complete
                recognizer.stop_continuous_recognition_async().get()
                logger.info("✔️ Speech recognizer stopped")
            except Exception as stop_ex:
                logger.error(f"Error stopping recognizer: {stop_ex}")


        # Close the push stream if it was initialized
        if push_stream:
            push_stream.close()
            logger.info("✔️ Push audio stream closed")

        # Close websocket if not already closed
        if websocket.client_state != WebSocketState.DISCONNECTED:
            with contextlib.suppress(Exception): # Suppress errors during close
                await websocket.close()
        logger.info(f"✔️ Media stream handler exited for call {call_connection_id}.")


# ----------------------------- Main Flow -----------------------------
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
                az_speech_synthesizer_client.stop_speaking()
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
    if websocket.client_state == WebSocketState.CONNECTED:
        data = {
            "Kind": "StopAudio",
            "AudioData": None,
            "StopAudio": {}
        }
        # Serialize the server streaming data
        serialized_data = json.dumps(data)
        print(f"Out Streaming Data ---> {serialized_data}")
        # Send the chunk over the WebSocket
        await websocket.send_json(data)
# ----------------------------- GPT Processing -----------------------------
async def process_gpt_response(
    cm: ConversationManager,
    user_prompt: str,
    websocket: WebSocket,
    is_acs: bool = False,
    call_id: Optional[str] = None, # Added call_id parameter
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
                # Synthesize speech and encode as base64 for ACS AudioData
                if acs_caller and call_id:
                    try:
                        # Use the passed call_id
                        await acs_caller.play_response(call_connection_id=call_id, response_text=final_text)
                        logger.info(f"Injected TTS into call {call_id}")
                    except Exception as e:
                        logger.error(f"Failed to play TTS via ACS for call {call_id}: {e}", exc_info=True)
                elif not acs_caller:
                    logger.error(f"ACS caller not initialized, cannot play TTS for call {call_id}")
                else: # call_id is None
                    logger.error(f"No call_id provided, cannot play TTS via ACS for prompt: '{user_prompt[:50]}...'")

            else:
                await websocket.send_text(json.dumps({"type": "assistant", "content": final_text}))
                await send_tts_audio(final_text, websocket)
            # Append assistant response regardless of successful TTS playback
            cm.hist.append({"role": "assistant", "content": final_text})
            logger.info(f"🧠 Assistant said: {final_text}")

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
        logger.info(f"🔚 process_gpt_response cancelled for input: '{user_prompt[:40]}'")
        raise

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
            if acs_caller and call_id:
                try:
                    await acs_caller.play_response(call_connection_id=call_id, response_text=final_text)
                    logger.info(f"Injected tool followup TTS into call {call_id}")
                except Exception as e:
                    logger.error(f"Failed to play tool followup TTS via ACS for call {call_id}: {e}", exc_info=True)
            elif not acs_caller:
                 logger.error(f"ACS caller not initialized, cannot play tool followup TTS for call {call_id}")
            else: # call_id is None
                 logger.error(f"No call_id provided, cannot play tool followup TTS via ACS.")
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
