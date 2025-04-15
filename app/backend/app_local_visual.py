#!/usr/bin/env python3
import os
import time
import json
import asyncio
from typing import List, Dict

# Import your existing modules and functions
from openai import AzureOpenAI
from src.speech.speech_recognizer import StreamingSpeechRecognizer
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

# --- Textual imports for the UI ---
from textual.app import App, ComposeResult
from textual.widgets import RichLog
from textual.containers import Container

# === Conversation Settings ===
STOP_WORDS = ["goodbye", "exit", "stop", "see you later", "bye"]
SILENCE_THRESHOLD = 10

# === Runtime Buffers ===
all_text_live = ""
final_transcripts: List[str] = []
last_final_text: str = None

# === Prompt Setup ===
prompt_manager = PromptManager()
system_prompt = prompt_manager.get_prompt("voice_agent_system.jinja")

# === Function Mapping ===
function_mapping = {
    "schedule_appointment": schedule_appointment,
    "refill_prescription": refill_prescription,
    "lookup_medication_info": lookup_medication_info,
    "evaluate_prior_authorization": evaluate_prior_authorization,
    "escalate_emergency": escalate_emergency,
    "authenticate_user": authenticate_user,
}

# === Clients Setup ===
logger = get_logger()
az_openai_client = AzureOpenAI(
    api_version="2025-02-01-preview",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_KEY"),
)
az_speech_recognizer_client = StreamingSpeechRecognizer(vad_silence_timeout_ms=3000)
az_speech_synthesizer_client = SpeechSynthesizer()

SPEECH_KEY = os.getenv("SPEECH_KEY")
SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION")

tts_sentence_end = [".", "!", "?", ";", "。", "！", "？", "；", "\n"]


def check_for_stopwords(prompt: str) -> bool:
    return any(stop_word in prompt.lower() for stop_word in STOP_WORDS)


def handle_speech_recognition() -> str:
    global all_text_live, final_transcripts, last_final_text

    logger.info("Starting microphone recognition...")
    final_transcripts.clear()
    all_text_live = ""
    last_final_text = None

    def on_partial(text: str) -> None:
        global all_text_live
        all_text_live = text
        logger.debug(f"Partial recognized: {text}")
        az_speech_synthesizer_client.stop_speaking()

    def on_final(text: str) -> None:
        global all_text_live, final_transcripts, last_final_text
        if text and text != last_final_text:
            final_transcripts.append(text)
            last_final_text = text
            all_text_live = ""
            logger.info(f"Finalized text: {text}")

    az_speech_recognizer_client.set_partial_result_callback(on_partial)
    az_speech_recognizer_client.set_final_result_callback(on_final)

    az_speech_recognizer_client.start()
    logger.info("🎤 Listening... (speak now)")

    start_time = time.time()
    while not final_transcripts and (time.time() - start_time < SILENCE_THRESHOLD):
        time.sleep(0.05)

    az_speech_recognizer_client.stop()
    logger.info("🛑 Recognition stopped.")

    return " ".join(final_transcripts) + " " + all_text_live


class VoiceAgentUI(App):
    """Textual UI that displays the conversation log from your voice agent."""
    CSS = """
    Screen { background: #1a1b26; }
    Container { border: double rgb(91, 164, 91); padding: 1; }
    #conversation-log { width: 100%; height: 100%; border: round rgb(205, 133, 63); padding: 1; }
    """

    def compose(self) -> ComposeResult:
        with Container():
            # A single RichLog widget is used to show the conversation history.
            yield RichLog(id="conversation-log")

    async def on_mount(self) -> None:
        self.conversation_log: RichLog = self.query_one("#conversation-log", RichLog)
        # Start the main conversation loop as a background task.
        asyncio.create_task(self.conversation_loop())

    async def conversation_loop(self) -> None:
        try:
            # Initial greeting via TTS; also log the transcript.
            az_speech_synthesizer_client.start_speaking_text(
                "Hello from XMYX Healthcare Company! We are here to assist you. How can I help you today?"
            )
            self.conversation_log.write("[bold yellow]System:[/bold yellow] Hello from XMYX Healthcare Company! We are here to assist you. How can I help you today?")
            # Wait for the greeting to finish.
            await asyncio.sleep(10)

            conversation_history: List[Dict[str, str]] = [
                {"role": "system", "content": system_prompt}
            ]

            # Main loop: listen, process input, stream responses, and update the UI.
            while True:
                # Run the blocking speech recognition in a separate thread.
                prompt = await asyncio.to_thread(handle_speech_recognition)
                if prompt.strip():
                    self.conversation_log.write(f"[bold cyan]User:[/bold cyan] {prompt}")

                    if check_for_stopwords(prompt):
                        self.conversation_log.write("[bold yellow]System:[/bold yellow] Detected stop word, exiting...")
                        az_speech_synthesizer_client.start_speaking_text(
                            "Thank you for using our service. Have a great day! Goodbye."
                        )
                        await asyncio.sleep(8)
                        break

                    conversation_history.append({"role": "user", "content": prompt})

                    tool_name = None
                    function_call_arguments = ""
                    tool_id = None
                    collected_messages: List[str] = []

                    # Stream the assistant response from Azure OpenAI.
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

                    async for chunk in response:
                        if chunk.choices:
                            delta = chunk.choices[0].delta
                            # Check for potential tool calls in the response.
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
                                    if chunk_text.strip() in tts_sentence_end:
                                        text = "".join(collected_messages).strip()
                                        # Speak the assistant's reply.
                                        az_speech_synthesizer_client.start_speaking_text(text)
                                        self.conversation_log.write(f"[bold green]Assistant:[/bold green] {text}")
                                        collected_messages.clear()

                    # If a tool call was detected, process it.
                    if tool_name:
                        self.conversation_log.write(f"[bold magenta]Tool Call Detected:[/bold magenta] {tool_name}")
                        try:
                            parsed_args = json.loads(function_call_arguments.strip())
                            function_to_call = function_mapping.get(tool_name)

                            if function_to_call:
                                result = await function_to_call(parsed_args)
                                self.conversation_log.write(f"[bold magenta]Tool Result:[/bold magenta] {result}")

                                conversation_history.append({
                                    "tool_call_id": tool_id,
                                    "role": "tool",
                                    "name": tool_name,
                                    "content": result,
                                })

                                # Second streaming call after tool execution.
                                second_response = az_openai_client.chat.completions.create(
                                    stream=True,
                                    messages=conversation_history,
                                    temperature=0.5,
                                    top_p=1.0,
                                    max_tokens=4096,
                                    model=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_ID"),
                                )

                                collected_messages = []

                                async for chunk in second_response:
                                    if chunk.choices:
                                        delta = chunk.choices[0].delta
                                        if hasattr(delta, "content") and delta.content:
                                            chunk_message = delta.content
                                            collected_messages.append(chunk_message)
                                            if chunk_message.strip() in tts_sentence_end:
                                                text = ''.join(collected_messages).strip()
                                                if text:
                                                    az_speech_synthesizer_client.start_speaking_text(text)
                                                    self.conversation_log.write(f"[bold green]Assistant:[/bold green] {text}")
                                                    collected_messages.clear()

                                final_text = ''.join(collected_messages).strip()
                                if final_text:
                                    conversation_history.append({"role": "assistant", "content": final_text})
                        except Exception as e:
                            self.conversation_log.write(f"[bold red]Error processing tool call: {e}[/bold red]")
                    else:
                        final_text = ''.join(collected_messages).strip()
                        if final_text:
                            conversation_history.append({"role": "assistant", "content": final_text})
                            self.conversation_log.write(f"[bold green]Assistant:[/bold green] {final_text}")
        except Exception as e:
            # Log any unexpected error in the conversation loop.
            self.conversation_log.write(f"[bold red]Unexpected error: {e}[/bold red]")
        finally:
            # Gracefully exit the app.
            self.exit()


if __name__ == "__main__":
    VoiceAgentUI().run()
