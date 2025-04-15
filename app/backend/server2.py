import os
import time
import json
import base64
import asyncio
import threading
from typing import List, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import azure.cognitiveservices.speech as speechsdk  # Azure Speech SDK

from openai import AzureOpenAI
from src.speech.text_to_speech import SpeechSynthesizer
from app.backend.tools import available_tools
from app.backend.functions import (
    schedule_appointment,
    refill_prescription,
    lookup_medication_info,
    evaluate_prior_authorization,
    escalate_emergency,
    authenticate_user
)
from app.backend.prompt_manager import PromptManager
from utils.ml_logging import get_logger

app = FastAPI()

# Mount static files from the frontend folder
app.mount("/static", StaticFiles(directory="app/frontend"), name="static")

# Serve the HTML page at the root route.
@app.get("/", response_class=HTMLResponse)
async def get_index():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    frontend_dir = os.path.join(current_dir, "..", "frontend")
    index_path = os.path.join(frontend_dir, "index.html")
    with open(index_path, encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), status_code=200)

# --- Conversation and Environment Settings ---
STOP_WORDS = ["goodbye", "exit", "stop", "see you later", "bye"]
# Although our continuous recognition model does not rely on a fixed SILENCE_THRESHOLD,
# we keep it for any fallback scenarios.
SILENCE_THRESHOLD = 10  

all_text_live = ""
final_transcripts: List[str] = []
last_final_text: str = None

prompt_manager = PromptManager()
system_prompt = prompt_manager.get_prompt("voice_agent_system.jinja")

function_mapping = {
    "schedule_appointment": schedule_appointment,
    "refill_prescription": refill_prescription,
    "lookup_medication_info": lookup_medication_info,
    "evaluate_prior_authorization": evaluate_prior_authorization,
    "escalate_emergency": escalate_emergency,
    "authenticate_user": authenticate_user,
}

logger = get_logger()
az_openai_client = AzureOpenAI(
    api_version="2025-02-01-preview",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_KEY"),
)

# We still keep TTS the same.
az_speech_synthesizer_client = SpeechSynthesizer()

SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY")
SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION")

tts_sentence_end = [".", "!", "?", ";", "。", "！", "？", "；", "\n"]

def check_for_stopwords(prompt: str) -> bool:
    return any(stop_word in prompt.lower() for stop_word in STOP_WORDS)


################################################################################
# Custom audio stream callback for continuously feeding incoming WebSocket audio.
################################################################################
class WebSocketAudioStream(speechsdk.audio.PullAudioInputStreamCallback):
    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.queue = asyncio.Queue()
        self.loop = loop  # capture the main event loop

    async def add_audio(self, data: bytes):
        await self.queue.put(data)

    def read(self, buffer):
        """
        This method is called on a separate thread by the Speech SDK.
        We schedule an async queue get on our event loop and wait up to 0.5 seconds.
        """
        try:
            future = asyncio.run_coroutine_threadsafe(self.queue.get(), self.loop)
            data = future.result(timeout=0.5)
        except Exception:
            data = b""
        length = len(data)
        # Copy data to the output buffer.
        buffer[0:length] = data
        return length

    def close(self):
        # Not strictly necessary for this demo.
        pass


###############################################################################
# Asynchronous receive function: expects JSON messages from the front end.
###############################################################################
async def receive_message(websocket: WebSocket) -> dict:
    data = await websocket.receive_text()
    try:
        message = json.loads(data)
    except json.JSONDecodeError:
        message = {"type": "text", "content": data}
    return message


###############################################################################
# Main conversation loop using continuous recognition
###############################################################################
async def main_conversation(websocket: WebSocket) -> None:
    try:
        loop = asyncio.get_event_loop()
        audio_stream_callback = WebSocketAudioStream(loop)
        pull_stream = speechsdk.audio.PullAudioInputStream(audio_stream_callback)
        audio_config = speechsdk.audio.AudioConfig(stream=pull_stream)

        speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
        # Set the language if necessary:
        speech_config.speech_recognition_language = "en-US"
        recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
        
        # This list and event hold final recognized text segments.
        recognized_fragments = []
        final_result_event = threading.Event()

        def recognized_handler(evt: speechsdk.SpeechRecognitionEventArgs):
            # Called for final recognition; this happens in a background thread.
            if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                text = evt.result.text
                logger.info(f"Continuous recognized: {text}")
                recognized_fragments.append(text)
                final_result_event.set()  # Signal that a final result is available.

        # Attach handler for final results.
        recognizer.recognized.connect(recognized_handler)
        recognizer.start_continuous_recognition_async().get()

        # Send an initial greeting.
        greeting = "Hello from XMYX Healthcare Company! We are here to assist you. How can I help you today?"
        await websocket.send_text(json.dumps({"type": "status", "message": greeting}))
        az_speech_synthesizer_client.start_speaking_text(greeting)
        await asyncio.sleep(2)

        conversation_history: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]

        # Run an asynchronous loop that both receives audio buffers and waits for recognition events.
        while True:
            # Check for new WebSocket messages with a short timeout.
            try:
                msg = await asyncio.wait_for(receive_message(websocket), timeout=0.1)
                if msg.get("type") == "input_audio_buffer.append":
                    audio_b64 = msg.get("audio", "")
                    # Decode from base64 and add audio bytes to our custom stream.
                    audio_bytes = base64.b64decode(audio_b64)
                    await audio_stream_callback.add_audio(audio_bytes)
                elif msg.get("type") == "text":
                    # Optionally, allow plain text messages.
                    text = msg.get("content", "").strip()
                    if text:
                        recognized_fragments.append(text)
            except asyncio.TimeoutError:
                # No incoming message; continue.
                pass

            # If a final recognition event was signaled, process the recognized text.
            if final_result_event.is_set():
                prompt = " ".join(recognized_fragments).strip()
                recognized_fragments.clear()
                final_result_event.clear()
                if not prompt:
                    continue

                logger.info(f"Aggregated prompt: {prompt}")
                if check_for_stopwords(prompt):
                    logger.info("Detected stop word. Ending conversation.")
                    exit_text = "Thank you for using our service. Have a great day! Goodbye."
                    az_speech_synthesizer_client.start_speaking_text(exit_text)
                    await websocket.send_text(json.dumps({"type": "exit", "message": exit_text}))
                    break

                conversation_history.append({"role": "user", "content": prompt})

                tool_name = None
                function_call_arguments = ""
                tool_id = None
                collected_messages: List[str] = []

                # Request response from Azure OpenAI.
                response = az_openai_client.chat.completions.create(
                    stream=True,
                    messages=conversation_history,
                    tools=available_tools,
                    tool_choice="auto",
                    max_tokens=4096,
                    temperature=0.5,
                    top_p=1.0,
                    model=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_ID"),
                )
                for chunk in response:
                    if chunk.choices:
                        delta = chunk.choices[0].delta
                        if delta.tool_calls:
                            if delta.tool_calls[0].function.name:
                                tool_name = delta.tool_calls[0].function.name
                                tool_id = delta.tool_calls[0].id
                                conversation_history.append(delta)
                            if delta.tool_calls[0].function.arguments:
                                function_call_arguments += delta.tool_calls[0].function.arguments
                        elif delta.content:
                            chunk_text = delta.content
                            if chunk_text:
                                collected_messages.append(chunk_text)
                                if chunk_text in tts_sentence_end:
                                    text_response = "".join(collected_messages).strip()
                                    az_speech_synthesizer_client.start_speaking_text(text_response)
                                    await websocket.send_text(json.dumps({
                                        "type": "assistant",
                                        "content": text_response
                                    }))
                                    collected_messages.clear()
                if tool_name:
                    logger.info(f"Tool call detected: {tool_name}")
                    try:
                        parsed_args = json.loads(function_call_arguments.strip())
                        function_to_call = function_mapping.get(tool_name)
                        if function_to_call:
                            result = await function_to_call(parsed_args)
                            logger.info(f"✅ Function `{tool_name}` executed. Result: {result}")
                            conversation_history.append({
                                "tool_call_id": tool_id,
                                "role": "tool",
                                "name": tool_name,
                                "content": result,
                            })
                            # Handle second streaming phase after tool execution.
                            second_response = az_openai_client.chat.completions.create(
                                stream=True,
                                messages=conversation_history,
                                temperature=0.5,
                                top_p=1.0,
                                max_tokens=4096,
                                model=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_ID"),
                            )
                            collected_messages = []
                            for chunk in second_response:
                                if chunk.choices:
                                    delta = chunk.choices[0].delta
                                    if hasattr(delta, "content") and delta.content:
                                        chunk_message = delta.content
                                        collected_messages.append(chunk_message)
                                        if chunk_message.strip() in tts_sentence_end:
                                            final_text = ''.join(collected_messages).strip()
                                            if final_text:
                                                az_speech_synthesizer_client.start_speaking_text(final_text)
                                                await websocket.send_text(json.dumps({
                                                    "type": "assistant",
                                                    "content": final_text
                                                }))
                                                collected_messages.clear()
                            final_text = ''.join(collected_messages).strip()
                            if final_text:
                                conversation_history.append({"role": "assistant", "content": final_text})
                    except json.JSONDecodeError as e:
                        logger.error(f"❌ Error parsing tool call arguments: {e}")
                else:
                    final_text = ''.join(collected_messages).strip()
                    if final_text:
                        conversation_history.append({"role": "assistant", "content": final_text})
                        logger.info(f"✅ Final assistant message: {final_text}")
                        await websocket.send_text(json.dumps({
                            "type": "assistant",
                            "content": final_text
                        }))
        # End of loop.
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.exception("An error occurred in main_conversation()")
    finally:
        recognizer.stop_continuous_recognition_async().get()

@app.websocket("/realtime")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await main_conversation(websocket)

@app.get("/health")
async def read_health():
    return {"message": "Server is running!"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
