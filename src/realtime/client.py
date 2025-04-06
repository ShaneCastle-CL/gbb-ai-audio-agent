"""
RealtimeClient is the high-level controller for handling realtime conversations,
audio, messaging, tool execution, and interaction with the Realtime API.
"""

import asyncio
import json
import logging
import traceback
from datetime import datetime
from typing import Dict, Any, Callable, Optional, List

import numpy as np

from src.realtime.api import RealtimeAPI
from src.realtime.conversation import RealtimeConversation
from src.realtime.utils import array_buffer_to_base64

logger = logging.getLogger(__name__)


class RealtimeClient(RealtimeAPI):
    """
    Client class to manage realtime conversation flow, messaging, and tool execution.
    """

    def __init__(self, system_prompt: str) -> None:
        super().__init__()
        self.system_prompt = system_prompt
        self.default_session_config: Dict[str, Any] = {
            "modalities": ["text", "audio"],
            "instructions": self.system_prompt,
            "voice": "shimmer",
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {"model": "whisper-1"},
            "turn_detection": {"type": "server_vad"},
            "tools": [],
            "tool_choice": "auto",
            "temperature": 0.8,
            "max_response_output_tokens": 4096,
        }
        self.session_config: Dict[str, Any] = {}
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.input_audio_buffer: bytearray = bytearray()
        self.session_created: bool = False
        self.conversation = RealtimeConversation()

        self._reset_config()
        self._add_api_event_handlers()

    def _reset_config(self) -> None:
        """
        Reset session configuration and tool registry.
        """
        self.session_config = self.default_session_config.copy()
        self.tools.clear()
        self.input_audio_buffer.clear()
        self.session_created = False

    def _add_api_event_handlers(self) -> None:
        """
        Attach event handlers to RealtimeAPI for conversation updates.
        """
        self.on("client.*", self._log_event)
        self.on("server.*", self._log_event)
        self.on("server.session.created", self._on_session_created)
        self.on("server.response.created", self._process_event)
        self.on("server.response.output_item.added", self._process_event)
        self.on("server.response.content_part.added", self._process_event)
        self.on("server.input_audio_buffer.speech_started", self._on_speech_started)
        self.on("server.input_audio_buffer.speech_stopped", self._on_speech_stopped)
        self.on("server.conversation.item.created", self._on_item_created)
        self.on("server.conversation.item.truncated", self._process_event)
        self.on("server.conversation.item.deleted", self._process_event)
        self.on("server.conversation.item.input_audio_transcription.completed", self._process_event)
        self.on("server.response.audio_transcript.delta", self._process_event)
        self.on("server.response.audio.delta", self._process_event)
        self.on("server.response.text.delta", self._process_event)
        self.on("server.response.function_call_arguments.delta", self._process_event)
        self.on("server.response.output_item.done", self._on_output_item_done)

    def _log_event(self, event: Dict[str, Any]) -> None:
        """
        Log incoming realtime events.

        Args:
            event (Dict[str, Any]): Event payload.
        """
        realtime_event = {
            "time": datetime.utcnow().isoformat(),
            "source": "client" if event["type"].startswith("client.") else "server",
            "event": event,
        }
        self.dispatch("realtime.event", realtime_event)

    def _on_session_created(self, event: Dict[str, Any]) -> None:
        """
        Handler when a new session is successfully created.
        """
        self.session_created = True

    def _process_event(self, event: Dict[str, Any], *args: Any) -> Any:
        """
        Dispatch incoming conversation events.

        Args:
            event (Dict[str, Any]): Incoming event.
            *args (Any): Additional args if needed.
        """
        item, delta = self.conversation.process_event(event, *args)

        if event["type"] == "conversation.item.input_audio_transcription.completed":
            self.dispatch("conversation.item.input_audio_transcription.completed", {"item": item, "delta": delta})

        if item:
            self.dispatch("conversation.updated", {"item": item, "delta": delta})

        return item, delta

    def _on_speech_started(self, event: Dict[str, Any]) -> None:
        self._process_event(event)
        self.dispatch("conversation.interrupted", event)

    def _on_speech_stopped(self, event: Dict[str, Any]) -> None:
        self._process_event(event, self.input_audio_buffer)

    def _on_item_created(self, event: Dict[str, Any]) -> None:
        item, delta = self._process_event(event)
        self.dispatch("conversation.item.appended", {"item": item})

        if item and item.get("status") == "completed":
            self.dispatch("conversation.item.completed", {"item": item})

    async def _on_output_item_done(self, event: Dict[str, Any]) -> None:
        item, delta = self._process_event(event)

        if item and item.get("status") == "completed":
            self.dispatch("conversation.item.completed", {"item": item})

        if item and item.get("formatted", {}).get("tool"):
            await self._call_tool(item["formatted"]["tool"])

    async def _call_tool(self, tool: Dict[str, Any]) -> None:
        """
        Execute a registered tool function with the parsed arguments.

        Args:
            tool (Dict[str, Any]): Tool descriptor with arguments.
        """
        try:
            logger.debug(f"Calling tool: {tool}")
            json_arguments = json.loads(tool["arguments"])
            tool_config = self.tools.get(tool["name"])

            if not tool_config:
                raise Exception(f"Tool '{tool['name']}' not registered.")

            result = await tool_config["handler"](**json_arguments)

            await self.send("conversation.item.create", {
                "item": {
                    "type": "function_call_output",
                    "call_id": tool["call_id"],
                    "output": json.dumps(result),
                }
            })
        except Exception as e:
            logger.error(traceback.format_exc())
            await self.send("conversation.item.create", {
                "item": {
                    "type": "function_call_output",
                    "call_id": tool["call_id"],
                    "output": json.dumps({"error": str(e)}),
                }
            })
        await self.create_response()

    async def connect_client(self) -> None:
        """
        Connect to the Realtime API and initialize the session.
        """
        if self.is_connected():
            raise RuntimeError("Already connected. Disconnect first.")
        await self.connect()
        await self.update_session()

    async def disconnect_client(self) -> None:
        """
        Disconnect the client and clear session state.
        """
        self.session_created = False
        self.conversation.clear()

        if self.is_connected():
            await self.disconnect()

    async def wait_for_session_created(self) -> None:
        """
        Wait until a session is created.
        """
        if not self.is_connected():
            raise RuntimeError("Not connected. Call connect() first.")
        while not self.session_created:
            await asyncio.sleep(0.001)

    def reset(self) -> None:
        """
        Reset the client to initial state.
        """
        self.clear_event_handlers()
        self._reset_config()
        self._add_api_event_handlers()

    async def update_session(self, **kwargs: Any) -> None:
        """
        Update session configuration with optional parameters.
        """
        self.session_config.update(kwargs)
        session = {
            **self.session_config,
            "tools": [
                {**tool["definition"], "type": "function"} for tool in self.tools.values()
            ],
        }
        await self.send("session.update", {"session": session})

    async def send_user_message_content(self, content: List[Dict[str, Any]]) -> None:
        """
        Send a user message content array to the conversation.

        Args:
            content (List[Dict[str, Any]]): Content array (audio, text, etc.)
        """
        if content:
            for c in content:
                if c["type"] == "input_audio" and isinstance(c["audio"], (bytes, bytearray)):
                    c["audio"] = array_buffer_to_base64(np.array(c["audio"]))

            await self.send("conversation.item.create", {
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": content,
                }
            })

        await self.create_response()

    async def append_input_audio(self, array_buffer: np.ndarray):
        try:
            if isinstance(array_buffer, bytearray):
                array_buffer = bytes(array_buffer)

            base64_audio = array_buffer_to_base64(array_buffer)
            await self.send("input_audio_buffer.append", {"audio": base64_audio})

            logger.info(f"🎤 Sent {len(array_buffer)} bytes of microphone input to GPT.")

            self.input_audio_buffer.extend(array_buffer)
            return True
        except Exception as e:
            logger.error(f"Error appending input audio: {e}")
            return False


    async def create_response(self) -> None:
        """
        Finalize and send a response creation event.
        """
        if self.conversation.get_turn_detection_type() is None and len(self.input_audio_buffer) > 0:
            await self.send("input_audio_buffer.commit")
            self.conversation.queue_input_audio(self.input_audio_buffer)
            self.input_audio_buffer = bytearray()
        await self.send("response.create")

    async def cancel_response(self, item_id: Optional[str] = None, sample_count: int = 0) -> Dict[str, Any]:
        """
        Cancel a response, optionally truncating an audio item.

        Args:
            item_id (Optional[str]): ID of the item to cancel.
            sample_count (int): Samples to truncate to.

        Returns:
            Dict[str, Any]: Result of the cancellation.
        """
        if not item_id:
            await self.send("response.cancel")
            return {"item": None}

        item = self.conversation.get_item(item_id)
        if not item:
            raise Exception(f"Item '{item_id}' not found.")

        if item["type"] != "message" or item["role"] != "assistant":
            raise Exception("Can only cancel assistant message items.")

        await self.send("response.cancel")

        audio_index = next((i for i, c in enumerate(item["content"]) if c["type"] == "audio"), -1)
        if audio_index == -1:
            raise Exception("No audio found on item to cancel.")

        await self.send("conversation.item.truncate", {
            "item_id": item_id,
            "content_index": audio_index,
            "audio_end_ms": int((sample_count / self.conversation.default_frequency) * 1000),
        })

        return {"item": item}
    
    async def add_tool(self, definition, handler):
        if not definition.get("name"):
            raise Exception("Missing tool name in definition")
        name = definition["name"]
        if name in self.tools:
            raise Exception(f'Tool "{name}" already added.')
        if not callable(handler):
            raise Exception(f'Tool "{name}" handler must be callable')
        self.tools[name] = {"definition": definition, "handler": handler}
        return self.tools[name]


