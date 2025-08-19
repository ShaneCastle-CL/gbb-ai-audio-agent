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
import os
import asyncio
import json
import threading
import base64
import time

from dataclasses import dataclass, field
from typing import Optional, Callable, Protocol, Union, Set
from enum import Enum

from azure.communication.callautomation import TextSource
from fastapi import WebSocket
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from apps.rtagent.backend.settings import GREETING, ACS_STREAMING_MODE, DTMF_VALIDATION_ENABLED
from apps.rtagent.backend.src.shared_ws import send_response_to_acs
from apps.rtagent.backend.src.orchestration.orchestrator import route_turn
from src.enums.stream_modes import StreamMode
from src.speech.speech_recognizer import StreamingSpeechRecognizerFromBytes
from src.stateful.state_managment import MemoManager
from utils.ml_logging import get_logger

# Import AWS Connect validation methods (deferred to avoid circular imports)
from apps.rtagent.backend.api.v1.events.types import CallEventContext

# Import DTMF validation lifecycle for centralized validation logic
from apps.rtagent.backend.api.v1.handlers.dtmf_validation_lifecycle import DTMFValidationLifecycle

logger = get_logger("v1.handlers.acs_media_lifecycle")
tracer = trace.get_tracer(__name__)


# ============================================================================
# 🔄 SPEECH EVENT DATA TYPES
# ============================================================================


class SpeechEventType(Enum):
    """Types of speech recognition events."""

    PARTIAL = "partial"  # Barge-in trigger
    FINAL = "final"  # Complete speech for AI processing
    ERROR = "error"  # Recognition error
    GREETING = "greeting"  # Greeting playback request
    ANNOUNCEMENT = "announcement"  # System announcement
    STATUS_UPDATE = "status"  # Status message
    ERROR_MESSAGE = "error_msg"  # Error message playback


@dataclass
class SpeechEvent:
    """Speech recognition event with metadata."""

    event_type: SpeechEventType
    text: str
    language: Optional[str] = None
    speaker_id: Optional[str] = None
    confidence: Optional[float] = None
    timestamp: Optional[float] = field(
        default_factory=time.time
    )  # Use time.time() instead of asyncio loop time


# ============================================================================
# 🔗 CROSS-THREAD COMMUNICATION INFRASTRUCTURE
# ============================================================================
# ============================================================================
# 🗂️ Instrumented Speech Queue for Backpressure
# ============================================================================
import collections
class BoundedSpeechQueue(asyncio.Queue):
    """Instrumented bounded queue for speech events with drop-oldest on overflow."""
    def __init__(self, maxsize: int = 10, *args, **kwargs):
        super().__init__(maxsize=maxsize, *args, **kwargs)
        self.high_watermark = 0
        self.dropped_events = 0
    
    def _update_hwm(self):
        qsz = self.qsize()
        if qsz > self.high_watermark:
            self.high_watermark = qsz

    def put_nowait_or_drop_oldest(self, item: "SpeechEvent"):
        try:
            super().put_nowait(item)
            self._update_hwm()
            return True
        except asyncio.QueueFull:
            try:
                # Drop the oldest item to make room for the newest
                _ = self.get_nowait()
                self.dropped_events += 1
                super().put_nowait(item)
                self._update_hwm()
                return True
            except Exception:
                return False

class ThreadBridge:
    """
    🔗 Cross-Thread Communication Bridge

    Provides thread-safe communication between Speech SDK Thread and Main Event Loop.
    Implements the non-blocking patterns described in the documentation.
    """

    def __init__(self, main_loop: Optional[asyncio.AbstractEventLoop] = None):
        # Store the main event loop for use in cross-thread communication
        self.main_loop = main_loop

    def set_main_loop(self, loop: asyncio.AbstractEventLoop):
        """
        Set the main event loop reference for cross-thread communication.

        :param loop: Main event loop instance
        :type loop: asyncio.AbstractEventLoop
        """
        self.main_loop = loop
        logger.debug(self._call_log(f"🔗 ThreadBridge main loop set: {id(loop)}"))

    def schedule_barge_in(self, handler_func: Callable):
        """Schedule barge-in handler to run on main event loop ASAP."""
        logger.info(f"🚨 SCHEDULE_BARGE_IN CALLED - Starting barge-in scheduling...")
        logger.debug(f"🔍 Handler function: {handler_func}")
        logger.debug(
            f"🔍 Main loop state: {self.main_loop is not None and not self.main_loop.is_closed()}"
        )

        if not self.main_loop or self.main_loop.is_closed():
            logger.warning(self._call_log("⚠️ No main event loop available for barge-in scheduling"))
            return

        try:
            future = asyncio.run_coroutine_threadsafe(handler_func(), self.main_loop)
            logger.debug(self._call_log("🚀 Barge-in scheduled successfully"))
            # Don't wait for completion - this should be fire-and-forget for speed
        except RuntimeError as e:
            if "no running event loop" in str(e).lower():
                logger.warning(self._call_log(f"⚠️ No running event loop for barge-in scheduling: {e}"))
            else:
                logger.error(self._call_log(f"❌ RuntimeError scheduling barge-in: {e}"))
        except Exception as e:
            logger.error(self._call_log(f"❌ Failed to schedule barge-in: {type(e).__name__}: {e}"))
            import traceback
            logger.error(self._call_log(f"❌ Schedule barge-in error traceback: {traceback.format_exc()}"))

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
            # Prefer fast-path; if it's our instrumented queue, use drop-oldest
            if hasattr(speech_queue, "put_nowait_or_drop_oldest"):
                ok = speech_queue.put_nowait_or_drop_oldest(event)
                if not ok:
                    logger.warning(self._call_log("⚠️ Failed to enqueue even after drop-oldest; will fallback to threadsafe put"))
                else:
                    logger.debug(self._call_log(f"📋 Speech event queued: {event.event_type.value} (queue: {speech_queue.qsize()}, hwm={getattr(speech_queue,'high_watermark',-1)}, dropped={getattr(speech_queue,'dropped_events',-1)})"))
                    return
            else:
                speech_queue.put_nowait(event)
                logger.debug(self._call_log(f"📋 Speech event queued: {event.event_type.value} (queue: {speech_queue.qsize()})"))
                return
        except asyncio.QueueFull:
            logger.warning(self._call_log(f"⚠️ Speech queue full ({speech_queue.qsize()}), using threadsafe fallback"))
        except Exception as e:
            logger.debug(self._call_log(f"put_nowait failed: {type(e).__name__}: {e}, using threadsafe fallback"))

        # Fallback to run_coroutine_threadsafe if put_nowait fails
        if not self.main_loop or self.main_loop.is_closed():
            logger.error(self._call_log("❌ No main event loop available for speech result queuing"))
            raise RuntimeError("Unable to queue speech event: no main event loop available")

        try:
            # Use stored main event loop reference
            future = asyncio.run_coroutine_threadsafe(
                speech_queue.put(event), self.main_loop
            )
            logger.debug(self._call_log(f"📋 Speech event queued via threadsafe: {event.event_type.value}"))
            future.result(timeout=0.1)
        except Exception as e:
            logger.error(self._call_log(f"❌ Failed to queue speech result: {type(e).__name__}: {e}"))
            logger.error(self._call_log(f"❌ Event: {event.event_type.value}, Queue size: {speech_queue.qsize()}"))
            raise RuntimeError(f"Unable to queue speech event: {e}") from e


# ============================================================================
# 🧵 THREAD MANAGERS - INDIVIDUAL THREAD RESPONSIBILITIES
# ============================================================================


class SpeechSDKThread:
    """
    🎤 Speech SDK Thread Manager

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
        speech_queue: asyncio.Queue,
    ):
        """
        Initialize Speech SDK Thread Manager.

        :param recognizer: Speech recognition client instance
        :type recognizer: StreamingSpeechRecognizerFromBytes
        :param thread_bridge: Cross-thread communication bridge
        :type thread_bridge: ThreadBridge
        :param barge_in_handler: Handler function for barge-in events
        :type barge_in_handler: Callable
        :param speech_queue: Queue for speech events
        :type speech_queue: asyncio.Queue
        """
        self.recognizer = recognizer
        self.thread_bridge = thread_bridge
        self.barge_in_handler = barge_in_handler
        self.speech_queue = speech_queue
        self.call_connection_id = call_connection_id
        self.thread_obj: Optional[threading.Thread] = None
        self.thread_running = False  # Thread lifecycle
        self.recognizer_started = False  # Recognizer state
        self.stop_event = threading.Event()  # Proper shutdown signal
        self._stopped = False  # Prevent multiple stop calls

        self._setup_callbacks()

    def _call_log(self, message: str) -> str:
        """Format log message with call connection ID prefix."""
        call_short = self.call_connection_id[-8:] if len(self.call_connection_id) > 8 else self.call_connection_id
        return f"[SpeechLoop call-{call_short}] {message}"

    def _setup_callbacks(self):
        """
        Configure speech recognition callbacks for immediate response.

        :raises Exception: When callback setup fails
        """

        def on_partial(text: str, lang: str, speaker_id: Optional[str] = None):
            """🚨 IMMEDIATE: Trigger barge-in detection"""
            logger.debug(self._call_log(f"🗣️ Partial speech: '{text[:25]}...' ({len(text)} chars, {lang})"))

            # Immediate barge-in trigger - no blocking operations
            try:
                self.thread_bridge.schedule_barge_in(self.barge_in_handler)
                logger.debug(self._call_log("🚨 Barge-in scheduled"))
            except Exception as e:
                logger.error(self._call_log(f"❌ Barge-in scheduling failed: {e}"))
                import traceback
                logger.error(self._call_log(f"❌ Barge-in error traceback: {traceback.format_exc()}"))

        def on_final(text: str, lang: str, speaker_id: Optional[str] = None):
            """📋 QUEUED: Send to Route Turn Thread for AI processing."""
            logger.info(f"✅ Final speech ready for processing: '{text[:40]}...' ({lang})")

            # Queue for AI processing - non-blocking
            event = SpeechEvent(
                event_type=SpeechEventType.FINAL,
                text=text,
                language=lang,
                speaker_id=speaker_id,
            )
            self.thread_bridge.queue_speech_result(self.speech_queue, event)

        def on_error(error: str):
            """❌ ERROR: Log and queue error event."""
            logger.error(f"Speech recognition error: {error}")
            logger.debug(f"🔍 Callback triggered - on_error: {error}")

            error_event = SpeechEvent(event_type=SpeechEventType.ERROR, text=error)
            self.thread_bridge.queue_speech_result(self.speech_queue, error_event)

        # Assign callbacks to recognizer using proper methods
        logger.info(self._call_log("🔧 Setting up speech recognition callbacks"))

        try:
            logger.debug(self._call_log("🔧 Setting partial result callback"))
            self.recognizer.set_partial_result_callback(on_partial)

            logger.debug(self._call_log("🔧 Setting final result callback"))
            self.recognizer.set_final_result_callback(on_final)

            logger.debug(self._call_log("🔧 Setting cancel callback"))
            self.recognizer.set_cancel_callback(on_error)

            logger.debug(self._call_log("✅ Speech recognition callbacks configured"))

            # Store test callback reference for manual testing
            self._test_partial_callback = on_partial

        except Exception as e:
            logger.error(self._call_log(f"❌ Failed to set speech recognition callbacks: {e}"))
            import traceback
            logger.error(self._call_log(f"❌ Callback setup error traceback: {traceback.format_exc()}"))
            raise

    def prepare_thread(self):
        """
        Prepare the speech recognition thread but don't start recognizer yet.
        """
        if self.thread_running:
            logger.warning("Speech SDK thread already prepared")
            return

        def recognition_thread():
            """Background thread ready for speech recognition."""
            try:
                logger.info("🧵 Speech SDK thread prepared and waiting")
                self.thread_running = True

                # Thread runs but recognizer waits for explicit start
                # Use proper Event.wait() instead of creating new events
                while self.thread_running and not self.stop_event.is_set():
                    self.stop_event.wait(0.1)  # Wait for stop signal or timeout

                logger.info("🧵 Speech SDK thread completed")
            except Exception as e:
                logger.error(f"Speech SDK thread error: {e}")
            finally:
                self.thread_running = False

        self.thread_obj = threading.Thread(target=recognition_thread, daemon=True)
        self.thread_obj.start()
        logger.info(self._call_log("Speech SDK thread prepared (recognizer not started yet)"))

    def start_recognizer(self):
        """
        Start the actual speech recognizer (called on AudioMetadata).

        :raises Exception: When recognizer startup fails
        """
        if self.recognizer_started:
            logger.debug(self._call_log("Speech recognizer already started"))
            return

        if not self.thread_running:
            logger.error(self._call_log("Cannot start recognizer: thread not prepared"))
            return

        try:
            logger.info(self._call_log("🎤 Starting speech recognizer on AudioMetadata ready state"))

            # Start recognizer with safeguards against blocking
            logger.debug(self._call_log("🚀 Starting speech recognizer"))
            start_time = time.time()

            self.recognizer.start()

            start_duration = time.time() - start_time
            logger.info(self._call_log(f"✅ Speech recognizer started ({start_duration:.3f}s)"))

            self.recognizer_started = True

            # Send a small test audio chunk to verify recognizer is processing
            logger.debug(self._call_log("🧪 Testing recognizer with silence audio"))
            test_audio = b"\x00" * 320  # 20ms of silence at 16kHz mono

            try:
                test_start = time.time()
                self.recognizer.write_bytes(test_audio)
                test_duration = time.time() - test_start
                logger.debug(
                    f"🧪 Test audio sent successfully ({len(test_audio)} bytes in {test_duration:.3f}s)"
                )
            except Exception as test_e:
                logger.error(f"❌ Test audio failed: {test_e}")
                # Continue anyway - test failure doesn't mean recognizer is broken

            # # Add a manual barge-in test after 5 seconds to verify the mechanism
            # logger.info("🧪 Scheduling manual barge-in test in 5 seconds...")
            # asyncio.run_coroutine_threadsafe(
            #     self._test_barge_in_after_delay(), asyncio.get_event_loop()
            # )

            # # Also test partial callback triggering after 3 seconds
            # logger.info("🧪 Scheduling manual partial callback test in 3 seconds...")
            # asyncio.run_coroutine_threadsafe(
            #     self._test_partial_callback_after_delay(), asyncio.get_event_loop()
            # )

        except Exception as e:
            logger.error(f"❌ Failed to start speech recognizer: {e}")
            logger.error(f"❌ Exception type: {type(e).__name__}")
            import traceback

            logger.error(f"❌ Full traceback: {traceback.format_exc()}")
            raise

    async def _test_barge_in_after_delay(self):
        """
        Test method to manually trigger barge-in for verification.
        """
        await asyncio.sleep(5.0)
        logger.info("🧪 MANUAL BARGE-IN TEST: Triggering test barge-in...")
        try:
            await self.barge_in_handler()
            logger.info("✅ Manual barge-in test completed successfully")
        except Exception as e:
            logger.error(f"❌ Manual barge-in test failed: {e}")
            import traceback

            logger.error(f"❌ Manual barge-in test traceback: {traceback.format_exc()}")

    async def _test_partial_callback_after_delay(self):
        """
        Test method to manually trigger partial callback for verification.
        """
        await asyncio.sleep(3.0)
        logger.info(
            "🧪 MANUAL PARTIAL CALLBACK TEST: Simulating partial speech detection..."
        )
        try:
            # Find the on_partial callback and trigger it manually
            if hasattr(self, "_test_partial_callback"):
                self._test_partial_callback("hello world", "en-US")
                logger.info("✅ Manual partial callback test completed successfully")
            else:
                logger.warning("⚠️ Could not find partial callback for testing")
        except Exception as e:
            logger.error(f"❌ Manual partial callback test failed: {e}")
            import traceback

            logger.error(
                f"❌ Manual partial callback test traceback: {traceback.format_exc()}"
            )

    def stop(self):
        """
        Stop speech recognition and thread.
        """
        if self._stopped:
            logger.debug("Speech SDK thread already stopped or stopping")
            return

        logger.info("Stopping Speech SDK thread")
        self._stopped = True
        self.thread_running = False
        self.recognizer_started = False

        # Signal thread to stop
        self.stop_event.set()

        if self.recognizer:
            try:
                self.recognizer.stop()
            except Exception as e:
                logger.error(f"Error stopping recognizer: {e}")

        if self.thread_obj and self.thread_obj.is_alive():
            self.thread_obj.join(timeout=2.0)
            if self.thread_obj.is_alive():
                logger.warning("Speech SDK thread did not stop gracefully")


class RouteTurnThread:
    """
    🔄 Route Turn Thread Manager

    Dedicated thread for AI processing and response generation. Can safely block
    on queue operations without affecting speech recognition or WebSocket handling.

    Key Characteristics:
    - Blocks only on queue.get() operations
    - Serializes conversation turns via queue
    - Delegates to orchestrator for response generation
    - Isolated from real-time operations
    """

    def __init__(
        self,
        call_connection_id: str,
        speech_queue: asyncio.Queue,
        orchestrator_func: Callable,
        memory_manager: Optional[MemoManager],
        websocket: WebSocket,
    ):
        """
        Initialize Route Turn Thread Manager.

        :param speech_queue: Queue for incoming speech events
        :type speech_queue: asyncio.Queue
        :param orchestrator_func: Function for conversation orchestration
        :type orchestrator_func: Callable
        :param memory_manager: Memory manager for conversation state
        :type memory_manager: Optional[MemoManager]
        :param websocket: WebSocket connection for communication
        :type websocket: WebSocket
        """
        self.call_connection_id = call_connection_id
        self.speech_queue = speech_queue
        self.orchestrator_func = orchestrator_func
        self.memory_manager = memory_manager
        self.websocket = websocket
        self.processing_task: Optional[asyncio.Task] = None
        self.current_response_task: Optional[
            asyncio.Task
        ] = None  # Track current TTS/playback task
        self.running = False
        self._stopped = False  # Prevent multiple stop calls

    def _call_log(self, message: str) -> str:
        """Format log message with call connection ID prefix."""
        call_short = self.call_connection_id[-8:] if len(self.call_connection_id) > 8 else self.call_connection_id
        return f"[RTThread call-{call_short}] {message}"

    async def start(self):
        """
        Start the route turn processing loop.
        """
        if self.running:
            logger.warning(self._call_log("Route turn thread already running"))
            return

        logger.info(self._call_log("🧵 Starting Route Turn thread"))
        self.running = True
        self.processing_task = asyncio.create_task(self._processing_loop())
        logger.info(self._call_log("🧵 Route Turn thread processing task created successfully"))

    async def _processing_loop(self):
        """
        Main processing loop - blocks only on queue.get().

        This is the ONLY thread that can safely block, as it's isolated
        from real-time speech recognition and WebSocket handling.
        """
        with tracer.start_as_current_span(
            "v1.route_turn_thread.processing_loop", kind=SpanKind.INTERNAL
        ):
            logger.info(self._call_log("Route Turn processing loop started"))

            while self.running:
                try:
                    # 🎯 BLOCKING OPERATION: Wait for speech events
                    # This is safe because this thread is isolated from real-time operations
                    logger.debug(
                        self._call_log(f"🔄 Route Turn Thread waiting for events (queue size: {self.speech_queue.qsize()})")
                    )
                    speech_event = await asyncio.wait_for(
                        self.speech_queue.get(),
                        timeout=1.0,  # Periodic check for shutdown
                    )

                    logger.info(
                        self._call_log(f"📢 Route Turn Thread received event: {speech_event.event_type.value} - '{speech_event.text}'")
                    )

                    try:
                        if speech_event.event_type == SpeechEventType.FINAL:
                            await self._process_final_speech(speech_event)
                        elif speech_event.event_type == SpeechEventType.GREETING:
                            await self._process_direct_text_playback(
                                speech_event, "greeting"
                            )
                        elif speech_event.event_type == SpeechEventType.ANNOUNCEMENT:
                            await self._process_direct_text_playback(
                                speech_event, "announcement"
                            )
                        elif speech_event.event_type == SpeechEventType.STATUS_UPDATE:
                            await self._process_direct_text_playback(
                                speech_event, "status_update"
                            )
                        elif speech_event.event_type == SpeechEventType.ERROR_MESSAGE:
                            await self._process_direct_text_playback(
                                speech_event, "error_message"
                            )
                        elif speech_event.event_type == SpeechEventType.ERROR:
                            logger.error(
                                f"Speech error in processing: {speech_event.text}"
                            )
                    except asyncio.CancelledError:
                        # Individual response task was cancelled (barge-in), but continue processing loop
                        logger.info(
                            f"🛑 {speech_event.event_type.value} processing cancelled (barge-in), continuing loop"
                        )
                        continue

                except asyncio.TimeoutError:
                    # Normal timeout - check for shutdown (no logging to avoid spam)
                    continue
                except Exception as e:
                    logger.error(self._call_log(f"Error in Route Turn processing loop: {e}"))
                    # Exit loop on unexpected errors to prevent infinite error logging
                    break

    async def _process_final_speech(self, event: SpeechEvent):
        """
        Process final speech through orchestrator.

        :param event: Final speech event to process
        :type event: SpeechEvent
        """
        with tracer.start_as_current_span(
            "v1.route_turn_thread.process_speech",
            kind=SpanKind.CLIENT,
            attributes={
                "speech.text": event.text,
                "speech.language": event.language,
            },
        ) as span:
            try:
                logger.info(self._call_log(f"🤖 Processing speech through orchestrator: {event.text}"))

                # Check if memory manager is available
                if not self.memory_manager:
                    logger.error(
                        self._call_log("❌ Memory manager is None, cannot process speech event")
                    )
                    return
                
                # Delegate to orchestrator using the new simplified signature
                if self.orchestrator_func:
                    await self.orchestrator_func(
                        cm=self.memory_manager,
                        transcript=event.text,
                        ws=self.websocket,
                        call_id=getattr(self.websocket, "_call_connection_id", None),
                        is_acs=True,
                    )
                    logger.info(self._call_log(f"✅ Orchestrator completed successfully"))
                else:
                    logger.warning(
                        self._call_log("⚠️ No orchestrator function provided, using fallback")
                    )
                    # Fallback to direct route_turn call
                    await route_turn(
                        cm=self.memory_manager,
                        transcript=event.text,
                        ws=self.websocket,
                        is_acs=True,
                    )

            except Exception as e:
                if hasattr(span, "set_status"):
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.error(self._call_log(f"Error processing speech: {e}"))

    async def _process_direct_text_playback(
        self, event: SpeechEvent, playback_type: str = "audio"
    ):
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
        with tracer.start_as_current_span(
            "v1.route_turn_thread.process_direct_text_playback",
            kind=SpanKind.CLIENT,
            attributes={
                "playback.text": event.text,
                "playback.type": playback_type,
                "playback.language": event.language,
            },
        ) as span:
            try:
                logger.info(
                    self._call_log(f"🎵 Playing {playback_type} through Route Turn Thread: {event.text}")
                )

                # Get latency tool if available
                latency_tool = getattr(self.websocket.state, "lt", None)

                # Create cancellable task for TTS/playback
                self.current_response_task = asyncio.create_task(
                    send_response_to_acs(
                        ws=self.websocket,
                        text=event.text,
                        blocking=False,
                        latency_tool=latency_tool,
                        stream_mode=StreamMode.MEDIA,
                        voice_name=None,  # Use default voice
                        voice_style=None,  # Use default style
                    )
                )

                # Wait for completion (can be cancelled by barge-in)
                await self.current_response_task

                span.set_status(Status(StatusCode.OK))
                logger.info(
                    f"✅ {playback_type.capitalize()} playback completed successfully"
                )

            except asyncio.CancelledError:
                if hasattr(span, "set_status"):
                    span.set_status(
                        Status(StatusCode.OK)
                    )  # Cancellation is normal behavior
                logger.info(
                    f"🛑 {playback_type.capitalize()} playback cancelled (barge-in)"
                )
                raise  # Re-raise to complete cancellation
            except Exception as e:
                if hasattr(span, "set_status"):
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.error(f"❌ Error playing {playback_type}: {e}")
            finally:
                # Clear the current response task reference
                self.current_response_task = None

    async def cancel_current_processing(self):
        """
        Cancel current Route Turn processing (for barge-in interruption).
        """
        try:
            # Clear the speech queue to prevent stale events
            queue_size = self.speech_queue.qsize()
            while not self.speech_queue.empty():
                try:
                    self.speech_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            if queue_size > 0:
                logger.info(f"🧹 Cleared {queue_size} stale events from speech queue")

            # Cancel any current response task (TTS/playback)
            if self.current_response_task and not self.current_response_task.done():
                self.current_response_task.cancel()
                try:
                    await self.current_response_task
                except asyncio.CancelledError:
                    logger.debug("Response task cancelled for barge-in")

            # NOTE: We don't cancel the main processing_task here because we want it to continue
            # running and processing new speech events after barge-in
            logger.debug("✅ Route Turn ready for new events after barge-in")

        except Exception as e:
            logger.error(f"Error cancelling Route Turn processing: {e}")

    async def stop(self):
        """
        Stop the route turn processing loop.
        """
        if self._stopped:
            logger.debug(self._call_log("Route Turn thread already stopped or stopping"))
            return

        logger.info(self._call_log("Stopping Route Turn thread"))
        self._stopped = True
        self.running = False

        # Cancel current processing if needed
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
    - Immediate task cancellation (< 1ms)
    - Real-time WebSocket handling
    - Coordinates with other threads via async patterns
    """

    def __init__(
        self,
        websocket: WebSocket,
        call_connection_id: str,
        route_turn_thread: Optional["RouteTurnThread"] = None,
    ):
        """
        Initialize Main Event Loop Manager.

        :param websocket: WebSocket connection for communication
        :type websocket: WebSocket
        :param call_connection_id: ACS call connection identifier
        :type call_connection_id: str
        :param route_turn_thread: Reference for barge-in cancellation
        :type route_turn_thread: Optional[RouteTurnThread]
        """
        self.websocket = websocket
        self.call_connection_id = call_connection_id
        self.route_turn_thread = (
            route_turn_thread  # Reference for barge-in cancellation
        )
        self.memory_manager = memory_manager
        self.redis_mgr = websocket.app.state.redis
        self.current_playback_task: Optional[asyncio.Task] = None
        self.barge_in_active = threading.Event()
        self.greeting_played = False

        # Audio processing task tracking
        self.active_audio_tasks: Set[asyncio.Task] = set()
        self.last_queue_health_log = 0  # Track queue health logging

        # 🔄 Memory Manager Refresh Tracking for Parallel Stream Updates
        self.last_memory_refresh_time = 0
        self.memory_refresh_count = 0
        self.call_start_time = time.time()
        self.pending_refresh_task: Optional[asyncio.Task] = None

        # 🎯 Improved Audio Processing Configuration
        self.active_audio_tasks: Set[asyncio.Task] = set()
        self.max_concurrent_audio_tasks = int(os.getenv("MAX_CONCURRENT_AUDIO_TASKS", "50"))  # Reduced from 500
        self.max_emergency_audio_tasks = int(os.getenv("MAX_EMERGENCY_AUDIO_TASKS", "100"))   # Emergency threshold
        
        # Audio backpressure and quality management
        self.audio_processing_semaphore = asyncio.Semaphore(self.max_concurrent_audio_tasks)
        self.dropped_frames_count = 0
        self.processed_frames_count = 0
        self.last_performance_log = 0
        
        # Audio frame buffering for overflow situations
        self.audio_overflow_buffer: asyncio.Queue = asyncio.Queue(maxsize=20)  # Short-term buffer
        self.buffer_processing_task: Optional[asyncio.Task] = None
        
        # Validation gate: pre-warm recognizer but only feed audio when open
        self._validation_gate_open = False
        self._validation_task: Optional[asyncio.Task] = None
        self._validation_log_once = False


    def _call_log(self, message: str) -> str:
        """Format log message with call connection ID prefix."""
        call_short = self.call_connection_id[-8:] if len(self.call_connection_id) > 8 else self.call_connection_id
        return f"[MainLoop call-{call_short}] {message}"

    async def _perform_memory_refresh(self, redis_mgr) -> None:
        """
        Perform the actual memory refresh operation.
        
        This runs in a separate task to avoid blocking audio processing.
        """
        try:
            refresh_start = time.time()
            
            # Refresh memory manager from Redis
            success = await self.memory_manager.refresh_from_redis_async(redis_mgr)
            
            refresh_duration = time.time() - refresh_start
            self.last_memory_refresh_time = time.time()
            self.memory_refresh_count += 1
            
            if success:
                logger.debug(self._call_log(f"Memory refresh completed in {refresh_duration:.3f}s (refresh #{self.memory_refresh_count})"))
                
                # Log significant state changes for debugging
                dtmf_validated = self.memory_manager.get_context("dtmf_validated", False)
                logger.debug(self._call_log(f"Post-refresh state: dtmf_validated={dtmf_validated}"))
            else:
                logger.debug(self._call_log(f"Memory refresh found no new data (refresh #{self.memory_refresh_count})"))
                
        except Exception as e:
            logger.warning(self._call_log(f"Error during memory refresh: {e}"))
        finally:
            # Clear the pending task reference
            self.pending_refresh_task = None

    async def _await_validation_and_open_gate(self):
        """Wait once for DTMF validation, then open the audio gate and (optionally) play greeting."""
        try:
            validation_completed = await DTMFValidationLifecycle.wait_for_dtmf_validation_completion(
                redis_mgr=self.redis_mgr,
                call_connection_id=self.call_connection_id,
                timeout_ms=30_000
            )
            if validation_completed:
                logger.info(self._call_log("✅ DTMF validation complete — opening audio gate"))
                self._validation_gate_open = True
                if not self.greeting_played:
                    await self._play_greeting(getattr(self.websocket, "_acs_media_handler", None))
            else:
                logger.warning(self._call_log("⏰ DTMF validation timeout — audio gate remains closed"))
        except Exception as e:
            logger.error(self._call_log(f"❌ Error awaiting validation: {e}"))

    async def handle_barge_in(self):
        """
        Handle barge-in interruption with immediate response.
        """
        with tracer.start_as_current_span(
            "v1.main_event_loop.handle_barge_in", kind=SpanKind.INTERNAL
        ):
            logger.info(self._call_log("🚨 BARGE-IN HANDLER CALLED - Starting barge-in processing..."))

            if self.barge_in_active.is_set():
                logger.debug(self._call_log("🚨 Barge-in already active, skipping duplicate"))
                return  # Already handling barge-in

            logger.info("� Barge-in interrupt: cancelling audio & processing")
            self.barge_in_active.set()

            try:
                # Immediate task cancellation (< 1ms)
                await self._cancel_current_playback()

                # Cancel Route Turn Thread processing (greeting/AI response)
                if self.route_turn_thread:
                    await self.route_turn_thread.cancel_current_processing()
                else:
                    logger.warning("⚠️ No Route Turn Thread available for cancellation")

                # Stop audio output immediately (< 50ms)
                await self._send_stop_audio_command()

                logger.debug("✅ Barge-in interrupt complete")

            except Exception as e:
                logger.error(f"❌ Error handling barge-in: {e}")
                import traceback
                logger.error(f"❌ Barge-in error traceback: {traceback.format_exc()}")
            finally:
                # Reset barge-in state after a brief delay
                asyncio.create_task(self._reset_barge_in_state())

    async def _cancel_current_playback(self):
        """
        Cancel any current playback task immediately.
        """
        if self.current_playback_task and not self.current_playback_task.done():
            self.current_playback_task.cancel()
            try:
                await self.current_playback_task
            except asyncio.CancelledError:
                logger.debug("Playback task cancelled successfully")

    async def _send_stop_audio_command(self):
        """
        Send immediate stop audio command to ACS.
        """
        try:
            stop_audio_data = {
                "Kind": "StopAudio",
                "AudioData": None,
                "StopAudio": {},
            }
            json_data = json.dumps(stop_audio_data)
            await self.websocket.send_text(json_data)
            logger.debug("Stop audio command sent to ACS")
        except Exception as e:
            logger.error(f"Failed to send stop audio command: {e}")

    async def _reset_barge_in_state(self):
        """
        Reset barge-in state after brief delay.
        """
        await asyncio.sleep(0.1)  # Brief delay to prevent rapid re-triggering
        self.barge_in_active.clear()
        logger.debug("Barge-in state reset")

    async def handle_media_message(self, stream_data: str, recognizer, acs_handler):
        """
        Handle incoming media messages (Main Event Loop responsibility).

        :param stream_data: JSON string containing media message data
        :type stream_data: str
        :param recognizer: Speech recognition client
        :param acs_handler: ACS media handler instance
        """
        start_time = time.time()
        try:
            data = json.loads(stream_data)
            kind = data.get("kind")
            # Log all message kinds with a shorthand
            if kind == "AudioMetadata":
                logger.debug("Received audio metadata")
                # AudioMetadata indicates call connection is ready for audio
        
                if acs_handler and acs_handler.speech_sdk_thread:
                    logger.debug("🎤 Prewarming recognizer from AudioMetadata...")
                    acs_handler.speech_sdk_thread.start_recognizer()
                else:
                    logger.warning("⚠️ No Speech SDK thread available for starting recognizer")
                
                if DTMF_VALIDATION_ENABLED:
                    # Kick off a one-time background waiter that opens the gate when ready
                    if not self._validation_task or self._validation_task.done():
                        self._validation_task = asyncio.create_task(self._await_validation_and_open_gate())
                else:
                                        # No validation — open the gate and optionally play greeting
                    self._validation_gate_open = True
                    if not self.greeting_played:
                        await self._play_greeting(acs_handler)


            elif kind == "AudioData":
                # Fast gate-check to avoid expensive waits on every frame
                if DTMF_VALIDATION_ENABLED and not self._validation_gate_open:
                    if not self._validation_log_once:
                        logger.info(self._call_log("🔒 Audio gate closed — holding frames until DTMF validation completes"))
                        self._validation_log_once = True
                    return

                # Process audio data asynchronously to prevent blocking main event loop
                audio_data_section = data.get("audioData", {})
                is_silent = audio_data_section.get("silent", True)

                logger.debug(f"🎵 AudioData received - silent: {is_silent}")
                if not is_silent:
                    audio_bytes = audio_data_section.get("data")
                    if audio_bytes:
                        # 🎯 Smart Audio Processing with Backpressure
                        await self._process_audio_with_backpressure(audio_bytes, recognizer)
                else:
                    logger.info("🔕 Silence detected")
            elif kind == "DtmfData": 
                # Log DTMF data messages for tracing and debugging
                dtmf_data = data.get("dtmfData", {})
                logger.info(self._call_log(f"🔔 DTMF Data received: {dtmf_data}"))
            else:
                logger.info(f">>>🔔 Unhandled media message kind: {kind}")


        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in media message: {e}")
        except Exception as e:
            logger.error(f"Error handling media message: {e}")
        finally:
            total_duration = time.time() - start_time
            if (
                total_duration > 0.005
            ):  # Warn if total message processing takes more than 5ms
                logger.warning(
                    f"⚠️ Message processing took {total_duration:.3f}s - should be < 5ms for non-blocking!"
                )

    async def _process_audio_chunk_async(
        self, audio_bytes: Union[str, bytes], recognizer
    ) -> None:
        """
        Process audio chunk asynchronously to prevent blocking the main event loop.

        :param audio_bytes: Base64 string or raw bytes of audio data
        :type audio_bytes: Union[str, bytes]
        :param recognizer: Speech recognizer instance
        """
        chunk_start_time = time.time()
        try:
            # Handle base64 decoding if needed
            if isinstance(audio_bytes, str):
                logger.debug(f"🔄 Decoding base64 audio data: {len(audio_bytes)} chars")
                decode_start = time.time()
                audio_bytes = base64.b64decode(audio_bytes)
                decode_time = time.time() - decode_start
                logger.debug(
                    f"✅ Base64 decode completed in {decode_time:.3f}s: {len(audio_bytes)} bytes"
                )

            # Check if recognizer is available
            if not recognizer:
                logger.error("❌ No recognizer available for audio processing")
                return

            logger.debug(f"🎤 Sending {len(audio_bytes)} bytes to recognizer...")

            # Run the speech recognizer write in a thread pool with timeout protection
            executor_start = time.time()

            try:
                # Add timeout to prevent hanging on recognizer.write_bytes
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,  # Use default ThreadPoolExecutor
                        recognizer.write_bytes,
                        audio_bytes,
                    ),
                    timeout=0.05,  # 50ms timeout for real-time audio processing
                )

                executor_time = time.time() - executor_start
                total_time = time.time() - chunk_start_time
                logger.debug(
                    f"✅ Audio chunk processed: {len(audio_bytes)} bytes in {total_time:.3f}s (executor: {executor_time:.3f}s)"
                )

                # Warn if processing is taking too long
                if total_time > 0.020:  # 20ms threshold for real-time audio (50 fps)
                    pass
                    # logger.warning(f"⚠️ Audio chunk processing slow: {total_time:.3f}s for {len(audio_bytes)} bytes")

            except asyncio.TimeoutError:
                timeout_duration = time.time() - executor_start
                logger.error(
                    f"⏰ Audio processing timeout after {timeout_duration:.3f}s - recognizer.write_bytes hanging!"
                )
                logger.error(
                    f"⏰ This suggests the speech recognizer is blocked or unresponsive"
                )
                # Don't re-raise - just skip this audio chunk
                return

        except Exception as e:
            total_time = time.time() - chunk_start_time
            logger.error(f"❌ Error processing audio chunk after {total_time:.3f}s: {e}")
            logger.error(f"❌ Recognizer state: {recognizer is not None}")
            logger.error(
                f"❌ Audio bytes type: {type(audio_bytes)}, length: {len(audio_bytes) if audio_bytes else 0}"
            )
    async def _process_audio_with_backpressure(self, audio_bytes: Union[str, bytes], recognizer) -> None:
        """
        Acquire a bounded concurrency slot quickly; if saturated, buffer briefly, else drop.
        """
        acquired = False
        try:
            # Try to acquire quickly to keep loop snappy
            acquired = await asyncio.wait_for(self.audio_processing_semaphore.acquire(), timeout=0.005)
        except asyncio.TimeoutError:
            acquired = False

        if acquired:
            try:
                await self._process_audio_chunk_async(audio_bytes, recognizer)
                self.processed_frames_count += 1
            finally:
                self.audio_processing_semaphore.release()
            return

        # Couldn't acquire fast — try short overflow buffer
        try:
            self.audio_overflow_buffer.put_nowait(audio_bytes)
            if not self.buffer_processing_task or self.buffer_processing_task.done():
                self.buffer_processing_task = asyncio.create_task(self._drain_overflow_buffer(recognizer))
        except asyncio.QueueFull:
            self.dropped_frames_count += 1
            now = time.time()
            if now - self.last_performance_log > 1.0:
                self.last_performance_log = now
                logger.warning(self._call_log(f"⚠️ Audio overflow: dropped_frames={self.dropped_frames_count}, processed_frames={self.processed_frames_count}"))

    async def _drain_overflow_buffer(self, recognizer) -> None:
        """Drain the short overflow buffer without starving the main loop."""
        while not self.audio_overflow_buffer.empty():
            try:
                audio_bytes = self.audio_overflow_buffer.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                acquired = await asyncio.wait_for(self.audio_processing_semaphore.acquire(), timeout=0.010)
            except asyncio.TimeoutError:
                # Give up this cycle; requeue and exit to avoid tight spin
                try:
                    self.audio_overflow_buffer.put_nowait(audio_bytes)
                except asyncio.QueueFull:
                    self.dropped_frames_count += 1
                return
            try:
                await self._process_audio_chunk_async(audio_bytes, recognizer)
                self.processed_frames_count += 1
            finally:
                self.audio_processing_semaphore.release()


    async def _play_greeting(self, acs_handler=None):
        """
        Queue greeting for playback through Route Turn Thread (maintains architecture consistency).

        :param acs_handler: ACS media handler instance for greeting configuration
        """
        if self.greeting_played:
            return  # Already played

        # Use the provided handler reference
        if not acs_handler:
            logger.warning("No ACS media handler provided for greeting playback")
            return

        greeting_text = getattr(acs_handler, "greeting_text", None)
        if not greeting_text:
            logger.debug("No greeting text configured")
            self.greeting_played = True
            return

        logger.info(
            f"🎵 Queueing greeting for Route Turn Thread processing: {greeting_text}"
        )

        try:
            # Create greeting event for Route Turn Thread processing
            greeting_event = SpeechEvent(
                event_type=SpeechEventType.GREETING,
                text=greeting_text,
                language="en-US",  # Default language for greeting
            )

            # Queue greeting through the same pipeline as speech events
            # This ensures it's processed in the Route Turn Thread with proper sequencing
            acs_handler.thread_bridge.queue_speech_result(
                acs_handler.speech_queue, greeting_event
            )
            self.greeting_played = True

            logger.info("✅ Greeting queued for Route Turn Thread processing")

        except Exception as e:
            logger.error(f"❌ Failed to queue greeting: {e}")
            self.greeting_played = True  # Mark as attempted to avoid retry loops


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

        # Core dependencies
        self.websocket = websocket
        self.orchestrator_func = orchestrator_func
        self.call_connection_id = call_connection_id
        self.session_id = session_id or call_connection_id
        self.memory_manager = memory_manager
        self.greeting_text = greeting_text

        # Initialize speech recognizer
        self.recognizer = recognizer or StreamingSpeechRecognizerFromBytes(
            candidate_languages=["en-US", "fr-FR", "de-DE", "es-ES", "it-IT"],
            vad_silence_timeout_ms=800,
            audio_format="pcm",
        )

        # Cross-thread communication infrastructure
        max_q = int(os.getenv("SPEECH_QUEUE_MAXSIZE", "10"))
        self.speech_queue = BoundedSpeechQueue(maxsize=max_q)  # Instrumented bounded queue
        self.thread_bridge = ThreadBridge(call_connection_id)  # Pass call ID to bridge

        # Initialize Route Turn Thread first (needed for MainEventLoop reference)
        self.route_turn_thread = RouteTurnThread(
            call_connection_id=call_connection_id,
            speech_queue=self.speech_queue,
            orchestrator_func=orchestrator_func,
            memory_manager=memory_manager,
            websocket=websocket,
        )

        # Initialize Main Event Loop with Route Turn Thread reference for barge-in
        self.main_event_loop = MainEventLoop(
            websocket, call_connection_id, self.route_turn_thread, self.memory_manager
        )

        # Initialize Speech SDK Thread
        self.speech_sdk_thread = SpeechSDKThread(
            call_connection_id=call_connection_id,
            recognizer=self.recognizer,
            thread_bridge=self.thread_bridge,
            barge_in_handler=self.main_event_loop.handle_barge_in,
            speech_queue=self.speech_queue,
        )

        # Lifecycle management
        self.running = False
        self._stopped = False  # Prevent multiple stop calls

    def _call_log(self, message: str) -> str:
        """Format log message with call connection ID prefix."""
        call_short = self.call_connection_id[-8:] if len(self.call_connection_id) > 8 else self.call_connection_id
        return f"[ACSMediaHandler call-{call_short}] {message}"


    async def start(self):
        """
        Start all three threads in coordinated fashion.

        :raises Exception: When thread startup fails
        """
        with tracer.start_as_current_span(
            "v1.acs_media_handler.start",
            kind=SpanKind.INTERNAL,
            attributes={
                "call.connection.id": self.call_connection_id,
                "session.id": self.session_id,
                "architecture": "three_thread_coordinated",
            },
        ) as span:
            try:
                logger.info(
                    self._call_log(f"🚀 Starting three-thread ACS media handler for call: {self.call_connection_id}")
                )
                self.running = True

                # Capture the main event loop for cross-thread communication
                main_loop = asyncio.get_running_loop()
                self.thread_bridge.set_main_loop(main_loop)
                logger.info(
                    self._call_log(f"🔗 Main event loop captured for cross-thread communication: {id(main_loop)}")
                )

                # Store reference to this handler in the WebSocket for greeting access
                self.websocket._acs_media_handler = self

                # Start threads in order of dependency
                # 1. Speech SDK Thread (foundation - prepare but don't start recognizer yet)
                self.speech_sdk_thread.prepare_thread()

                # 2. Route Turn Thread (depends on speech queue)
                await self.route_turn_thread.start()

                # 3. Main Event Loop is already running (FastAPI context)
                logger.info(self._call_log("🌐 Main Event Loop is ready for WebSocket handling"))

                # Greeting and recognizer will start automatically on first AudioMetadata
                logger.info(
                    self._call_log("🎵 Greeting and recognizer scheduled for first AudioMetadata reception")
                )

                span.set_status(
                    Status(StatusCode.OK)
                )  # Success status without description
                logger.info("✅ Three-thread ACS media handler started successfully")

            except Exception as e:
                if hasattr(span, "set_status"):
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.error(f"❌ Failed to start media handler: {e}")
                await self.stop()  # Clean shutdown on startup failure
                raise

    async def handle_media_message(self, stream_data: str):
        """
        Handle incoming media messages (Main Event Loop responsibility).

        :param stream_data: JSON string containing media message data
        :type stream_data: str
        """
        try:
            await self.main_event_loop.handle_media_message(
                stream_data, self.recognizer, self
            )
        except Exception as e:
            logger.error(f"❌ Error in handle_media_message: {e}")
            # Don't re-raise to prevent breaking the message processing loop
            # Log the error and continue processing subsequent messages

    async def stop(self):
        """
        Stop all threads in reverse dependency order.
        """
        if self._stopped:
            logger.debug("ACS media handler already stopped or stopping")
            return

        with tracer.start_as_current_span(
            "v1.acs_media_handler.stop", kind=SpanKind.INTERNAL
        ) as span:
            try:
                logger.info("🛑 Stopping three-thread ACS media handler")
                self._stopped = True
                self.running = False

                # Stop threads in reverse dependency order
                # 1. Route Turn Thread (stops AI processing)
                await self.route_turn_thread.stop()

                # 2. Speech SDK Thread (stops audio recognition)
                self.speech_sdk_thread.stop()

                # 3. Main Event Loop cleanup (cancel any pending tasks)
                await self.main_event_loop._cancel_current_playback()

                span.set_status(
                    Status(StatusCode.OK)
                )  # Success status without description
                logger.info("✅ Three-thread ACS media handler stopped successfully")

            except Exception as e:
                if hasattr(span, "set_status"):
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.error(f"❌ Error stopping media handler: {e}")

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
            logger.warning("Cannot queue text playback: media handler not running")
            return False

        # Validate playback type
        direct_playback_types = {
            SpeechEventType.GREETING,
            SpeechEventType.ANNOUNCEMENT,
            SpeechEventType.STATUS_UPDATE,
            SpeechEventType.ERROR_MESSAGE,
        }

        if playback_type not in direct_playback_types:
            logger.error(
                f"Invalid playback type: {playback_type}. Must be one of: {direct_playback_types}"
            )
            return False

        try:
            # Create event for direct text playback
            text_event = SpeechEvent(
                event_type=playback_type, text=text, language=language
            )

            # Queue through the same pipeline as speech events
            self.thread_bridge.queue_speech_result(self.speech_queue, text_event)

            logger.info(f"✅ Queued {playback_type.value} for playback: {text}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to queue text playback: {e}")
            return False
