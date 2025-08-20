"""
V1 ACS Media Handler - Three-Thread Architecture
===============================================

Implements the documented three-thread architecture for low-latency voice interactions:

🧵 Thread 1: Speech SDK Thread (Never Blocks)
- Continuous audio recognition
- Immediate barge-in detection via on_partial callbacks
- Cross-thread communication via run_coroutine_threadsafe

🧵 Thread 2: Route Turn Thread (Blocks on Queue Only)  
- AI processing and response generation
- Orchestrator delegation for TTS and playback
- Queue-based serialization of conversation turns

🧵 Thread 3: Main Event Loop (Never Blocks)
- WebSocket handling and real-time commands
- Task cancellation for barge-in scenarios
- Non-blocking media streaming coordination
"""
import asyncio
import json
import threading
import base64
import time

from dataclasses import dataclass, field
from typing import Optional, Callable, Union, Set
from enum import Enum

from fastapi import WebSocket
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from apps.rtagent.backend.settings import GREETING
from apps.rtagent.backend.src.shared_ws import send_response_to_acs
from apps.rtagent.backend.src.orchestration.orchestrator import route_turn
from src.enums.stream_modes import StreamMode
from src.speech.speech_recognizer import StreamingSpeechRecognizerFromBytes
from src.stateful.state_managment import MemoManager
from utils.ml_logging import get_logger

logger = get_logger("v1.handlers.acs_media_lifecycle")
tracer = trace.get_tracer(__name__)

# Simple thread-safe handler registry - just add a lock
_active_handlers = {}
_handlers_lock = threading.RLock()


class SpeechEventType(Enum):
    """Types of speech recognition events."""
    PARTIAL = "partial"
    FINAL = "final"
    ERROR = "error"
    GREETING = "greeting"
    ANNOUNCEMENT = "announcement"
    STATUS_UPDATE = "status"
    ERROR_MESSAGE = "error_msg"


@dataclass
class SpeechEvent:
    """Speech recognition event with metadata."""
    event_type: SpeechEventType
    text: str
    language: Optional[str] = None
    speaker_id: Optional[str] = None
    confidence: Optional[float] = None
    timestamp: Optional[float] = field(default_factory=time.time)


class ThreadBridge:
    """
    Cross-thread communication bridge.
    
    Provides thread-safe communication between Speech SDK Thread and Main Event Loop.
    Implements the non-blocking patterns described in the documentation.
    """
    def __init__(self):
        """
        Initialize cross-thread communication bridge.

        :param main_loop: Main event loop for cross-thread communication
        :type main_loop: Optional[asyncio.AbstractEventLoop]
        """
        self.main_loop: Optional[asyncio.AbstractEventLoop] = None
        # Create shorthand for call connection ID (last 8 chars)
        self.call_id_short = "unknown"
    
    def set_main_loop(self, loop: asyncio.AbstractEventLoop, call_connection_id: str = None):
        """
        Set the main event loop reference for cross-thread communication.

        :param loop: Main event loop instance
        :type loop: asyncio.AbstractEventLoop
        """
        self.main_loop = loop
        if call_connection_id:
            self.call_id_short = call_connection_id[-8:]

    def schedule_barge_in(self, handler_func: Callable):
        """
        Schedule barge-in handler to run on main event loop ASAP.

        :param handler_func: Barge-in handler function to schedule
        :type handler_func: Callable
        """
        if not self.main_loop or self.main_loop.is_closed():
            logger.warning(f"[{self.call_id_short}] No main loop for barge-in scheduling")
            return

        try:
            asyncio.run_coroutine_threadsafe(handler_func(), self.main_loop)
        except Exception as e:
            logger.error(f"[{self.call_id_short}] Failed to schedule barge-in: {e}")

    def queue_speech_result(self, speech_queue: asyncio.Queue, event: SpeechEvent):
        """
        Queue final speech result for Route Turn Thread processing.

        :param speech_queue: Async queue for speech events
        :type speech_queue: asyncio.Queue
        :param event: Speech event to queue
        :type event: SpeechEvent
        :raises RuntimeError: When unable to queue speech event
        """
        try:
            speech_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(f"[{self.call_id_short}] Speech queue full, dropping event")
        except Exception:
            # Fallback to run_coroutine_threadsafe
            if self.main_loop and not self.main_loop.is_closed():
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        speech_queue.put(event), self.main_loop
                    )
                    future.result(timeout=0.1)
                except Exception as e:
                    logger.error(f"[{self.call_id_short}] Failed to queue speech: {e}")


class SpeechSDKThread:
    """
    Speech SDK Thread Manager - handles continuous audio recognition.

    Handles continuous audio recognition in isolation. Never blocks on AI processing
    or network operations, ensuring immediate barge-in detection capability.

    Key Characteristics:
    - Runs in dedicated background thread
    - Immediate callback execution (< 10ms)
    - Cross-thread communication via ThreadBridge
    - Never blocks on queue operations
    """
    
    def __init__(
            self,
            recognizer: StreamingSpeechRecognizerFromBytes,
            thread_bridge: ThreadBridge,
            barge_in_handler: Callable,
            speech_queue: asyncio.Queue
    ):
        self.recognizer = recognizer
        self.thread_bridge = thread_bridge
        self.barge_in_handler = barge_in_handler
        self.speech_queue = speech_queue
        self.thread_obj: Optional[threading.Thread] = None
        self.thread_running = False
        self.recognizer_started = False
        self.stop_event = threading.Event()
        self._stopped = False

        self._setup_callbacks()

    def _setup_callbacks(self):
        """Configure speech recognition callbacks."""
        def on_partial(text: str, lang: str, speaker_id: Optional[str] = None):
            if len(text.strip()) > 3:  # Only trigger on meaningful partial results
                logger.info(f"[{self.thread_bridge.call_id_short}] Barge-in: '{text[:30]}...' ({lang})")
                try:
                    self.thread_bridge.schedule_barge_in(self.barge_in_handler)
                except Exception as e:
                    logger.error(f"[{self.thread_bridge.call_id_short}] Barge-in error: {e}")

        def on_final(text: str, lang: str, speaker_id: Optional[str] = None):
            if len(text.strip()) > 1:  # Only process meaningful final results
                logger.info(f"[{self.thread_bridge.call_id_short}] Speech: '{text}' ({lang})")
                event = SpeechEvent(
                    event_type=SpeechEventType.FINAL,
                    text=text,
                    language=lang,
                    speaker_id=speaker_id,
                )
                self.thread_bridge.queue_speech_result(self.speech_queue, event)

        def on_error(error: str):
            logger.error(f"[{self.thread_bridge.call_id_short}] Speech error: {error}")
            error_event = SpeechEvent(event_type=SpeechEventType.ERROR, text=error)
            self.thread_bridge.queue_speech_result(self.speech_queue, error_event)

        try:
            self.recognizer.set_partial_result_callback(on_partial)
            self.recognizer.set_final_result_callback(on_final)
            self.recognizer.set_cancel_callback(on_error)
        except Exception as e:
            logger.error(f"[{self.thread_bridge.call_id_short}] Failed to setup callbacks: {e}")
            raise

    def prepare_thread(self):
        """Prepare the speech recognition thread."""
        if self.thread_running:
            return

        def recognition_thread():
            try:
                self.thread_running = True
                while self.thread_running and not self.stop_event.is_set():
                    self.stop_event.wait(0.1)
            except Exception as e:
                logger.error(f"[{self.thread_bridge.call_id_short}] Speech thread error: {e}")
            finally:
                self.thread_running = False

        self.thread_obj = threading.Thread(target=recognition_thread, daemon=True)
        self.thread_obj.start()

    def start_recognizer(self):
        """Start the speech recognizer."""
        if self.recognizer_started or not self.thread_running:
            return

        try:
            logger.info(f"[{self.thread_bridge.call_id_short}] Starting speech recognizer")
            self.recognizer.start()
            self.recognizer_started = True
        except Exception as e:
            logger.error(f"[{self.thread_bridge.call_id_short}] Failed to start recognizer: {e}")
            raise

    def stop(self):
        """Stop speech recognition and thread."""
        if self._stopped:
            return

        self._stopped = True
        self.thread_running = False
        self.recognizer_started = False
        self.stop_event.set()

        if self.recognizer:
            try:
                self.recognizer.stop()
            except Exception as e:
                logger.error(f"[{self.thread_bridge.call_id_short}] Error stopping recognizer: {e}")

        if self.thread_obj and self.thread_obj.is_alive():
            self.thread_obj.join(timeout=2.0)


class RouteTurnThread:
    """
    Route Turn Thread Manager - handles AI processing and response generation.

    Dedicated thread for AI processing and response generation. Can safely block
    on queue operations without affecting speech recognition or WebSocket handling.

    Key Characteristics:
    - Blocks only on queue.get() operations
    - Serializes conversation turns via queue
    - Delegates to orchestrator for response generation
    - Isolated from real-time operations
    """
    
    def __init__(self, speech_queue: asyncio.Queue, orchestrator_func: Callable, memory_manager: Optional[MemoManager], websocket: WebSocket):
        self.speech_queue = speech_queue
        self.orchestrator_func = orchestrator_func
        self.memory_manager = memory_manager
        self.websocket = websocket
        self.processing_task: Optional[asyncio.Task] = None
        self.current_response_task: Optional[asyncio.Task] = None
        self.running = False
        self._stopped = False
        # Get call ID shorthand from websocket if available
        self.call_id_short = getattr(websocket, '_call_connection_id', 'unknown')[-8:] if hasattr(websocket, '_call_connection_id') else 'unknown'

    async def start(self):
        """Start the route turn processing loop."""
        if self.running:
            return

        self.running = True
        self.processing_task = asyncio.create_task(self._processing_loop())

    async def _processing_loop(self):
        """Main processing loop."""
        while self.running:
            try:
                speech_event = await asyncio.wait_for(self.speech_queue.get(), timeout=1.0)
                
                try:
                    if speech_event.event_type == SpeechEventType.FINAL:
                        await self._process_final_speech(speech_event)
                    elif speech_event.event_type in {
                        SpeechEventType.GREETING, 
                        SpeechEventType.ANNOUNCEMENT, 
                        SpeechEventType.STATUS_UPDATE, 
                        SpeechEventType.ERROR_MESSAGE
                    }:
                        await self._process_direct_text_playback(speech_event)
                    elif speech_event.event_type == SpeechEventType.ERROR:
                        logger.error(f"[{self.call_id_short}] Speech error: {speech_event.text}")
                except asyncio.CancelledError:
                    continue  # Barge-in cancellation, continue processing
            except asyncio.TimeoutError:
                continue  # Normal timeout
            except Exception as e:
                logger.error(f"[{self.call_id_short}] Processing loop error: {e}")
                break

    async def _process_final_speech(self, event: SpeechEvent):
        """Process final speech through orchestrator."""
        with tracer.start_as_current_span(
            "route_turn_thread.process_speech",
            kind=SpanKind.CLIENT,
            attributes={"speech.text": event.text, "speech.language": event.language},
        ):
            try:
                if not self.memory_manager:
                    logger.error(f"[{self.call_id_short}] No memory manager available")
                    return

                if self.orchestrator_func:
                    await self.orchestrator_func(
                        cm=self.memory_manager,
                        transcript=event.text,
                        ws=self.websocket,
                        call_id=getattr(self.websocket, "_call_connection_id", None),
                        is_acs=True,
                    )
                else:
                    await route_turn(
                        cm=self.memory_manager,
                        transcript=event.text,
                        ws=self.websocket,
                        is_acs=True,
                    )
            except Exception as e:
                logger.error(f"[{self.call_id_short}] Speech processing error: {e}")

    async def _process_direct_text_playback(self, event: SpeechEvent):
        """
        Process direct text playback through send_response_to_acs (bypasses orchestrator).

        Generic method for sending text directly to ACS for TTS playback. Can be used for:
        - Greeting messages
        - System announcements
        - Error messages
        - Status updates
        - Any direct text-to-speech scenarios

        :param event: SpeechEvent containing the text to play
        :type event: SpeechEvent
        :param playback_type: Type of playback for logging/tracing (e.g., "greeting", "announcement", "error")
        :type playback_type: str
        :raises asyncio.CancelledError: When playback is cancelled by barge-in
        """
        with tracer.start_as_current_span("route_turn_thread.process_direct_text_playback", kind=SpanKind.CLIENT):
            try:
                playback_type = event.event_type.value
                # Only log significant text or greeting
                if event.event_type == SpeechEventType.GREETING or len(event.text) > 10:
                    logger.info(f"[{self.call_id_short}] Playing {playback_type}: '{event.text[:50]}...'")

                self.current_response_task = asyncio.create_task(
                    send_response_to_acs(
                        ws=self.websocket,
                        text=event.text,
                        blocking=False,
                        latency_tool=getattr(self.websocket.state, "lt", None),
                        stream_mode=StreamMode.MEDIA,
                    )
                )
                await self.current_response_task
            except asyncio.CancelledError:
                logger.info(f"[{self.call_id_short}] {event.event_type.value} playback cancelled")
                raise
            except Exception as e:
                logger.error(f"[{self.call_id_short}] Playback error: {e}")
            finally:
                self.current_response_task = None

    async def cancel_current_processing(self):
        """Cancel current processing for barge-in."""
        try:
            # Clear speech queue
            queue_size = self.speech_queue.qsize()
            while not self.speech_queue.empty():
                try:
                    self.speech_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            
            if queue_size > 2:  # Only log if significant clearing
                logger.info(f"[{self.call_id_short}] Cleared {queue_size} stale events")

            # Cancel current response task
            if self.current_response_task and not self.current_response_task.done():
                self.current_response_task.cancel()
                try:
                    await self.current_response_task
                except asyncio.CancelledError:
                    pass
        except Exception as e:
            logger.error(f"[{self.call_id_short}] Error cancelling processing: {e}")

    async def stop(self):
        """Stop the route turn processing loop."""
        if self._stopped:
            return

        self._stopped = True
        self.running = False
        await self.cancel_current_processing()

        if self.processing_task and not self.processing_task.done():
            self.processing_task.cancel()
            try:
                await self.processing_task
            except asyncio.CancelledError:
                pass


class MainEventLoop:
    """
    🌐 Main Event Loop Manager

    Handles WebSocket operations, task cancellation, and real-time commands.
    Never blocks to ensure immediate responsiveness for barge-in scenarios.

    Key Characteristics:
    - Never blocks on any operations
    - Immediate task cancellation
    - Real-time WebSocket handling
    - Coordinates with other threads via async patterns
    """
    
    def __init__(self, websocket: WebSocket, call_connection_id: str, route_turn_thread: Optional[RouteTurnThread] = None):
        self.websocket = websocket
        self.call_connection_id = call_connection_id
        self.call_id_short = call_connection_id[-8:] if call_connection_id else "unknown"
        self.route_turn_thread = route_turn_thread
        self.current_playback_task: Optional[asyncio.Task] = None
        self.barge_in_active = threading.Event()
        self.greeting_played = False
        self.active_audio_tasks: Set[asyncio.Task] = set()
        self.max_concurrent_audio_tasks = 50  # Reduced for better performance

    async def handle_barge_in(self):
        """Handle barge-in interruption."""
        with tracer.start_as_current_span("main_event_loop.handle_barge_in", kind=SpanKind.INTERNAL):
            if self.barge_in_active.is_set():
                return  # Already handling barge-in

            self.barge_in_active.set()

            try:
                # Cancel current playback
                await self._cancel_current_playback()

                # Cancel Route Turn Thread processing
                if self.route_turn_thread:
                    await self.route_turn_thread.cancel_current_processing()

                # Send stop audio command
                await self._send_stop_audio_command()
            except Exception as e:
                logger.error(f"[{self.call_id_short}] Barge-in error: {e}")
            finally:
                asyncio.create_task(self._reset_barge_in_state())

    async def _cancel_current_playback(self):
        """Cancel any current playback task."""
        if self.current_playback_task and not self.current_playback_task.done():
            self.current_playback_task.cancel()
            try:
                await self.current_playback_task
            except asyncio.CancelledError:
                pass

    async def _send_stop_audio_command(self):
        """Send stop audio command to ACS."""
        try:
            stop_audio_data = {"Kind": "StopAudio", "AudioData": None, "StopAudio": {}}
            await self.websocket.send_text(json.dumps(stop_audio_data))
        except Exception as e:
            logger.error(f"[{self.call_id_short}] Failed to send stop audio: {e}")

    async def _reset_barge_in_state(self):
        """Reset barge-in state after brief delay."""
        await asyncio.sleep(0.1)
        self.barge_in_active.clear()

    async def handle_media_message(self, stream_data: str, recognizer, acs_handler):
        """Handle incoming media messages."""
        try:
            data = json.loads(stream_data)
            kind = data.get("kind")

            if kind == "AudioMetadata":
                # Start recognizer on first AudioMetadata
                if acs_handler and acs_handler.speech_sdk_thread:
                    acs_handler.speech_sdk_thread.start_recognizer()

                # Play greeting on first AudioMetadata
                if not self.greeting_played:
                    await self._play_greeting_when_ready(acs_handler)

            elif kind == "AudioData":
                audio_data_section = data.get("audioData", {})
                is_silent = audio_data_section.get("silent", True)

                if not is_silent:
                    audio_bytes = audio_data_section.get("data")
                    if audio_bytes and recognizer:
                        if len(self.active_audio_tasks) < self.max_concurrent_audio_tasks:
                            task = asyncio.create_task(
                                self._process_audio_chunk_async(audio_bytes, recognizer)
                            )
                            self.active_audio_tasks.add(task)
                            task.add_done_callback(lambda t: self.active_audio_tasks.discard(t))

            elif kind == "DtmfData":
                tone = data.get('dtmfData').get('data')
                logger.info(f"[{self.call_id_short}] DTMF tone received: {tone}")
                # DTMF handling is delegated to DTMFValidationLifecycle via event handlers

        except json.JSONDecodeError as e:
            logger.error(f"[{self.call_id_short}] Invalid JSON: {e}")
        except Exception as e:
            logger.error(f"[{self.call_id_short}] Media message error: {e}")

    async def _process_audio_chunk_async(self, audio_bytes: Union[str, bytes], recognizer) -> None:
        """Process audio chunk asynchronously."""
        try:
            # Handle base64 decoding if needed
            if isinstance(audio_bytes, str):
                audio_bytes = base64.b64decode(audio_bytes)

            if recognizer:
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, recognizer.write_bytes, audio_bytes
                    ),
                    timeout=0.03,  # Reduced timeout for better performance
                )
        except asyncio.TimeoutError:
            logger.warning(f"[{self.call_id_short}] Audio processing timeout")
        except Exception as e:
            logger.error(f"[{self.call_id_short}] Audio processing error: {e}")

    async def _play_greeting_when_ready(self, acs_handler=None):
        """Queue greeting for playback."""
        if self.greeting_played or not acs_handler:
            return

        greeting_text = getattr(acs_handler, "greeting_text", None)
        if not greeting_text:
            self.greeting_played = True
            return

        try:
            greeting_event = SpeechEvent(
                event_type=SpeechEventType.GREETING,
                text=greeting_text,
                language="en-US",
            )
            acs_handler.thread_bridge.queue_speech_result(
                acs_handler.speech_queue, greeting_event
            )
            self.greeting_played = True
            logger.info(f"[{self.call_id_short}] Greeting queued")
        except Exception as e:
            logger.error(f"[{self.call_id_short}] Failed to queue greeting: {e}")
            self.greeting_played = True

# ============================================================================
# 🎯 MAIN ORCHESTRATOR - THREE-THREAD ARCHITECTURE COORDINATOR
# ============================================================================
class ACSMediaHandler:
    """
    🎯 V1 ACS Media Handler - Three-Thread Architecture Implementation

    Coordinates the documented three-thread architecture for low-latency voice interactions:

    🧵 Speech SDK Thread: Isolated audio recognition, never blocks
    🧵 Route Turn Thread: AI processing queue, blocks only on queue operations
    🧵 Main Event Loop: WebSocket & task management, never blocks

    This design ensures sub-50ms barge-in response time while maintaining
    clean separation of concerns and thread-safe communication patterns.
    """

    def __init__(
        self,
        websocket: WebSocket,
        orchestrator_func: Callable,
        call_connection_id: str,
        recognizer: Optional[StreamingSpeechRecognizerFromBytes] = None,
        memory_manager: Optional[MemoManager] = None,
        session_id: Optional[str] = None,
        greeting_text: str = GREETING,
    ):
        """
        Initialize the three-thread architecture media handler.

        :param websocket: WebSocket connection for media streaming
        :type websocket: WebSocket
        :param orchestrator_func: Orchestrator function for conversation management
        :type orchestrator_func: Callable
        :param call_connection_id: ACS call connection identifier
        :type call_connection_id: str
        :param recognizer: Speech recognition client instance
        :type recognizer: Optional[StreamingSpeechRecognizerFromBytes]
        :param memory_manager: Memory manager for conversation state
        :type memory_manager: Optional[MemoManager]
        :param session_id: Session identifier
        :type session_id: Optional[str]
        :param greeting_text: Text for greeting playback
        :type greeting_text: str
        """
        self.websocket = websocket
        self.orchestrator_func = orchestrator_func
        self.call_connection_id = call_connection_id
        self.session_id = session_id or call_connection_id
        self.memory_manager = memory_manager
        self.greeting_text = greeting_text
        self.call_id_short = call_connection_id[-8:] if call_connection_id else "unknown"

        # Initialize speech recognizer
        self.recognizer = recognizer or StreamingSpeechRecognizerFromBytes(
            candidate_languages=["en-US", "fr-FR", "de-DE", "es-ES", "it-IT"],
            vad_silence_timeout_ms=800,
            audio_format="pcm",
        )

        # Cross-thread communication
        self.speech_queue = asyncio.Queue(maxsize=10)
        self.thread_bridge = ThreadBridge()

        # Initialize threads
        self.route_turn_thread = RouteTurnThread(
            speech_queue=self.speech_queue,
            orchestrator_func=orchestrator_func,
            memory_manager=memory_manager,
            websocket=websocket,
        )

        self.main_event_loop = MainEventLoop(
            websocket, call_connection_id, self.route_turn_thread
        )

        self.speech_sdk_thread = SpeechSDKThread(
            recognizer=self.recognizer,
            thread_bridge=self.thread_bridge,
            barge_in_handler=self.main_event_loop.handle_barge_in,
            speech_queue=self.speech_queue,
        )

        # Lifecycle management
        self.running = False
        self._stopped = False

    async def start(self):
        """Start all three threads."""
        with tracer.start_as_current_span(
            "acs_media_handler.start",
            kind=SpanKind.INTERNAL,
            attributes={"call.connection.id": self.call_connection_id},
        ):
            try:
                logger.info(f"[{self.call_id_short}] Starting three-thread media handler")
                
                # Thread-safe registry registration
                with _handlers_lock:
                    existing = _active_handlers.get(self.call_connection_id)
                    if existing and existing.is_running:
                        logger.warning(f"[{self.call_id_short}] Stopping existing handler")
                        await existing.stop()
                    _active_handlers[self.call_connection_id] = self
                
                self.running = True

                # Capture main event loop
                main_loop = asyncio.get_running_loop()
                self.thread_bridge.set_main_loop(main_loop, self.call_connection_id)

                # Store reference for greeting access
                self.websocket._acs_media_handler = self

                # Start threads
                self.speech_sdk_thread.prepare_thread()
                await self.route_turn_thread.start()

                logger.info(f"[{self.call_id_short}] Media handler started")
            except Exception as e:
                logger.error(f"[{self.call_id_short}] Failed to start: {e}")
                await self.stop()
                raise

    async def handle_media_message(self, stream_data: str):
        """
        Handle incoming media messages (Main Event Loop responsibility).

        :param stream_data: JSON string containing media message data
        :type stream_data: str
        """
        try:
            await self.main_event_loop.handle_media_message(stream_data, self.recognizer, self)
        except Exception as e:
            logger.error(f"[{self.call_id_short}] Media message error: {e}")

    async def stop(self):
        """Stop all threads."""
        if self._stopped:
            return

        with tracer.start_as_current_span("acs_media_handler.stop", kind=SpanKind.INTERNAL):
            try:
                logger.info(f"[{self.call_id_short}] Stopping media handler")
                self._stopped = True
                self.running = False

                # Thread-safe registry cleanup
                with _handlers_lock:
                    _active_handlers.pop(self.call_connection_id, None)

                await self.route_turn_thread.stop()
                self.speech_sdk_thread.stop()
                await self.main_event_loop._cancel_current_playback()

                logger.info(f"[{self.call_id_short}] Media handler stopped")
            except Exception as e:
                logger.error(f"[{self.call_id_short}] Stop error: {e}")

    @property
    def is_running(self) -> bool:
        """
        Check if the handler is currently running.

        :return: True if handler is running, False otherwise
        :rtype: bool
        """
        return self.running

    def queue_direct_text_playback(
        self,
        text: str,
        playback_type: SpeechEventType = SpeechEventType.ANNOUNCEMENT,
        language: str = "en-US",
    ) -> bool:
        """
        Queue direct text for playback through the Route Turn Thread.

        This is a convenience method for external code to send text directly to ACS
        for TTS playback without going through the orchestrator.

        :param text: Text to be played back
        :type text: str
        :param playback_type: Type of playback event (GREETING, ANNOUNCEMENT, STATUS_UPDATE, ERROR_MESSAGE)
        :type playback_type: SpeechEventType
        :param language: Language for TTS (default: en-US)
        :type language: str
        :return: True if successfully queued, False otherwise
        :rtype: bool
        """
        if not self.running:
            return False

        valid_types = {
            SpeechEventType.GREETING,
            SpeechEventType.ANNOUNCEMENT,
            SpeechEventType.STATUS_UPDATE,
            SpeechEventType.ERROR_MESSAGE,
        }

        if playback_type not in valid_types:
            logger.error(f"[{self.call_id_short}] Invalid playback type: {playback_type}")
            return False

        try:
            text_event = SpeechEvent(event_type=playback_type, text=text, language=language)
            self.thread_bridge.queue_speech_result(self.speech_queue, text_event)
            return True
        except Exception as e:
            logger.error(f"[{self.call_id_short}] Failed to queue text: {e}")
            return False


# Simple utility functions for debugging
def get_active_handlers_count() -> int:
    """Get count of active handlers."""
    with _handlers_lock:
        return len(_active_handlers)


def get_active_handlers_debug() -> dict:
    """Get debug info for active handlers."""
    with _handlers_lock:
        return {
            "count": len(_active_handlers),
            "call_ids": [call_id[-8:] for call_id in _active_handlers.keys()]
        }