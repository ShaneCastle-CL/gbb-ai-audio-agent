import asyncio
import json
import logging
import traceback
from datetime import datetime
from typing import Dict, List, Optional

from src.realtime_client.api import RealtimeAPI
from src.realtime_client.conversation import RealtimeConversation
from src.realtime_client.event_handler import RealtimeEventHandler
from .utils import array_buffer_to_base64

logger = logging.getLogger(__name__)

class RealtimeClient(RealtimeEventHandler):
    """
    Client orchestrator that manages RealtimeAPI, conversation tracking,
    session configuration, and user interactions.
    """

    def __init__(self, system_prompt: str) -> None:
        super().__init__()
        self.system_prompt = system_prompt
        self.realtime = RealtimeAPI()
        self.conversation = RealtimeConversation()
        self.session_created = False

        self.default_session_config = {
            "modalities": ["text", "audio"],
            "instructions": system_prompt,
            "voice": "ballad",
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {"model": "whisper-1"},
            "turn_detection": {"type": "server_vad"},
            "tools": [],
            "tool_choice": "auto",
            "temperature": 1,
            "max_response_output_tokens": 4096,
        }

        self.session_config: dict = {}
        self.tools: Dict[str, dict] = {}
        self.input_audio_buffer: bytearray = bytearray()

        self._reset_config()
        self._add_api_event_handlers()

    def _reset_config(self) -> None:
        self.session_created = False
        self.session_config = self.default_session_config.copy()
        self.tools = {}
        self.input_audio_buffer = bytearray()

    def _add_api_event_handlers(self) -> None:
        self.realtime.on("client.*", self._log_event)
        self.realtime.on("server.*", self._log_event)
        self.realtime.on("server.session.created", self._on_session_created)
        self.realtime.on("server.response.created", self._process_event)
        self.realtime.on("server.response.output_item.added", self._process_event)
        self.realtime.on("server.response.content_part.added", self._process_event)
        self.realtime.on("server.input_audio_buffer.speech_started", self._on_speech_started)
        self.realtime.on("server.input_audio_buffer.speech_stopped", self._on_speech_stopped)
        self.realtime.on("server.conversation.item.created", self._on_item_created)
        self.realtime.on("server.conversation.item.truncated", self._process_event)
        self.realtime.on("server.conversation.item.deleted", self._process_event)
        self.realtime.on("server.conversation.item.input_audio_transcription.completed", self._process_event)
        self.realtime.on("server.response.audio_transcript.delta", self._process_event)
        self.realtime.on("server.response.audio.delta", self._process_event)
        self.realtime.on("server.response.text.delta", self._process_event)
        self.realtime.on("server.response.function_call_arguments.delta", self._process_event)
        self.realtime.on("server.response.output_item.done", self._on_output_item_done)

    def _log_event(self, event: dict) -> None:
        realtime_event = {
            "time": datetime.utcnow().isoformat(),
            "source": "client" if event["type"].startswith("client.") else "server",
            "event": event,
        }
        self.dispatch("realtime.event", realtime_event)

    def _on_session_created(self, event: dict) -> None:
        self.session_created = True

    def _process_event(self, event: dict, *args) -> Optional[tuple]:
        item, delta = self.conversation.process_event(event, *args)
        if event["type"] == "conversation.item.input_audio_transcription.completed":
            self.dispatch("conversation.item.input_audio_transcription.completed", {"item": item, "delta": delta})
        if item:
            self.dispatch("conversation.updated", {"item": item, "delta": delta})
        return item, delta

    def _on_speech_started(self, event: dict) -> None:
        self._process_event(event)
        self.dispatch("conversation.interrupted", event)

    def _on_speech_stopped(self, event: dict) -> None:
        self._process_event(event, self.input_audio_buffer)

    def _on_item_created(self, event: dict) -> None:
        item, delta = self._process_event(event)
        self.dispatch("conversation.item.appended", {"item": item})
        if item and item.get("status") == "completed":
            self.dispatch("conversation.item.completed", {"item": item})

    async def _on_output_item_done(self, event: dict) -> None:
        item, delta = self._process_event(event)
        if item and item.get("status") == "completed":
            self.dispatch("conversation.item.completed", {"item": item})
        if item and item.get("formatted", {}).get("tool"):
            await self._call_tool(item["formatted"]["tool"])

    async def _call_tool(self, tool: dict) -> None:
        try:
            logger.info(f"Calling tool {tool['name']} with arguments: {tool['arguments']}")
            json_arguments = json.loads(tool["arguments"])
            tool_config = self.tools.get(tool["name"])

            if not tool_config:
                raise Exception(f"Tool '{tool['name']}' not registered.")

            result = await tool_config["handler"](**json_arguments)
            await self.realtime.send("conversation.item.create", {
                "item": {
                    "type": "function_call_output",
                    "call_id": tool["call_id"],
                    "output": json.dumps(result),
                }
            })
        except Exception as e:
            logger.error(traceback.format_exc())
            await self.realtime.send("conversation.item.create", {
                "item": {
                    "type": "function_call_output",
                    "call_id": tool["call_id"],
                    "output": json.dumps({"error": str(e)}),
                }
            })
        await self.create_response()

    def is_connected(self) -> bool:
        return self.realtime.is_connected()

    def reset(self) -> bool:
        self.disconnect()
        self.realtime.clear_event_handlers()
        self._reset_config()
        self._add_api_event_handlers()
        return True

    async def connect(self) -> bool:
        if self.is_connected():
            raise Exception("Already connected, call disconnect first.")
        await self.realtime.connect()
        await self.update_session()
        return True

    async def wait_for_session_created(self) -> bool:
        if not self.is_connected():
            raise Exception("Not connected, use connect() first.")
        while not self.session_created:
            await asyncio.sleep(0.001)
        return True

    async def disconnect(self) -> None:
        self.session_created = False
        self.conversation.clear()
        if self.realtime.is_connected():
            await self.realtime.disconnect()

    def get_turn_detection_type(self) -> Optional[str]:
        return self.session_config.get("turn_detection", {}).get("type")

    async def add_tool(self, definition: dict, handler) -> dict:
        if not definition.get("name"):
            raise Exception("Tool definition must have a 'name'.")
        name = definition["name"]
        if name in self.tools:
            raise Exception(f"Tool '{name}' already exists.")
        if not callable(handler):
            raise Exception("Tool handler must be callable.")
        self.tools[name] = {"definition": definition, "handler": handler}
        await self.update_session()
        return self.tools[name]

    def remove_tool(self, name: str) -> bool:
        if name not in self.tools:
            raise Exception(f"Tool '{name}' does not exist.")
        del self.tools[name]
        return True

    async def update_session(self, **kwargs) -> bool:
        self.session_config.update(kwargs)
        tool_definitions = [
            {**definition, "type": "function"} for definition in self.session_config.get("tools", [])
        ] + [{**tool["definition"], "type": "function"} for tool in self.tools.values()]
        session_update = {**self.session_config, "tools": tool_definitions}
        if self.realtime.is_connected():
            await self.realtime.send("session.update", {"session": session_update})
        return True

    async def send_user_message_content(self, content: List[dict]) -> bool:
        if content:
            for c in content:
                if c.get("type") == "input_audio" and isinstance(c.get("audio"), (bytes, bytearray)):
                    c["audio"] = array_buffer_to_base64(c["audio"])
            await self.realtime.send("conversation.item.create", {
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": content,
                }
            })
        await self.create_response()
        return True

    async def create_response(self) -> bool:
        if self.get_turn_detection_type() is None and len(self.input_audio_buffer) > 0:
            await self.realtime.send("input_audio_buffer.commit")
            self.conversation.queue_input_audio(self.input_audio_buffer)
            self.input_audio_buffer = bytearray()
        await self.realtime.send("response.create")
        return True

    async def append_input_audio(self, array_buffer: bytes) -> bool:
        if len(array_buffer) > 0:
            await self.realtime.send("input_audio_buffer.append", {
                "audio": array_buffer_to_base64(array_buffer),
            })
            self.input_audio_buffer.extend(array_buffer)
        return True

    async def wait_for_next_item(self) -> dict:
        event = await self.wait_for_next("conversation.item.appended")
        return {"item": event["item"]}

    async def wait_for_next_completed_item(self) -> dict:
        event = await self.wait_for_next("conversation.item.completed")
        return {"item": event["item"]}
