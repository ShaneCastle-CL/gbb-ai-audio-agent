import os
import json
import asyncio
import uuid
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from openai import AzureOpenAI

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
    check_drug_interactions


)
from usecases.browser_RTMedAgent.backend.prompt_manager import PromptManager
from usecases.browser_RTMedAgent.backend.tools import available_tools
from utils.ml_logging import get_logger

# ----------------------------- App & Middleware -----------------------------
app: FastAPI = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STOP_WORDS: List[str] = ["goodbye", "exit", "see you later", "bye"]
logger = get_logger()
prompt_manager: PromptManager = PromptManager()
az_openai_client: AzureOpenAI = AzureOpenAI(
    api_version="2025-02-01-preview",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
    api_key=os.getenv("AZURE_OPENAI_KEY", ""),
)
az_speech_synthesizer_client: SpeechSynthesizer = SpeechSynthesizer()

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

class ConversationManager:
    """
    Manages conversation history and context for the voice agent.

    Attributes:
        pm: PromptManager instance for retrieving system prompts.
        cid: Unique conversation identifier.
        hist: List of message dicts representing the conversation history.
    """
    def __init__(self, auth: bool = True)-> None:
        self.pm: PromptManager = PromptManager()
        self.cid: str = str(uuid.uuid4())[:8]
        prompt_key: str = (
            "voice_agent_authentication.jinja"
            if auth
            else "voice_agent_system.jinja"
        )
        if auth:
            # TODO: Add dynamic flow for patient metadata when PromptManager supports it
            system_prompt: str = self.pm.get_prompt(prompt_key)
        else:
            system_prompt: str = self.pm.create_prompt_system_main()

        self.hist: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]


def check_for_stopwords(prompt: str) -> bool:
    """
    Check whether the user's prompt contains any predefined stop words.
    """
    return any(stop_word in prompt.lower() for stop_word in STOP_WORDS)


def check_for_interrupt(prompt: str) -> bool:
    """
    Determine if the incoming message is an interrupt command.
    """
    return "interrupt" in prompt.lower()

async def send_tts_audio(text: str, websocket: WebSocket) -> None:
    """
    Initiate text-to-speech synthesis and measure TTS enqueue latency.
    """
    start = time.perf_counter()
    try:
        az_speech_synthesizer_client.start_speaking_text(text)
    except Exception as e:
        logger.error(f"Error synthesizing TTS: {e}")
    end = time.perf_counter()
    logger.info(f"🗣️ TTS enqueue time: {(end - start)*1000:.1f} ms")

async def receive_and_filter(websocket: WebSocket) -> Optional[str]:
    """
    Receive a single WebSocket frame, filter interrupts, and measure receive latency.
    """
    start = time.perf_counter()
    raw: str = await websocket.receive_text()
    end = time.perf_counter()
    logger.info(f"📥 WS receive time: {(end - start)*1000:.1f} ms")
    try:
        msg: Dict[str, Any] = json.loads(raw)
        if msg.get("type") == "interrupt":
            logger.info("🛑 Interrupt received, stopping TTS")
            az_speech_synthesizer_client.stop_speaking()
            return None
    except json.JSONDecodeError:
        pass
    return raw

@app.websocket("/realtime")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    Entry point for WebSocket connections. Handles authentication then main conversation.
    """
    await websocket.accept()
    cm: ConversationManager = ConversationManager(auth=True)
    caller_ctx = await authentication_conversation(websocket, cm)
    if caller_ctx:
        cm = ConversationManager(auth=False)
        await main_conversation(websocket, cm)

async def authentication_conversation(
    websocket: WebSocket, cm: ConversationManager
) -> Optional[Dict[str, Any]]:
    """
    Conduct the authentication flow over WebSocket.
    """
    greeting = (
        "Hello from XMYX Healthcare Company! Before I can assist you, let’s "
        "verify your identity. How may I address you?"
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
        auth_end = time.perf_counter()
        auth_total = (auth_end - auth_start) * 1000
        logger.info(
            f"[Latency Summary] phase:auth | cid:{cm.cid} | total:{auth_total:.1f}ms"
        )
        if result and result.get("authenticated"):
            return result

async def main_conversation(websocket: WebSocket, cm: ConversationManager) -> None:
    """
    Handle the main multi-turn conversation after authentication.
    """
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
        total_end = time.perf_counter()
        e2e_total = (total_end - total_start) * 1000
        logger.info(
            f"📊 phase:main | cid:{cm.cid} | total:{e2e_total:.1f}ms"
        )

async def process_gpt_response(
    cm: ConversationManager, user_prompt: str, websocket: WebSocket
) -> Optional[Dict[str, Any]]:
    """
    Send the user's prompt to GPT, stream and log per-chunk latency, handle tools, and send back TTS.

    Returns tool result if authenticate_user, else None.
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
        prev_ts = stream_start
        tool_started = False
        tool_name = tool_id = args = ""

        for chunk in response:
            now = time.perf_counter()
            chunk_ms = (now - prev_ts) * 1000
            logger.info(f"🔸 Chunk arrived after: {chunk_ms:.1f} ms")
            prev_ts = now
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.tool_calls:
                tc = delta.tool_calls[0]
                tool_id = tc.id or tool_id
                if tc.function.name:
                    tool_name = tc.function.name
                if tc.function.arguments:
                    args += tc.function.arguments
                tool_started = True
                continue
            if delta.content:
                collected.append(delta.content)

        stream_end = time.perf_counter()
        logger.info(f"💬 GPT full stream time: {(stream_end - stream_start)*1000:.1f} ms")

        text = "".join(collected).strip()
        if text:
            await websocket.send_text(json.dumps({"type": "assistant", "content": text}))
            await send_tts_audio(text, websocket)
            cm.hist.append({"role": "assistant", "content": text})
            logger.info(f"🧠 Assistant responded: {text}")

        if tool_started:
            cm.hist.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": tool_id, "type": "function", "function": {"name": tool_name, "arguments": args}}],
            })
            return await handle_tool_call(tool_name, tool_id, args, cm, websocket)
    except asyncio.CancelledError:
        logger.info(f"🔚 process_gpt_response cancelled for input: '{user_prompt[:40]}'")
        raise 
    return None

async def handle_tool_call(
    tool_name: str,
    tool_id: str,
    function_call_arguments: str,
    cm: ConversationManager,
    websocket: WebSocket,
) -> Any:
    """
    Execute mapped function tool, measure its duration, and follow up.
    """
    try:
        params = json.loads(function_call_arguments.strip() or "{}")
        fn = function_mapping.get(tool_name)
        if fn:
            t0 = time.perf_counter()
            result_json = await fn(params)
            t1 = time.perf_counter()
            logger.info(f"⚙️ Tool '{tool_name}' exec time: {(t1 - t0)*1000:.1f} ms")
            result = json.loads(result_json) if isinstance(result_json, str) else result_json
            cm.hist.append({"tool_call_id": tool_id, "role": "tool", "name": tool_name, "content": json.dumps(result)})
            await process_tool_followup(cm, websocket)
            return result
    except Exception as e:
        logger.error(f"Tool '{tool_name}' error: {e}")
    return {}

async def process_tool_followup(cm: ConversationManager, websocket: WebSocket):

    try: 
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
            await websocket.send_text(json.dumps({"type": "assistant", "content": final_text}))
            await send_tts_audio(final_text, websocket)
            cm.hist.append({"role": "assistant", "content": final_text})
            logger.info(f"🧠 Assistant said: {final_text}")
    
    except asyncio.CancelledError:
        logger.info(f"🔚 process_tool_followup")
        raise 
    return None

@app.get("/health")
async def read_health() -> Dict[str, str]:
    """
    Health check endpoint.
    """
    return {"message": "Server is running!"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
