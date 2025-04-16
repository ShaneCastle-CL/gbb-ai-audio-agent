import os
import time
import json
import base64
from typing import List, Dict
import asyncio
import contextlib

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# -- Import your GPT + Tools + TTS modules --
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

# -- FastAPI Setup --
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # or ["*"] for dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# If you have some frontend static files
app.mount("/static", StaticFiles(directory="app/frontend"), name="static")

@app.websocket("/ws")
async def echo_endpoint(websocket: WebSocket):
    """
    Simple echo example (unused if you're focusing on /realtime).
    """
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        print(f"Received message: {data}")
        await websocket.send_text(f"Message received: {data}")

# -- Serve a basic index.html if needed --
@app.get("/", response_class=HTMLResponse)
async def get_index():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    frontend_dir = os.path.join(current_dir, "..", "frontend")
    index_path = os.path.join(frontend_dir, "index.html")
    with open(index_path, encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), status_code=200)

# -- Basic configs --
STOP_WORDS = ["goodbye", "exit", "see you later", "bye"]
logger = get_logger()

# -- Prompt Setup --
prompt_manager = PromptManager()
system_prompt = prompt_manager.get_prompt("voice_agent_system.jinja")

# -- GPT/OpenAI & TTS Clients --
az_openai_client = AzureOpenAI(
    api_version="2025-02-01-preview",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_KEY"),
)
az_speech_synthesizer_client = SpeechSynthesizer()

# -- Mapping from tool_name -> your Python function --
function_mapping = {
    "schedule_appointment": schedule_appointment,
    "refill_prescription": refill_prescription,
    "lookup_medication_info": lookup_medication_info,
    "evaluate_prior_authorization": evaluate_prior_authorization,
    "escalate_emergency": escalate_emergency,
    "authenticate_user": authenticate_user,
}

# -- Helper: check for "stop" keywords --
def check_for_stopwords(prompt: str) -> bool:
    return any(stop_word in prompt.lower() for stop_word in STOP_WORDS)

# -- Helper: Synthesize TTS and send base64 to client --
async def send_tts_audio(text: str, websocket: WebSocket):
    """
    1) Synthesize TTS in memory using your SpeechSynthesizer.
    2) Base64-encode it.
    3) Send it as {"type":"tts_base64","audio":"..."}.
    """
    try:
        wav_bytes = az_speech_synthesizer_client.synthesize_speech(text)
        encoded = base64.b64encode(wav_bytes).decode("utf-8")
        await websocket.send_text(json.dumps({
            "type": "tts_base64",
            "audio": encoded
        }))
    except Exception as e:
        logger.error(f"Error synthesizing TTS: {e}")

# =========================================================
# Main Conversation Logic at /realtime
# =========================================================
@app.websocket("/realtime")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await main_conversation(websocket)

async def main_conversation(websocket: WebSocket) -> None:
    try:
        # 1) Greet the user
        greeting_text = "Hello from XMYX Healthcare Company! We are here to assist you. How can I help you today?"
        # Send a "status" message (optional)
        await websocket.send_text(json.dumps({"type": "status", "message": greeting_text}))
        # Also send TTS
        await send_tts_audio(greeting_text, websocket)
        await asyncio.sleep(1)

        # Keep a conversation history for GPT
        conversation_history: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]

        processing_task = None
        last_cancelled_tokens = None

        while True:
            # 2) Wait for text from the client (already STT'ed in the browser).
            prompt_raw = await websocket.receive_text()
            try:
                prompt_json = json.loads(prompt_raw)
                prompt = prompt_json.get("text", prompt_raw)
            except Exception:
                prompt = prompt_raw

            prompt = prompt.strip()
            if not prompt:
                continue

            logger.info(f"User said: {prompt}")

            # If user says a "stop" word, we exit
            if check_for_stopwords(prompt):
                logger.info("Detected stop word, exiting...")
                exit_text = "Thank you for using our service. Have a great day! Goodbye."
                await websocket.send_text(json.dumps({"type": "exit", "message": exit_text}))
                await send_tts_audio(exit_text, websocket)
                await asyncio.sleep(1)
                break

            # If there's an ongoing GPT response, cancel it
            if processing_task and not processing_task.done():
                processing_task.cancel()
                logger.info(f"🛑 Cancelled ongoing GPT response for new prompt: '{prompt[:40]}'")
                if last_cancelled_tokens:
                    logger.info(f"🛑 Last cancelled tokens: {last_cancelled_tokens}")
                with contextlib.suppress(asyncio.CancelledError):
                    await processing_task

            # 3) Add user prompt to conversation
            conversation_history.append({"role": "user", "content": prompt})

            # 4) Create a new async task to process GPT
            processing_task = asyncio.create_task(
                process_gpt_response(conversation_history.copy(), prompt, websocket)
            )

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.exception("An error occurred in main_conversation()")

# =========================================================
# GPT and TTS Handling
# =========================================================
async def process_gpt_response(history_snapshot, user_prompt, websocket: WebSocket):
    """
    1) Streams GPT output (chunk by chunk).
    2) Detects tool calls and executes them.
    3) Sends text messages + TTS audio back to the client.
    """
    tool_name = None
    function_call_arguments = ""
    tool_id = None
    collected_messages: List[str] = []
    cancelled_tokens = []

    tts_sentence_end = [".", "!", "?", ";", "。", "！", "？", "；", "\n"]
    global last_cancelled_tokens

    try:
        # 1) Send conversation to GPT with streaming
        response = az_openai_client.chat.completions.create(
            stream=True,
            messages=history_snapshot,
            tools=available_tools,
            tool_choice="auto",
            max_tokens=4096,
            temperature=0.5,
            top_p=1.0,
            model=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_ID"),
        )

        # 2) Read each streamed chunk
        for chunk in response:
            if chunk.choices:
                delta = chunk.choices[0].delta

                # If GPT is requesting a tool function call
                if delta.tool_calls:
                    if delta.tool_calls[0].function.name:
                        tool_name = delta.tool_calls[0].function.name
                        tool_id = delta.tool_calls[0].id
                        history_snapshot.append(delta)

                    if delta.tool_calls[0].function.arguments:
                        function_call_arguments += delta.tool_calls[0].function.arguments

                # Otherwise if there's text content
                elif delta.content:
                    chunk_text = delta.content
                    if chunk_text:
                        collected_messages.append(chunk_text)
                        cancelled_tokens.append(chunk_text)

                        # Check for sentence boundaries to TTS partial
                        if chunk_text in tts_sentence_end:
                            text_to_speak = "".join(collected_messages).strip()
                            # Send partial text to client
                            await websocket.send_text(json.dumps({
                                "type": "assistant",
                                "content": text_to_speak
                            }))
                            # Synthesize TTS
                            await send_tts_audio(text_to_speak, websocket)
                            collected_messages.clear()

        last_cancelled_tokens = cancelled_tokens

        # After the stream ends, if there's leftover text
        final_text = "".join(collected_messages).strip()
        if final_text:
            # Send final GPT text
            await websocket.send_text(json.dumps({
                "type": "assistant",
                "content": final_text
            }))
            # TTS
            await send_tts_audio(final_text, websocket)

            # Add it to conversation
            history_snapshot.append({"role": "assistant", "content": final_text})

        # 3) If GPT made a tool call
        if tool_name:
            await handle_tool_call(tool_name, tool_id, function_call_arguments, history_snapshot, websocket)

    except asyncio.CancelledError:
        logger.info(f"🛑 process_gpt_response cancelled for input: '{user_prompt[:40]}'")
        raise

async def handle_tool_call(tool_name, tool_id, function_call_arguments, history_snapshot, websocket):
    """Execute the requested tool and then continue GPT streaming with the tool result."""
    logger.info(f"tool_name: {tool_name}")
    logger.info(f"tool_id: {tool_id}")
    logger.info(f"function_call_arguments: {function_call_arguments}")

    try:
        parsed_args = json.loads(function_call_arguments.strip())
        function_to_call = function_mapping.get(tool_name)

        if function_to_call:
            result = await function_to_call(parsed_args)
            logger.info(f"✅ Function `{tool_name}` executed. Result: {result}")

            # Add the tool response to the conversation
            history_snapshot.append({
                "tool_call_id": tool_id,
                "role": "tool",
                "name": tool_name,
                "content": result,
            })

            # Now call GPT again with the tool result
            await process_tool_followup(history_snapshot, websocket)

    except json.JSONDecodeError as e:
        logger.error(f"❌ Error parsing function arguments: {e}")

async def process_tool_followup(history_snapshot, websocket):
    """
    After a tool result, we ask GPT to continue. 
    We'll again stream text + TTS to the client.
    """
    collected_messages = []
    cancelled_tokens = []
    tts_sentence_end = [".", "!", "?", ";", "。", "！", "？", "；", "\n"]

    second_response = az_openai_client.chat.completions.create(
        stream=True,
        messages=history_snapshot,
        temperature=0.5,
        top_p=1.0,
        max_tokens=4096,
        model=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_ID"),
    )

    for chunk in second_response:
        if chunk.choices:
            delta = chunk.choices[0].delta
            if hasattr(delta, "content") and delta.content:
                chunk_message = delta.content
                collected_messages.append(chunk_message)
                cancelled_tokens.append(chunk_message)

                # If we hit punctuation, TTS partial
                if chunk_message.strip() in tts_sentence_end:
                    text_to_speak = "".join(collected_messages).strip()
                    if text_to_speak:
                        await websocket.send_text(json.dumps({
                            "type": "assistant",
                            "content": text_to_speak
                        }))
                        await send_tts_audio(text_to_speak, websocket)
                        collected_messages.clear()

    final_text = "".join(collected_messages).strip()
    if final_text:
        await websocket.send_text(json.dumps({"type": "assistant", "content": final_text}))
        await send_tts_audio(final_text, websocket)
        history_snapshot.append({"role": "assistant", "content": final_text})

# -- Simple health check --
@app.get("/health")
async def read_health():
    return {"message": "Server is running!"}

# -- Run Uvicorn server --
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
