"""
RealtimeConversation manages the conversation state, items, responses, 
audio, and transcript processing for realtime applications.
"""

import logging
from typing import Optional, Dict, List, Tuple, Any
import numpy as np

from src.realtime.utils import base64_to_array_buffer

logger = logging.getLogger(__name__)


class RealtimeConversation:
    """
    Class to manage conversation items, audio buffers, and transcription events.
    """

    default_frequency: int = 16000  # Default to 16kHz unless configured elsewhere.

    def __init__(self) -> None:
        """
        Initialize the RealtimeConversation instance and reset its state.
        """
        self.clear()

    def clear(self) -> None:
        """
        Reset the conversation state, clearing all items, responses, and queued data.
        """
        self.item_lookup: Dict[str, Dict[str, Any]] = {}
        self.items: List[Dict[str, Any]] = []
        self.response_lookup: Dict[str, Dict[str, Any]] = {}
        self.responses: List[Dict[str, Any]] = []
        self.queued_speech_items: Dict[str, Dict[str, Any]] = {}
        self.queued_transcript_items: Dict[str, Dict[str, Any]] = {}
        self.queued_input_audio: Optional[np.ndarray] = None

    def queue_input_audio(self, input_audio: np.ndarray) -> None:
        """
        Store input audio temporarily for later assignment.

        Args:
            input_audio (np.ndarray): Audio array to queue.
        """
        self.queued_input_audio = input_audio

    def process_event(self, event: Dict[str, Any], *args: Any) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Process a realtime event and update conversation state.

        Args:
            event (Dict[str, Any]): Incoming event containing type and data.
            *args (Any): Optional additional parameters for event processing.

        Returns:
            Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]: 
            The affected item and delta, if applicable.
        """
        event_processor = self.EventProcessors.get(event["type"])
        if not event_processor:
            raise Exception(f"Missing conversation event processor for {event['type']}")
        return event_processor(self, event, *args)

    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve an item by its unique ID.

        Args:
            item_id (str): The ID of the item.

        Returns:
            Optional[Dict[str, Any]]: The item if found, else None.
        """
        return self.item_lookup.get(item_id)

    def get_items(self) -> List[Dict[str, Any]]:
        """
        Get a list of all conversation items.

        Returns:
            List[Dict[str, Any]]: A copy of the list of items.
        """
        return self.items[:]

    def _process_item_created(self, event: Dict[str, Any]) -> Tuple[Dict[str, Any], None]:
        """
        Handle the creation of a new conversation item.

        Args:
            event (Dict[str, Any]): Event containing the new item data.

        Returns:
            Tuple[Dict[str, Any], None]: The newly created item and no delta.
        """
        item = event["item"]
        new_item = item.copy()

        if new_item["id"] not in self.item_lookup:
            self.item_lookup[new_item["id"]] = new_item
            self.items.append(new_item)

        new_item["formatted"] = {"audio": [], "text": "", "transcript": ""}

        # Handle queued speech items
        if new_item["id"] in self.queued_speech_items:
            new_item["formatted"]["audio"] = self.queued_speech_items[new_item["id"]].get("audio", [])
            del self.queued_speech_items[new_item["id"]]

        # Process content for text formatting
        if "content" in new_item:
            text_content = [c for c in new_item["content"] if c["type"] in ["text", "input_text"]]
            for content in text_content:
                new_item["formatted"]["text"] += content.get("text", "")

        # Handle queued transcript items
        if new_item["id"] in self.queued_transcript_items:
            new_item["formatted"]["transcript"] = self.queued_transcript_items[new_item["id"]]["transcript"]
            del self.queued_transcript_items[new_item["id"]]

        # Set status and handle specific item types
        if new_item["type"] == "message":
            if new_item["role"] == "user":
                new_item["status"] = "completed"
                if self.queued_input_audio is not None:
                    new_item["formatted"]["audio"] = self.queued_input_audio
                    self.queued_input_audio = None
            else:
                new_item["status"] = "in_progress"
        elif new_item["type"] == "function_call":
            new_item["formatted"]["tool"] = {
                "type": "function",
                "name": new_item["name"],
                "call_id": new_item["call_id"],
                "arguments": ""
            }
            new_item["status"] = "in_progress"
        elif new_item["type"] == "function_call_output":
            new_item["formatted"]["output"] = new_item.get("output", "")
            new_item["status"] = "completed"

        return new_item, None

    def _process_item_truncated(self, event: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], None]:
        """
        Handle truncation of an item's audio data.

        Args:
            event (Dict[str, Any]): Event containing the item ID and truncation details.

        Returns:
            Tuple[Optional[Dict[str, Any]], None]: The updated item and no delta.
        """
        item_id = event["item_id"]
        audio_end_ms = event["audio_end_ms"]

        item = self.item_lookup.get(item_id)
        if not item:
            raise Exception(f"Item '{item_id}' not found for truncation.")

        end_index = (audio_end_ms * self.default_frequency) // 1000
        item["formatted"]["transcript"] = ""
        item["formatted"]["audio"] = item["formatted"]["audio"][:end_index]

        return item, None

    def _process_item_deleted(self, event: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], None]:
        """
        Handle the deletion of a conversation item.
    
        Args:
            event (Dict[str, Any]): Event containing the item ID to delete.
    
        Returns:
            Tuple[Optional[Dict[str, Any]], None]: The deleted item and no delta.
    
        Raises:
            Exception: If the item with the given ID is not found.
        """
        item_id = event["item_id"]
        item = self.item_lookup.pop(item_id, None)
    
        if not item:
            raise Exception(f"Item '{item_id}' not found for deletion.")
    
        self.items.remove(item)
        return item, None
    
    def _process_input_audio_transcription_completed(self, event: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
        """
        Handle the completion of input audio transcription.
    
        Args:
            event (Dict[str, Any]): Event containing the item ID, content index, and transcript.
    
        Returns:
            Tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]]]: 
            The updated item and a dictionary containing the transcript.
    
        Notes:
            If the item is not found, the transcript is queued for later processing.
        """
        item_id = event["item_id"]
        content_index = event["content_index"]
        transcript = event.get("transcript", " ")
    
        item = self.item_lookup.get(item_id)
        if not item:
            self.queued_transcript_items[item_id] = {"transcript": transcript}
            return None, None
    
        item["content"][content_index]["transcript"] = transcript
        item["formatted"]["transcript"] = transcript
    
        return item, {"transcript": transcript}
    
    def _process_speech_started(self, event: Dict[str, Any]) -> Tuple[None, None]:
        """
        Handle the start of speech input.
    
        Args:
            event (Dict[str, Any]): Event containing the item ID and audio start time in milliseconds.
    
        Returns:
            Tuple[None, None]: No item or delta is returned.
        """
        item_id = event["item_id"]
        audio_start_ms = event["audio_start_ms"]
    
        self.queued_speech_items[item_id] = {"audio_start_ms": audio_start_ms}
        return None, None
    
    def _process_speech_stopped(self, event: Dict[str, Any], input_audio_buffer: Optional[np.ndarray]) -> Tuple[None, None]:
        """
        Handle the end of speech input.
    
        Args:
            event (Dict[str, Any]): Event containing the item ID and audio end time in milliseconds.
            input_audio_buffer (Optional[np.ndarray]): The audio buffer to extract the speech segment.
    
        Returns:
            Tuple[None, None]: No item or delta is returned.
        """
        item_id = event["item_id"]
        audio_end_ms = event["audio_end_ms"]
    
        speech = self.queued_speech_items.get(item_id, {})
        speech["audio_end_ms"] = audio_end_ms
    
        if input_audio_buffer is not None:
            start_index = (speech["audio_start_ms"] * self.default_frequency) // 1000
            end_index = (speech["audio_end_ms"] * self.default_frequency) // 1000
            speech["audio"] = input_audio_buffer[start_index:end_index]
    
        return None, None
    
    def _process_response_created(self, event: Dict[str, Any]) -> Tuple[None, None]:
        """
        Handle the creation of a new response.
    
        Args:
            event (Dict[str, Any]): Event containing the response data.
    
        Returns:
            Tuple[None, None]: No item or delta is returned.
        """
        response = event["response"]
    
        if response["id"] not in self.response_lookup:
            self.response_lookup[response["id"]] = response
            self.responses.append(response)
    
        return None, None
    
    def _process_output_item_added(self, event: Dict[str, Any]) -> Tuple[None, None]:
        """
        Handle the addition of an output item to a response.
    
        Args:
            event (Dict[str, Any]): Event containing the response ID and the item to add.
    
        Returns:
            Tuple[None, None]: No item or delta is returned.
    
        Raises:
            Exception: If the response with the given ID is not found.
        """
        response_id = event["response_id"]
        item = event["item"]
    
        response = self.response_lookup.get(response_id)
        if not response:
            raise Exception(f"Response '{response_id}' not found for output item addition.")
    
        response["output"].append(item["id"])
        return None, None
    
    def _process_output_item_done(self, event: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], None]:
        """
        Handle the completion of an output item.
    
        Args:
            event (Dict[str, Any]): Event containing the completed item.
    
        Returns:
            Tuple[Optional[Dict[str, Any]], None]: The updated item and no delta.
    
        Raises:
            Exception: If the item is missing or not found.
        """
        item = event.get("item")
        if not item:
            raise Exception("Missing item in response output item done event.")
    
        found_item = self.item_lookup.get(item["id"])
        if not found_item:
            raise Exception(f"Item '{item['id']}' not found in output item done event.")
    
        found_item["status"] = item["status"]
        return found_item, None
    
    def _process_content_part_added(self, event: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], None]:
        """
        Handle the addition of a content part to an item.
    
        Args:
            event (Dict[str, Any]): Event containing the item ID and the content part to add.
    
        Returns:
            Tuple[Optional[Dict[str, Any]], None]: The updated item and no delta.
    
        Raises:
            Exception: If the item with the given ID is not found.
        """
        item_id = event["item_id"]
        part = event["part"]
    
        item = self.item_lookup.get(item_id)
        if not item:
            raise Exception(f"Item '{item_id}' not found for content part addition.")
    
        item["content"].append(part)
        return item, None
    
    def _process_audio_transcript_delta(self, event: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
        """
        Handle a delta update to an audio transcript.
    
        Args:
            event (Dict[str, Any]): Event containing the item ID, content index, and transcript delta.
    
        Returns:
            Tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]]]: 
            The updated item and a dictionary containing the transcript delta.
    
        Raises:
            Exception: If the item with the given ID is not found.
        """
        item_id = event["item_id"]
        content_index = event["content_index"]
        delta = event["delta"]
    
        item = self.item_lookup.get(item_id)
        if not item:
            raise Exception(f"Item '{item_id}' not found for audio transcript delta.")
    
        item["content"][content_index]["transcript"] += delta
        item["formatted"]["transcript"] += delta
    
        return item, {"transcript": delta}
    
    def _process_audio_delta(self, event: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Handle a delta update to audio data.
    
        Args:
            event (Dict[str, Any]): Event containing the item ID and audio delta.
    
        Returns:
            Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]: 
            The updated item and a dictionary containing the audio delta.
    
        Notes:
            If the item is not found, a debug message is logged.
        """
        item_id = event["item_id"]
        delta = event["delta"]
    
        item = self.item_lookup.get(item_id)
        if not item:
            logger.debug(f"Item '{item_id}' not found for audio delta.")
            return None, None
    
        array_buffer = base64_to_array_buffer(delta)
        append_values = array_buffer.tobytes()
    
        # Placeholder for merging into formatted audio
        # item["formatted"]["audio"] = merge_int16_arrays(item["formatted"]["audio"], append_values)
    
        return item, {"audio": append_values}
    
    def _process_text_delta(self, event: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
        """
        Handle a delta update to text content.
    
        Args:
            event (Dict[str, Any]): Event containing the item ID, content index, and text delta.
    
        Returns:
            Tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]]]: 
            The updated item and a dictionary containing the text delta.
    
        Raises:
            Exception: If the item with the given ID is not found.
        """
        item_id = event["item_id"]
        content_index = event["content_index"]
        delta = event["delta"]
    
        item = self.item_lookup.get(item_id)
        if not item:
            raise Exception(f"Item '{item_id}' not found for text delta.")
    
        item["content"][content_index]["text"] += delta
        item["formatted"]["text"] += delta
    
        return item, {"text": delta}
    
    def _process_function_call_arguments_delta(self, event: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
        """
        Handle a delta update to function call arguments.
    
        Args:
            event (Dict[str, Any]): Event containing the item ID and arguments delta.
    
        Returns:
            Tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]]]: 
            The updated item and a dictionary containing the arguments delta.
    
        Raises:
            Exception: If the item with the given ID is not found.
        """
        item_id = event["item_id"]
        delta = event["delta"]
    
        item = self.item_lookup.get(item_id)
        if not item:
            raise Exception(f"Item '{item_id}' not found for function call arguments delta.")
    
        item["arguments"] += delta
        item["formatted"]["tool"]["arguments"] += delta
    
        return item, {"arguments": delta}

    # Event dispatch table
    EventProcessors = {
        "conversation.item.created": _process_item_created,
        "conversation.item.truncated": _process_item_truncated,
        "conversation.item.deleted": _process_item_deleted,
        "conversation.item.input_audio_transcription.completed": _process_input_audio_transcription_completed,
        "input_audio_buffer.speech_started": _process_speech_started,
        "input_audio_buffer.speech_stopped": _process_speech_stopped,
        "response.created": _process_response_created,
        "response.output_item.added": _process_output_item_added,
        "response.output_item.done": _process_output_item_done,
        "response.content_part.added": _process_content_part_added,
        "response.audio_transcript.delta": _process_audio_transcript_delta,
        "response.audio.delta": _process_audio_delta,
        "response.text.delta": _process_text_delta,
        "response.function_call_arguments.delta": _process_function_call_arguments_delta,
    }
