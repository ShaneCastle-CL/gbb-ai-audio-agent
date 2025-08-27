"""
Live Voice Handler
==================

Business logic handler for Azure AI Speech Live Voice API integration.

This handler manages:
- Azure AI Speech Live Voice client connections
- Real-time audio streaming and processing  
- WebSocket communication with clients
- Session state management and persistence
- Error handling and recovery
- Performance monitoring and metrics

The handler follows the Azure Voice Live API patterns and integrates
with the application's orchestration system using simplified, maintainable code.
"""

import asyncio
import json
import uuid
import base64
import time
import websockets
from datetime import datetime
from typing import Optional, Dict, Any, Callable
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from src.speech.voice_live import AzureVoiceLiveClient
from utils.ml_logging import get_logger
from ..models import (
    VoiceLiveSession,
    VoiceLiveConnectionState,
    VoiceLiveMetrics,
    VoiceLiveSessionStatus,
    VoiceLiveAudioConfig,
    VoiceLiveModelConfig,
)
from ..schemas import (
    VoiceLiveStatusMessage,
    VoiceLiveErrorMessage,
    VoiceLiveTextMessage,
    VoiceLiveMetricsMessage,
    VoiceLiveControlMessage,
)

logger = get_logger("api.v1.handlers.voice_live_handler")
tracer = trace.get_tracer(__name__)


# class VoiceLiveClient:
#     """
#     Simplified Azure Voice Live API client based on the quickstart pattern.
    
#     This client handles WebSocket connections to Azure Voice Live API using
#     the same patterns as the official quickstart example.
#     """
    
#     def __init__(
#         self,
#         *,
#         azure_endpoint: str,
#         api_version: str = "2025-05-01-preview", 
#         token: Optional[str] = None,
#         api_key: Optional[str] = None,
#     ):
#         self.azure_endpoint = azure_endpoint
#         self.api_version = api_version
#         self.token = token
#         self.api_key = api_key
#         self.websocket = None
#         self.message_queue = asyncio.Queue()
#         self.connected = False
        
#     async def connect(self, model: str, session_id: str) -> bool:
#         """Connect to Azure Voice Live API via WebSocket."""
#         try:
#             import websockets
            
#             # Build WebSocket URL following Azure Voice Live API pattern
#             azure_ws_endpoint = self.azure_endpoint.rstrip('/').replace("https://", "wss://")
#             url = f"{azure_ws_endpoint}/voice-live/realtime?api-version={self.api_version}&model={model}"
            
#             # Set up authentication headers
#             extra_headers = {}
#             if self.token:
#                 extra_headers["Authorization"] = f"Bearer {self.token}"
#             elif self.api_key:
#                 extra_headers["api-key"] = self.api_key
            
#             # Add request ID
#             request_id = str(uuid.uuid4())
#             extra_headers["x-ms-client-request-id"] = request_id
            
#             # Connect to WebSocket TODO: investigate further, make it work with ACS
#             self.websocket = await websockets.connect(url, extra_headers=extra_headers)
#             self.connected = True
            
#             # Start message handler
#             asyncio.create_task(self._message_handler())
            
#             logger.info(f"Connected to Azure Voice Live API for session {session_id}")
#             return True
            
#         except Exception as e:
#             logger.error(f"Failed to connect to Azure Voice Live API: {e}")
#             self.connected = False
#             return False
    
#     async def _message_handler(self):
#         """Handle incoming WebSocket messages."""
#         try:
#             async for message in self.websocket:
#                 await self.message_queue.put(message)
#         except Exception as e:
#             logger.error(f"Message handler error: {e}")
#             self.connected = False
    
#     async def send_message(self, message: dict) -> None:
#         """Send a message to the Voice Live API."""
#         if self.websocket and self.connected:
#             try:
#                 await self.websocket.send(json.dumps(message))
#             except Exception as e:
#                 logger.error(f"Failed to send message: {e}")
#                 self.connected = False
#                 raise
    
#     async def receive_message(self, timeout: float = 1.0) -> Optional[dict]:
#         """Receive a message from the Voice Live API."""
#         try:
#             message = await asyncio.wait_for(self.message_queue.get(), timeout=timeout)
#             return json.loads(message)
#         except asyncio.TimeoutError:
#             return None
#         except json.JSONDecodeError as e:
#             logger.error(f"Failed to decode message: {e}")
#             return None
    
#     async def close(self):
#         """Close the WebSocket connection."""
#         if self.websocket:
#             await self.websocket.close()
#             self.connected = False


class VoiceLiveHandler:
    """
    Handler for Live Voice sessions with Azure AI Speech integration.
    
    Manages the complete lifecycle of a Live Voice session including:
    - Azure AI Speech client setup and teardown
    - Audio streaming and processing
    - WebSocket communication
    - Session state persistence
    - Error handling and recovery
    """
    
    def __init__(
        self,
        session_id: str,
        voice_live_client: AzureVoiceLiveClient,
        websocket,
        azure_endpoint: str,
        token: Optional[str] = None,
        api_key: Optional[str] = None,
        redis_client=None,
        orchestrator: Optional[Callable] = None
    ):
        """
        Initialize Live Voice handler.
        
        :param session_id: Unique session identifier
        :param websocket: WebSocket connection for client communication
        :param azure_endpoint: Azure Voice Live API endpoint
        :param token: Azure authentication token (recommended)
        :param api_key: Azure API key (alternative auth)
        :param redis_client: Redis client for session persistence
        :param orchestrator: Optional orchestrator for conversation routing
        """
        self.session_id = session_id
        self.websocket = websocket
        self.azure_endpoint = azure_endpoint
        self.token = token
        self.api_key = api_key
        self.redis_client = redis_client
        self.orchestrator = orchestrator
        
        # Session state
        self.session: Optional[VoiceLiveSession] = None
        self.connection_state: Optional[VoiceLiveConnectionState] = None
        self.metrics: Optional[VoiceLiveMetrics] = None
        
        # Azure Voice Live client
        self.voice_live_client = voice_live_client
        self.is_connected = False
        self.is_streaming = False
        
        # Task management
        self.message_processing_task: Optional[asyncio.Task] = None
        self.metrics_reporting_task: Optional[asyncio.Task] = None
        
        # Configuration
        self.audio_config = VoiceLiveAudioConfig()
        self.model_configuration = VoiceLiveModelConfig(
            model_name="gpt-4o",
            deployment_name="gpt-4o",
            temperature=0.7,
            max_tokens=2000,

            voice_name="en-US-Ava:DragonHDLatestNeural",
            voice_type="azure-standard",
            voice_style=None,
            speaking_rate=1.0,
            voice_temperature=0.8,

            system_instructions="You are a helpful AI assistant responding in natural, engaging language.",
            context_window=4000,
            api_version="2025-05-01-preview",

            turn_detection_type="azure_semantic_vad",
            vad_threshold=0.3,
            prefix_padding_ms=200,
            silence_duration_ms=200,
            remove_filler_words=False,
            
            end_of_utterance_model="semantic_detection_v1",
        )
        
        logger.info(f"VoiceLiveHandler initialized for session {session_id}")
    
    async def initialize(self) -> None:
        """
        Initialize the Live Voice session.
        
        Sets up session state, Azure AI Speech connection, and starts
        background processing tasks.
        
        :raises Exception: If initialization fails
        """
        with tracer.start_as_current_span(
            "voice_live_handler.initialize",
            kind=SpanKind.INTERNAL,
            attributes={"session_id": self.session_id}
        ) as span:
            try:
                # Initialize session models
                await self._initialize_session_state()
                
                # Connect to Azure AI Speech Live Voice API
                await self._connect_azure_speech()
                
                # Send session configuration
                await self._configure_session()
                
                # Start background processing tasks
                await self._start_background_tasks()
                
                # Send initial status message
                await self._send_status_message("connected", "Live Voice session initialized successfully")
                
                span.set_status(Status(StatusCode.OK))
                logger.info(f"Live Voice session initialized successfully: {self.session_id}")
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, f"Initialization failed: {e}"))
                logger.error(f"Failed to initialize Live Voice session {self.session_id}: {e}")
                await self._handle_error("INITIALIZATION_ERROR", str(e), "session_error")
                raise
    
    async def handle_audio_data(self, audio_data: bytes) -> None:
        """
        Handle incoming audio data from the client.
        
        :param audio_data: Raw audio bytes from the client
        """
        if not self.is_connected or not self.is_streaming:
            logger.warning(f"Received audio data while not streaming in session {self.session_id}")
            return
        
        try:
            # Add to metrics
            if self.session:
                self.session.add_audio_bytes(len(audio_data))
            
            if self.connection_state:
                self.connection_state.record_message_received(len(audio_data))
            
            # Convert audio to base64 for Azure Voice Live API
            audio_b64 = base64.b64encode(audio_data).decode("utf-8")
            
            # Send to Azure Voice Live API using the standard event format
            audio_event = {
                "type": "input_audio_buffer.append",
                "audio": audio_b64,
                "event_id": str(uuid.uuid4())
            }
            
            await self.voice_live_client.send_message(audio_event)
            
        except Exception as e:
            logger.error(f"Error handling audio data in session {self.session_id}: {e}")
            await self._handle_error("AUDIO_PROCESSING_ERROR", str(e), "audio_error")
    
    async def handle_text_message(self, text_data: str) -> None:
        """
        Handle incoming text messages from the client.
        
        :param text_data: JSON text message from the client
        """
        try:
            message = json.loads(text_data)
            message_type = message.get("type", "unknown")
            
            if message_type == "control":
                await self._handle_control_message(message)
            elif message_type == "configuration":
                await self._handle_configuration_message(message)
            elif message_type == "text":
                await self._handle_text_input(message)
            else:
                logger.warning(f"Unknown message type '{message_type}' in session {self.session_id}")
            
            if self.connection_state:
                self.connection_state.record_message_received(len(text_data))
                
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON message in session {self.session_id}: {text_data}")
            await self._handle_error("INVALID_MESSAGE", "Invalid JSON message format", "validation_error")
        except Exception as e:
            logger.error(f"Error handling text message in session {self.session_id}: {e}")
            await self._handle_error("MESSAGE_PROCESSING_ERROR", str(e), "unknown_error")
    
    async def cleanup(self) -> None:
        """
        Clean up the Live Voice session resources.
        
        Stops all background tasks, disconnects from Azure AI Speech,
        and persists session state to Redis.
        """
        with tracer.start_as_current_span(
            "voice_live_handler.cleanup",
            attributes={"session_id": self.session_id}
        ) as span:
            try:
                logger.info(f"Starting cleanup for Live Voice session {self.session_id}")
                
                # Stop background tasks
                await self._stop_background_tasks()
                
                # Disconnect from Azure AI Speech
                await self._disconnect_azure_speech()
                
                # Update session status
                if self.session:
                    self.session.set_status(VoiceLiveSessionStatus.DISCONNECTED, "Session ended")
                    self.session.disconnected_at = datetime.utcnow()
                
                # Persist final state to Redis
                await self._persist_session_state()
                
                # Send final status message
                await self._send_status_message("disconnected", "Live Voice session ended")
                
                span.set_status(Status(StatusCode.OK))
                logger.info(f"Live Voice session cleanup completed: {self.session_id}")
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, f"Cleanup failed: {e}"))
                logger.error(f"Error during Live Voice session cleanup {self.session_id}: {e}")
    
    # ============================================================================
    # Private Methods
    # ============================================================================
    
    async def _initialize_session_state(self) -> None:
        """Initialize session state models."""
        self.session = VoiceLiveSession(
            session_id=self.session_id,
            status=VoiceLiveSessionStatus.INITIALIZING,
            audio_config=self.audio_config,
            model_configuration=self.model_configuration,
            websocket_connected=True,
            connection_established_at=datetime.utcnow()
        )
        
        self.connection_state = VoiceLiveConnectionState(
            session_id=self.session_id,
            websocket_id=str(uuid.uuid4()),
            connected_at=datetime.utcnow()
        )
        
        self.metrics = VoiceLiveMetrics(
            session_id=self.session_id,
            measurement_start=datetime.utcnow()
        )
        
        logger.info(f"Session state initialized for {self.session_id}")
    
    async def _connect_azure_speech(self) -> None:
        """Connect to Azure AI Speech Live Voice API."""
        try:
            # Create Voice Live client
            self.voice_live_client = VoiceLiveClient(
                azure_endpoint=self.azure_endpoint,
                token=self.token,
                api_key=self.api_key
            )
            
            # Connect to Azure Voice Live API
            model_name = self.model_configuration.model_name
            success = await self.voice_live_client.connect(model_name, self.session_id)
            
            if success:
                self.is_connected = True
                self.is_streaming = True
                
                if self.session:
                    self.session.azure_speech_connected = True
                    self.session.status = VoiceLiveSessionStatus.CONNECTED
                    
                logger.info(f"Connected to Azure Voice Live API for session {self.session_id}")
            else:
                raise Exception("Failed to establish connection to Azure Voice Live API")
                
        except Exception as e:
            logger.error(f"Failed to connect to Azure Voice Live API for session {self.session_id}: {e}")
            raise
    
    async def _configure_session(self) -> None:
        """Send session configuration to Azure Voice Live API."""
        try:
            # Build session configuration following Azure Voice Live API format
            session_config = {
                "type": "session.update",
                "session": {
                    "instructions": (
                        self.model_configuration.system_instructions or 
                        "You are a helpful AI assistant responding in natural, engaging language."
                    ),
                    "turn_detection": {
                        "type": "azure_semantic_vad",
                        "threshold": self.audio_config.vad_sensitivity,
                        "prefix_padding_ms": 200,
                        "silence_duration_ms": 200,
                        "remove_filler_words": False,
                        "end_of_utterance_detection": {
                            "model": "semantic_detection_v1",
                            "threshold": 0.01,
                            "timeout": 2,
                        },
                    },
                    "input_audio_noise_reduction": {
                        "type": "azure_deep_noise_suppression"
                    } if self.audio_config.noise_reduction else None,
                    "input_audio_echo_cancellation": {
                        "type": "server_echo_cancellation"
                    } if self.audio_config.echo_cancellation else None,
                    "voice": {
                        "name": self.model_configuration.voice_name,
                        "type": "azure-standard",
                        "temperature": self.model_configuration.temperature,
                    },
                },
                "event_id": str(uuid.uuid4())
            }
            
            # Remove None values
            if not session_config["session"]["input_audio_noise_reduction"]:
                del session_config["session"]["input_audio_noise_reduction"]
            if not session_config["session"]["input_audio_echo_cancellation"]:
                del session_config["session"]["input_audio_echo_cancellation"]
            
            # Send configuration
            await self.voice_live_client.send_message(session_config)
            logger.info(f"Session configuration sent for {self.session_id}")
            
        except Exception as e:
            logger.error(f"Failed to configure session {self.session_id}: {e}")
            raise
    
    async def _disconnect_azure_speech(self) -> None:
        """Disconnect from Azure AI Speech Live Voice API."""
        try:
            if self.voice_live_client:
                await self.voice_live_client.close()
                logger.info(f"Disconnected from Azure Voice Live API for session {self.session_id}")
                self.voice_live_client = None
            
            self.is_connected = False
            self.is_streaming = False
            
            if self.session:
                self.session.azure_speech_connected = False
            
        except Exception as e:
            logger.error(f"Error disconnecting from Azure Voice Live API for session {self.session_id}: {e}")
    
    async def _start_background_tasks(self) -> None:
        """Start background processing tasks."""
        self.message_processing_task = asyncio.create_task(self._process_voice_live_messages())
        self.metrics_reporting_task = asyncio.create_task(self._report_metrics())
        
        logger.info(f"Background tasks started for session {self.session_id}")
    
    async def _stop_background_tasks(self) -> None:
        """Stop background processing tasks."""
        tasks = [
            self.message_processing_task,
            self.metrics_reporting_task
        ]
        
        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        logger.info(f"Background tasks stopped for session {self.session_id}")
    
    async def _process_voice_live_messages(self) -> None:
        """Process messages from Azure Voice Live API."""
        while self.is_connected:
            try:
                # Receive message from Azure Voice Live API
                message = await self.voice_live_client.receive_message(timeout=1.0)
                if not message:
                    continue
                
                message_type = message.get("type", "")
                
                if message_type == "session.created":
                    await self._handle_session_created(message)
                elif message_type == "response.audio.delta":
                    await self._handle_audio_response(message)
                elif message_type == "input_audio_buffer.speech_started":
                    await self._handle_speech_started(message)
                elif message_type == "input_audio_buffer.speech_stopped":
                    await self._handle_speech_stopped(message)
                elif message_type == "response.text.delta":
                    await self._handle_text_response(message)
                elif message_type == "error":
                    await self._handle_voice_live_error(message)
                else:
                    logger.debug(f"Unhandled message type: {message_type}")
                
            except Exception as e:
                logger.error(f"Error processing Voice Live message in session {self.session_id}: {e}")
                await asyncio.sleep(0.1)  # Prevent tight loop on errors
    
    async def _handle_session_created(self, message: dict) -> None:
        """Handle session.created event from Azure Voice Live API."""
        session_data = message.get("session", {})
        session_id = session_data.get("id", "unknown")
        logger.info(f"Azure Voice Live session created: {session_id}")
        
        # Send status update to client
        await self._send_status_message("processing", "Ready to process audio")
    
    async def _handle_audio_response(self, message: dict) -> None:
        """Handle response.audio.delta event from Azure Voice Live API."""
        try:
            # Extract audio data
            audio_delta = message.get("delta", "")
            if audio_delta:
                # Decode base64 audio and send to client
                audio_bytes = base64.b64decode(audio_delta)
                
                # Send audio to client via WebSocket
                audio_message = {
                    "type": "audio",
                    "data": audio_delta,  # Keep as base64 for client
                    "format": "pcm",
                    "sample_rate": self.audio_config.sample_rate,
                    "channels": self.audio_config.channels,
                    "session_id": self.session_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
                await self._send_websocket_message(audio_message)
                
                # Update metrics
                if self.session:
                    self.session.add_audio_bytes(len(audio_bytes))
                
        except Exception as e:
            logger.error(f"Error handling audio response in session {self.session_id}: {e}")
    
    async def _handle_speech_started(self, message: dict) -> None:
        """Handle input_audio_buffer.speech_started event."""
        logger.info(f"Speech started in session {self.session_id}")
        await self._send_status_message("processing", "User speaking")
    
    async def _handle_speech_stopped(self, message: dict) -> None:
        """Handle input_audio_buffer.speech_stopped event."""
        logger.info(f"Speech stopped in session {self.session_id}")
        await self._send_status_message("processing", "Processing speech")
    
    async def _handle_text_response(self, message: dict) -> None:
        """Handle response.text.delta event from Azure Voice Live API."""
        try:
            text_delta = message.get("delta", "")
            if text_delta:
                # Send text response to client
                text_message = VoiceLiveTextMessage(
                    session_id=self.session_id,
                    content=text_delta,
                    role="assistant",
                    is_partial=True,
                    timestamp=datetime.utcnow()
                )
                
                await self._send_websocket_message(text_message.dict())
                
                # Add to conversation history
                if self.session:
                    self.session.add_conversation_message("assistant", text_delta)
                
        except Exception as e:
            logger.error(f"Error handling text response in session {self.session_id}: {e}")
    
    async def _handle_voice_live_error(self, message: dict) -> None:
        """Handle error events from Azure Voice Live API."""
        error_details = message.get("error", {})
        error_type = error_details.get("type", "Unknown")
        error_code = error_details.get("code", "Unknown")
        error_message = error_details.get("message", "No message provided")
        
        logger.error(f"Azure Voice Live API error: {error_type} - {error_code} - {error_message}")
        await self._handle_error("AZURE_VOICE_LIVE_ERROR", error_message, "service_error")
    
    async def _report_metrics(self) -> None:
        """Report performance metrics periodically."""
        while self.is_connected:
            try:
                await asyncio.sleep(30)  # Report every 30 seconds
                
                if self.session and self.metrics:
                    # Calculate basic metrics
                    duration = self.session.get_session_duration_seconds()
                    
                    metrics_message = VoiceLiveMetricsMessage(
                        session_id=self.session_id,
                        session_stats={
                            "total_messages": self.session.total_messages,
                            "audio_bytes_processed": self.session.audio_bytes_processed,
                            "session_duration_seconds": duration,
                            "error_count": self.session.error_count,
                        },
                        timestamp=datetime.utcnow()
                    )
                    
                    await self._send_websocket_message(metrics_message.dict())
                
            except Exception as e:
                logger.error(f"Error reporting metrics for session {self.session_id}: {e}")
                await asyncio.sleep(5)  # Wait before retrying
    
    async def handle_text_message(self, text_data: str) -> None:
        """
        Handle incoming text messages from the client.
        
        :param text_data: JSON text message from the client
        """
        try:
            message = json.loads(text_data)
            message_type = message.get("type", "unknown")
            
            if message_type == "control":
                await self._handle_control_message(message)
            elif message_type == "configuration":
                await self._handle_configuration_message(message)
            elif message_type == "text":
                await self._handle_text_input(message)
            else:
                logger.warning(f"Unknown message type '{message_type}' in session {self.session_id}")
            
            if self.connection_state:
                self.connection_state.record_message_received(len(text_data))
                
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON message in session {self.session_id}: {text_data}")
            await self._handle_error("INVALID_MESSAGE", "Invalid JSON message format", "validation_error")
        except Exception as e:
            logger.error(f"Error handling text message in session {self.session_id}: {e}")
            await self._handle_error("MESSAGE_PROCESSING_ERROR", str(e), "unknown_error")
    
    async def cleanup(self) -> None:
        """
        Clean up the Live Voice session resources.
        
        Stops all background tasks, disconnects from Azure AI Speech,
        and persists session state to Redis.
        """
        with tracer.start_as_current_span(
            "voice_live_handler.cleanup",
            attributes={"session_id": self.session_id}
        ) as span:
            try:
                logger.info(f"Starting cleanup for Live Voice session {self.session_id}")
                
                # Stop background tasks
                await self._stop_background_tasks()
                
                # Disconnect from Azure AI Speech
                await self._disconnect_azure_speech()
                
                # Update session status
                if self.session:
                    self.session.status = VoiceLiveSessionStatus.DISCONNECTED
                    self.session.update_activity()
                
                # Persist final state to Redis
                await self._persist_session_state()
                
                # Send final status message
                await self._send_status_message("disconnected", "Live Voice session ended")
                
                span.set_status(Status(StatusCode.OK))
                logger.info(f"Live Voice session cleanup completed: {self.session_id}")
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, f"Cleanup failed: {e}"))
                logger.error(f"Error during Live Voice session cleanup {self.session_id}: {e}")
    
    # ============================================================================
    # Private Methods
    # ============================================================================
    
    async def _initialize_session_state(self) -> None:
        """Initialize session state models."""
        self.session = VoiceLiveSession(
            session_id=self.session_id,
            status=VoiceLiveSessionStatus.INITIALIZING,
            audio_config=self.audio_config,
            model_configuration=self.model_configuration,
            websocket_connected=True,
            connection_established_at=datetime.utcnow()
        )
        
        self.connection_state = VoiceLiveConnectionState(
            session_id=self.session_id,
            websocket_id=str(uuid.uuid4()),
            connected_at=datetime.utcnow()
        )
        
        self.metrics = VoiceLiveMetrics(
            session_id=self.session_id,
            measurement_start=datetime.utcnow()
        )
        
        logger.info(f"Session state initialized for {self.session_id}")
    
    async def _connect_azure_speech(self) -> None:
        """Connect to Azure AI Speech Live Voice API using pool."""
        try:
            # Acquire Live Voice client from pool
            self.voice_live_client = await self.voice_live_pool.acquire()
            logger.info(f"Acquired Live Voice client from pool for session {self.session_id}")

            # Connect to Azure AI Speech Live Voice API
            result = await self.voice_live_client.connect(
                session_id=self.session_id
            )

            # For now, simulate a successful connection
            if result:
                self.is_connected = True
                self.is_streaming = True

                if self.session:
                    self.session.azure_speech_connected = True
                self.session.status = VoiceLiveSessionStatus.CONNECTED
                logger.info(f"Connected to Azure AI Speech for session {self.session_id}")
            else:
                logger.warning(f"Failed to connect to Azure AI Speech for session {self.session_id}")

        except Exception as e:
            logger.error(f"Failed to connect to Azure AI Speech for session {self.session_id}: {e}")
            raise
    
    async def _disconnect_azure_speech(self) -> None:
        """Disconnect from Azure AI Speech Live Voice API and return client to pool."""
        try:
            if self.voice_live_client:
                # Close the Live Voice connection if needed
                # await self.voice_live_client.close()
                
                # Return client to pool
                await self.voice_live_pool.release(self.voice_live_client)
                logger.info(f"Released Live Voice client back to pool for session {self.session_id}")
                self.voice_live_client = None
            
            self.is_connected = False
            self.is_streaming = False
            
            if self.session:
                self.session.azure_speech_connected = False
            
            logger.info(f"Disconnected from Azure AI Speech for session {self.session_id}")
            
        except Exception as e:
            logger.error(f"Error disconnecting from Azure AI Speech for session {self.session_id}: {e}")
    
    async def _start_background_tasks(self) -> None:
        """Start background processing tasks."""
        self.audio_processing_task = asyncio.create_task(self._process_audio_queue())
        self.response_processing_task = asyncio.create_task(self._process_response_queue())
        self.metrics_reporting_task = asyncio.create_task(self._report_metrics())
        
        logger.info(f"Background tasks started for session {self.session_id}")
    
    async def _stop_background_tasks(self) -> None:
        """Stop background processing tasks."""
        tasks = [
            self.audio_processing_task,
            self.response_processing_task,
            self.metrics_reporting_task
        ]
        
        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        logger.info(f"Background tasks stopped for session {self.session_id}")
    
    async def _process_audio_queue(self) -> None:
        """Process audio data from the queue."""
        while self.is_streaming:
            try:
                audio_data = await asyncio.wait_for(self.audio_queue.get(), timeout=1.0)
                
                # Process audio through Azure AI Speech
                # In a real implementation, you would send the audio to Azure AI Speech
                # and handle the response
                
                # Simulate processing delay
                await asyncio.sleep(0.01)
                
                # Queue a simulated response
                await self.response_queue.put({
                    "type": "transcription",
                    "text": "Simulated transcription",
                    "confidence": 0.95
                })
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error processing audio in session {self.session_id}: {e}")
                await self._handle_error("AUDIO_PROCESSING_ERROR", str(e), "audio_error")
    
    async def _process_response_queue(self) -> None:
        """Process responses from Azure AI Speech."""
        while self.is_connected:
            try:
                response = await asyncio.wait_for(self.response_queue.get(), timeout=1.0)
                
                if response.get("type") == "transcription":
                    # Send transcription to client
                    text_message = VoiceLiveTextMessage(
                        session_id=self.session_id,
                        content=response.get("text", ""),
                        role="user",
                        confidence=response.get("confidence"),
                        timestamp=datetime.utcnow()
                    )
                    
                    await self._send_websocket_message(text_message.dict())
                    
                    # Add to session history
                    if self.session:
                        self.session.add_conversation_message(
                            "user",
                            response.get("text", ""),
                            {"confidence": response.get("confidence")}
                        )
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error processing response in session {self.session_id}: {e}")
                await self._handle_error("RESPONSE_PROCESSING_ERROR", str(e), "unknown_error")
    
    async def _report_metrics(self) -> None:
        """Report performance metrics periodically."""
        while self.is_connected:
            try:
                await asyncio.sleep(30)  # Report every 30 seconds
                
                if self.metrics and self.session:
                    metrics_message = VoiceLiveMetricsMessage(
                        session_id=self.session_id,
                        session_stats={
                            "total_messages": self.session.total_messages,
                            "audio_bytes_processed": self.session.audio_bytes_processed,
                            "error_count": self.session.error_count,
                            "average_response_time_ms": self.session.average_response_time_ms
                        },
                        timestamp=datetime.utcnow()
                    )
                    
                    await self._send_websocket_message(metrics_message.dict())
                
            except Exception as e:
                logger.error(f"Error reporting metrics for session {self.session_id}: {e}")
    
    async def _handle_control_message(self, message: Dict[str, Any]) -> None:
        """Handle control messages from the client."""
        command = message.get("command", "")
        
        if command == "start":
            self.is_streaming = True
            await self._send_status_message("processing", "Audio streaming started")
        elif command == "stop":
            self.is_streaming = False
            await self._send_status_message("idle", "Audio streaming stopped")
        elif command == "pause":
            self.is_streaming = False
            await self._send_status_message("paused", "Session paused")
        elif command == "resume":
            self.is_streaming = True
            await self._send_status_message("processing", "Session resumed")
        elif command == "status":
            status = "connected" if self.is_connected else "disconnected"
            await self._send_status_message(status, f"Session is {status}")
        else:
            await self._handle_error("UNKNOWN_COMMAND", f"Unknown control command: {command}", "validation_error")
    
    async def _handle_configuration_message(self, message: Dict[str, Any]) -> None:
        """Handle configuration update messages."""
        config_type = message.get("configuration_type", "")
        config_data = message.get("configuration_data", {})
        
        try:
            if config_type == "audio":
                # Update audio configuration
                for key, value in config_data.items():
                    if hasattr(self.audio_config, key):
                        setattr(self.audio_config, key, value)
                
                # Send updated configuration to Azure Voice Live API if needed
                await self._configure_session()
            
            elif config_type == "model":
                # Update model configuration
                for key, value in config_data.items():
                    if hasattr(self.model_configuration, key):
                        setattr(self.model_configuration, key, value)
                
                # Send updated configuration to Azure Voice Live API if needed
                await self._configure_session()
            
            else:
                await self._handle_error("UNKNOWN_CONFIG_TYPE", f"Unknown configuration type: {config_type}", "validation_error")
        
        except Exception as e:
            await self._handle_error("CONFIGURATION_ERROR", str(e), "unknown_error")
    
    async def _handle_text_input(self, message: Dict[str, Any]) -> None:
        """Handle direct text input from the client."""
        text_content = message.get("content", "")
        
        if text_content and self.session:
            # Add to conversation history
            self.session.add_conversation_message("user", text_content)
            
            # Process through orchestrator if available
            if self.orchestrator:
                try:
                    response = await self.orchestrator.process_text(text_content, self.session_id)
                    # Handle orchestrator response if needed
                except Exception as e:
                    logger.error(f"Orchestrator error for session {self.session_id}: {e}")
    
    async def _send_status_message(self, status: str, message: str, level: str = "info") -> None:
        """Send a status message to the client."""
        status_message = VoiceLiveStatusMessage(
            session_id=self.session_id,
            status=status,
            message=message,
            level=level,
            timestamp=datetime.utcnow()
        )
        
        await self._send_websocket_message(status_message.dict())
    
    async def _handle_error(self, error_code: str, error_message: str, error_type: str) -> None:
        """Handle errors and send error messages to the client."""
        if self.session:
            self.session.record_error(error_message)
        
        if self.connection_state:
            if error_type in ["network_error", "service_error"]:
                self.connection_state.record_connection_error()
            elif error_type == "validation_error":
                self.connection_state.record_protocol_error()
        
        error_msg = VoiceLiveErrorMessage(
            session_id=self.session_id,
            error_code=error_code,
            error_message=error_message,
            error_type=error_type,
            timestamp=datetime.utcnow()
        )
        
        await self._send_websocket_message(error_msg.dict())
    
    async def _send_websocket_message(self, message: Dict[str, Any]) -> None:
        """Send a message to the WebSocket client."""
        try:
            await self.websocket.send_text(json.dumps(message, default=str))
            
            if self.connection_state:
                self.connection_state.record_message_sent(len(json.dumps(message, default=str)))
        
        except Exception as e:
            logger.error(f"Error sending WebSocket message in session {self.session_id}: {e}")
    
    async def _persist_session_state(self) -> None:
        """Persist session state to Redis."""
        try:
            if self.session and self.redis_client:
                # Serialize session state
                session_data = {
                    "session_id": self.session.session_id,
                    "status": self.session.status.value,
                    "created_at": self.session.created_at.isoformat(),
                    "conversation_summary": self.session.get_conversation_summary(),
                    "total_messages": self.session.total_messages,
                    "audio_bytes_processed": self.session.audio_bytes_processed,
                    "error_count": self.session.error_count,
                }
                
                # Store in Redis with expiration
                key = f"voice_live_session:{self.session_id}"
                await self.redis_client.setex(key, 3600, json.dumps(session_data, default=str))
                
                logger.info(f"Session state persisted for {self.session_id}")
        
        except Exception as e:
            logger.error(f"Error persisting session state for {self.session_id}: {e}")
    
    async def _handle_configuration_message(self, message: Dict[str, Any]) -> None:
        """Handle configuration update messages."""
        config_type = message.get("configuration_type", "")
        config_data = message.get("configuration_data", {})
        
        try:
            if config_type == "audio":
                # Update audio configuration
                for key, value in config_data.items():
                    if hasattr(self.audio_config, key):
                        setattr(self.audio_config, key, value)
                
                await self._send_status_message("configured", f"Audio configuration updated")
            
            elif config_type == "model":
                # Update model configuration
                for key, value in config_data.items():
                    if hasattr(self.model_configuration, key):
                        setattr(self.model_configuration, key, value)
                
                await self._send_status_message("configured", f"Model configuration updated")
            
            else:
                await self._handle_error("UNKNOWN_CONFIG_TYPE", f"Unknown configuration type: {config_type}", "validation_error")
        
        except Exception as e:
            await self._handle_error("CONFIGURATION_ERROR", str(e), "unknown_error")
    
    async def _handle_text_input(self, message: Dict[str, Any]) -> None:
        """Handle direct text input from the client."""
        text_content = message.get("content", "")
        
        if text_content and self.session:
            # Add to conversation history
            self.session.add_conversation_message("user", text_content)
            
            # Process through orchestrator if available
            if self.orchestrator:
                # This would integrate with your existing orchestration system
                # For now, just echo back a response
                response = f"Processed: {text_content}"
                
                response_message = VoiceLiveTextMessage(
                    session_id=self.session_id,
                    content=response,
                    role="assistant",
                    timestamp=datetime.utcnow()
                )
                
                await self._send_websocket_message(response_message.dict())
                self.session.add_conversation_message("assistant", response)
    
    async def _send_status_message(self, status: str, message: str, level: str = "info") -> None:
        """Send a status message to the client."""
        status_message = VoiceLiveStatusMessage(
            session_id=self.session_id,
            status=status,
            message=message,
            level=level,
            timestamp=datetime.utcnow()
        )
        
        await self._send_websocket_message(status_message.dict())
    
    async def _handle_error(self, error_code: str, error_message: str, error_type: str) -> None:
        """Handle errors and send error messages to the client."""
        if self.session:
            self.session.record_error(error_message)
        
        if self.connection_state:
            if error_type in ["network_error", "service_error"]:
                self.connection_state.record_connection_error()
            elif error_type == "validation_error":
                self.connection_state.record_protocol_error()
        
        error_msg = VoiceLiveErrorMessage(
            session_id=self.session_id,
            error_code=error_code,
            error_message=error_message,
            error_type=error_type,
            timestamp=datetime.utcnow()
        )
        
        await self._send_websocket_message(error_msg.dict())
    
    async def _send_websocket_message(self, message: Dict[str, Any]) -> None:
        """Send a message to the WebSocket client."""
        try:
            await self.websocket.send_text(json.dumps(message, default=str))
            
            if self.connection_state:
                self.connection_state.record_message_sent(len(json.dumps(message, default=str)))
        
        except Exception as e:
            logger.error(f"Error sending WebSocket message in session {self.session_id}: {e}")
    
    async def _persist_session_state(self) -> None:
        """Persist session state to Redis."""
        try:
            if self.session and self.redis_client:
                session_data = self.session.dict()
                await self.redis_client.setex(
                    f"voice_live_session:{self.session_id}",
                    3600,  # 1 hour TTL
                    json.dumps(session_data, default=str)
                )
                
                logger.info(f"Session state persisted for {self.session_id}")
        
        except Exception as e:
            logger.error(f"Error persisting session state for {self.session_id}: {e}")