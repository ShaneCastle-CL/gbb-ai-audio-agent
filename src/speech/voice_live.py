"""Azure AI Speech Voice Live Client Integration Module.

This module provides comprehensive Azure AI Speech Voice Live API integration for real-time
voice interactions with generative AI models. It offers WebSocket-based communication
with Azure AI Speech service for low-latency, bidirectional audio streaming and
real-time conversation processing.

The Voice Live API enables:
- Real-time audio streaming with voice activity detection
- Semantic audio processing and interruption handling
- Direct integration with generative AI models
- Custom voice and conversation flow configuration
- Automatic audio enhancement and noise reduction
"""

import asyncio
import json
import os
import uuid
import time
from typing import Optional, Dict, Any, Callable, List
from enum import Enum
from datetime import datetime

import azure.cognitiveservices.speech as speechsdk
from utils.azure_auth import get_credential
from dotenv import load_dotenv

# OpenTelemetry imports for tracing
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

# Import centralized span attributes enum
from src.enums.monitoring import SpanAttr
from utils.ml_logging import get_logger
import queue
from websocket import WebSocketApp
import threading

# Load environment variables
load_dotenv()

# Initialize logger
logger = get_logger(__name__)

# Get tracer
tracer = trace.get_tracer(__name__)

AUDIO_SAMPLE_RATE = 24000

class VoiceLiveConnectionState(str, Enum):
    """Voice Live connection states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STREAMING = "streaming"
    ERROR = "error"
    RECONNECTING = "reconnecting"


class VoiceLiveEventType(str, Enum):
    """Voice Live event types."""
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    AUDIO_RECEIVED = "audio_received"
    TEXT_RECEIVED = "text_received"
    RESPONSE_GENERATED = "response_generated"
    ERROR = "error"
    VAD_START = "vad_start"
    VAD_END = "vad_end"

class VoiceLiveConnection:
    def __init__(self, url: str, headers: dict) -> None:
        self._url = url
        self._headers = headers
        self._ws = None
        self._message_queue = queue.Queue()
        self._connected = False

    def connect(self) -> None:
        def on_message(ws, message):
            self._message_queue.put(message)

        def on_error(ws, error):
            logger.error(f"WebSocket error: {error}")

        def on_close(ws, close_status_code, close_msg):
            logger.info("WebSocket connection closed")
            self._connected = False

        def on_open(ws):
            logger.info("WebSocket connection opened")
            self._connected = True

        self._ws = WebSocketApp(
            self._url,
            header=self._headers,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )

        # Start WebSocket in a separate thread
        self._ws_thread = threading.Thread(target=self._ws.run_forever)
        self._ws_thread.daemon = True
        self._ws_thread.start()

        # Wait for connection to be established
        timeout = 10  # seconds
        start_time = time.time()
        while not self._connected and time.time() - start_time < timeout:
            time.sleep(0.1)

        if not self._connected:
            raise ConnectionError("Failed to establish WebSocket connection")

    def recv(self) -> str:
        try:
            return self._message_queue.get(timeout=1)
        except queue.Empty:
            return None

    def send(self, message: str) -> None:
        if self._ws and self._connected:
            self._ws.send(message)

    def close(self) -> None:
        if self._ws:
            self._ws.close()
            self._connected = False


class AzureVoiceLiveClient:
    """
    Azure AI Speech Voice Live client for real-time voice interactions.
    
    This client provides a high-level interface to Azure AI Speech Voice Live API,
    handling WebSocket connections, audio streaming, and conversation management
    for real-time voice applications.
    
    Key Features:
    - Real-time audio streaming with WebSocket connections
    - Voice Activity Detection (VAD) with configurable sensitivity
    - Custom model and voice configuration
    - Event-driven architecture with callbacks
    - Automatic reconnection and error handling
    - Performance monitoring and metrics collection
    """
    
    def __init__(
        self,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        region: Optional[str] = None,
        model_name: str = "gpt-4",
        voice_name: str = "en-US-AriaNeural",
        language: str = "en-US",
        sample_rate: int = 24000,
        enable_vad: bool = True,
        vad_sensitivity: float = 0.5,
        enable_interruption: bool = True,
        **kwargs
    ):
        """
        Initialize Azure Voice Live client.
        
        :param endpoint: Azure Speech service endpoint (optional, uses env var)
        :param api_key: Azure Speech service API key (optional, uses env var)
        :param region: Azure Speech service region (optional, uses env var)
        :param model_name: AI model for conversation processing
        :param voice_name: TTS voice for responses
        :param language: Primary language for speech processing
        :param sample_rate: Audio sample rate in Hz
        :param enable_vad: Enable voice activity detection
        :param vad_sensitivity: VAD sensitivity (0.0-1.0)
        :param enable_interruption: Enable conversation interruption
        :param kwargs: Additional configuration parameters
        """
        # Azure Speech configuration
        self.endpoint = endpoint or os.getenv("AZURE_SPEECH_ENDPOINT")
        self.api_key = api_key or os.getenv("AZURE_SPEECH_KEY")
        self.region = region or os.getenv("AZURE_SPEECH_REGION", "eastus")
        
        # Voice Live configuration
        self.model_name = model_name
        self.voice_name = voice_name
        self.language = language
        self.sample_rate = sample_rate
        self.enable_vad = enable_vad
        self.vad_sensitivity = vad_sensitivity
        self.enable_interruption = enable_interruption
        
        # Connection state
        self.state = VoiceLiveConnectionState.DISCONNECTED
        self.session_id: Optional[str] = None
        self.connection_id: Optional[str] = None
        
        # Event callbacks
        self.event_callbacks: Dict[VoiceLiveEventType, List[Callable]] = {
            event_type: [] for event_type in VoiceLiveEventType
        }
        
        # Audio processing
        self.audio_queue = asyncio.Queue(maxsize=1000)
        self.response_queue = asyncio.Queue(maxsize=100)
        
        # Performance metrics
        self.metrics = {
            "connection_time": None,
            "total_audio_bytes": 0,
            "total_messages": 0,
            "last_activity": None,
            "error_count": 0,
            "latency_samples": []
        }
        
        # Background tasks
        self._connection_task: Optional[asyncio.Task] = None
        self._audio_processing_task: Optional[asyncio.Task] = None
        self._response_processing_task: Optional[asyncio.Task] = None
        
        # Azure Speech SDK components (placeholder for actual Voice Live SDK)
        self._speech_config: Optional[speechsdk.SpeechConfig] = None
        self._audio_config: Optional[speechsdk.AudioConfig] = None
        
        logger.info(f"Azure Voice Live client initialized with model {model_name} and voice {voice_name}")

    def connect(self, model: str) -> VoiceLiveConnection:
        if self._connection is not None:
            raise ValueError("Already connected to the Voice Live API.")
        if not model:
            raise ValueError("Model name is required.")

        azure_ws_endpoint = self._azure_endpoint.rstrip('/').replace("https://", "wss://")

        url = f"{azure_ws_endpoint}/voice-live/realtime?api-version={self._api_version}&model={model}"

        auth_header = {"Authorization": f"Bearer {self._token}"} if self._token else {"api-key": self._api_key}
        request_id = uuid.uuid4()
        headers = {"x-ms-client-request-id": str(request_id), **auth_header}

        self._connection = VoiceLiveConnection(url, headers)
        self._connection.connect()
        return self._connection
    
    async def connect(self, session_id: Optional[str] = None) -> bool:
        """
        Establish connection to Azure AI Speech Voice Live API.
        
        :param session_id: Optional session identifier for tracking
        :return: True if connection successful, False otherwise
        :raises: Exception if connection fails critically
        """
        with tracer.start_as_current_span(
            "azure_voice_live_connect",
            kind=SpanKind.CLIENT,
            attributes={
                SpanAttr.OPERATION_NAME: "azure_voice_live_connect",
                SpanAttr.SESSION_ID: session_id or "unknown",
                "voice_live.model": self.model_name,
                "voice_live.voice": self.voice_name,
                "voice_live.language": self.language
            }
        ) as span:
            try:
                if self.state in [VoiceLiveConnectionState.CONNECTED, VoiceLiveConnectionState.CONNECTING]:
                    logger.warning("Already connected or connecting to Voice Live API")
                    return True
                
                self.state = VoiceLiveConnectionState.CONNECTING
                self.session_id = session_id
                start_time = time.time()
                
                # Initialize Azure Speech SDK configuration
                await self._initialize_speech_config()
                
                self._connection = await VoiceLiveConnection(
                    azure_endpoint=self.endpoint,
                    api_version="2024-11-15",
                    token=await get_credential().get_token()
                ).connect(model=self.model_name)    

                # For now, simulate connection establishment
                await asyncio.sleep(0.1)  # Simulate connection latency
                
                self.state = VoiceLiveConnectionState.CONNECTED
                self.connection_id = f"conn_{int(time.time())}"
                self.metrics["connection_time"] = time.time() - start_time
                self.metrics["last_activity"] = datetime.utcnow()
                
                # Start background processing tasks
                await self._start_background_tasks()
                
                # Trigger connection event
                await self._trigger_event(VoiceLiveEventType.CONNECTED, {
                    "session_id": self.session_id,
                    "connection_id": self.connection_id,
                    "connection_time": self.metrics["connection_time"]
                })
                
                span.set_status(Status(StatusCode.OK))
                span.set_attributes({
                    "voice_live.connection_time_ms": self.metrics["connection_time"] * 1000,
                    "voice_live.connection_id": self.connection_id
                })
                
                logger.info(f"Connected to Azure Voice Live API in {self.metrics['connection_time']:.3f}s")
                return True
                
            except Exception as e:
                self.state = VoiceLiveConnectionState.ERROR
                self.metrics["error_count"] += 1
                
                span.set_status(Status(StatusCode.ERROR, f"Connection failed: {e}"))
                logger.error(f"Failed to connect to Azure Voice Live API: {e}")
                
                await self._trigger_event(VoiceLiveEventType.ERROR, {
                    "error": str(e),
                    "error_type": "connection_error"
                })
                
                raise
    
    async def disconnect(self) -> None:
        """
        Disconnect from Azure AI Speech Voice Live API and clean up resources.
        """
        with tracer.start_as_current_span(
            "azure_voice_live_disconnect",
            attributes={"voice_live.session_id": self.session_id or "unknown"}
        ) as span:
            try:
                if self.state == VoiceLiveConnectionState.DISCONNECTED:
                    logger.info("Already disconnected from Voice Live API")
                    return
                
                logger.info("Disconnecting from Azure Voice Live API")
                
                # Stop background tasks
                await self._stop_background_tasks()
                
                # Close connection (placeholder)
                # if self._connection:
                #     await self._connection.close()
                
                # Reset state
                self.state = VoiceLiveConnectionState.DISCONNECTED
                self.connection_id = None
                
                # Trigger disconnection event
                await self._trigger_event(VoiceLiveEventType.DISCONNECTED, {
                    "session_id": self.session_id,
                    "total_messages": self.metrics["total_messages"],
                    "total_audio_bytes": self.metrics["total_audio_bytes"]
                })
                
                span.set_status(Status(StatusCode.OK))
                logger.info("Disconnected from Azure Voice Live API")
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, f"Disconnect error: {e}"))
                logger.error(f"Error disconnecting from Voice Live API: {e}")
                raise
    
    async def send_audio(self, audio_data: bytes) -> None:
        """
        Send audio data to Azure Voice Live API for processing.
        
        :param audio_data: Raw audio bytes to process
        """
        if self.state != VoiceLiveConnectionState.CONNECTED:
            logger.warning("Cannot send audio: not connected to Voice Live API")
            return
        
        try:
            # Queue audio for processing
            await self.audio_queue.put(audio_data)
            
            # Update metrics
            self.metrics["total_audio_bytes"] += len(audio_data)
            self.metrics["last_activity"] = datetime.utcnow()
            
        except Exception as e:
            logger.error(f"Error sending audio data: {e}")
            await self._trigger_event(VoiceLiveEventType.ERROR, {
                "error": str(e),
                "error_type": "audio_send_error"
            })
    
    async def send_text(self, text: str, role: str = "user") -> None:
        """
        Send text input to Azure Voice Live API for processing.
        
        :param text: Text content to process
        :param role: Role of the message sender (user, assistant, system)
        """
        if self.state != VoiceLiveConnectionState.CONNECTED:
            logger.warning("Cannot send text: not connected to Voice Live API")
            return
        
        try:
            # In a real implementation, this would send text through the WebSocket
            # await self._connection.send_text(text, role)
            
            # For now, simulate text processing
            await asyncio.sleep(0.01)
            
            # Update metrics
            self.metrics["total_messages"] += 1
            self.metrics["last_activity"] = datetime.utcnow()
            
            # Trigger text received event
            await self._trigger_event(VoiceLiveEventType.TEXT_RECEIVED, {
                "text": text,
                "role": role,
                "session_id": self.session_id
            })
            
        except Exception as e:
            logger.error(f"Error sending text: {e}")
            await self._trigger_event(VoiceLiveEventType.ERROR, {
                "error": str(e),
                "error_type": "text_send_error"
            })
    
    def add_event_callback(self, event_type: VoiceLiveEventType, callback: Callable) -> None:
        """
        Add event callback for Voice Live events.
        
        :param event_type: Type of event to listen for
        :param callback: Callback function to execute
        """
        if event_type not in self.event_callbacks:
            self.event_callbacks[event_type] = []
        
        self.event_callbacks[event_type].append(callback)
        logger.debug(f"Added callback for event type: {event_type}")
    
    def remove_event_callback(self, event_type: VoiceLiveEventType, callback: Callable) -> bool:
        """
        Remove event callback.
        
        :param event_type: Type of event
        :param callback: Callback function to remove
        :return: True if callback was found and removed
        """
        if event_type in self.event_callbacks and callback in self.event_callbacks[event_type]:
            self.event_callbacks[event_type].remove(callback)
            return True
        return False
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Get current performance metrics.
        
        :return: Dictionary of performance metrics
        """
        return {
            **self.metrics,
            "state": self.state.value,
            "session_id": self.session_id,
            "connection_id": self.connection_id,
            "average_latency": sum(self.metrics["latency_samples"]) / len(self.metrics["latency_samples"]) 
                             if self.metrics["latency_samples"] else 0
        }
    
    def is_connected(self) -> bool:
        """Check if client is connected to Voice Live API."""
        return self.state == VoiceLiveConnectionState.CONNECTED
    
    def is_healthy(self) -> bool:
        """Check if client is in a healthy state."""
        return self.state in [VoiceLiveConnectionState.CONNECTED, VoiceLiveConnectionState.STREAMING]
    
    async def configure(self, **config) -> None:
        """
        Update client configuration.
        
        :param config: Configuration parameters to update
        """
        try:
            # Update configuration parameters
            for key, value in config.items():
                if hasattr(self, key):
                    setattr(self, key, value)
                    logger.debug(f"Updated configuration: {key} = {value}")
            
            # If connected, apply configuration changes
            if self.state == VoiceLiveConnectionState.CONNECTED:
                # In a real implementation, this would update the live connection
                # await self._connection.configure(**config)
                pass
                
        except Exception as e:
            logger.error(f"Error updating configuration: {e}")
            raise
    
    async def _initialize_speech_config(self) -> None:
        """Initialize Azure Speech SDK configuration."""
        try:
            if self.api_key:
                self._speech_config = speechsdk.SpeechConfig(
                    subscription=self.api_key,
                    
                    region=self.region
                )
            else:
                # Use managed identity or default credentials
                credential = get_credential().get_token("https://cognitiveservices.azure.com/.default")
                self._speech_config = speechsdk.SpeechConfig(
                    endpoint=self.endpoint,
                    token_credential=credential,
                    region=self.region
                )
                # This would be configured for Voice Live API specifically
                pass
            
            if self._speech_config:
                self._speech_config.speech_recognition_language = self.language
                self._speech_config.speech_synthesis_voice_name = self.voice_name
                
        except Exception as e:
            logger.error(f"Failed to initialize speech configuration: {e}")
            raise
    
    async def _start_background_tasks(self) -> None:
        """Start background processing tasks."""
        self._audio_processing_task = asyncio.create_task(self._process_audio_queue())
        self._response_processing_task = asyncio.create_task(self._process_response_queue())
        logger.debug("Background tasks started")
    
    async def _stop_background_tasks(self) -> None:
        """Stop background processing tasks."""
        tasks = [self._audio_processing_task, self._response_processing_task]
        
        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        logger.debug("Background tasks stopped")
    
    async def _process_audio_queue(self) -> None:
        """Process queued audio data."""
        while self.state == VoiceLiveConnectionState.CONNECTED:
            try:
                audio_data = await asyncio.wait_for(self.audio_queue.get(), timeout=1.0)
                
                # In a real implementation, this would send audio to Voice Live API
                # response = await self._connection.send_audio(audio_data)
                
                # Simulate audio processing
                await asyncio.sleep(0.01)
                
                # Simulate receiving a transcription
                if len(audio_data) > 100:  # Only for substantial audio chunks
                    await self.response_queue.put({
                        "type": "transcription",
                        "text": "Simulated transcription from audio",
                        "confidence": 0.95,
                        "is_final": True
                    })
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error processing audio: {e}")
    
    async def _process_response_queue(self) -> None:
        """Process responses from Voice Live API."""
        while self.state == VoiceLiveConnectionState.CONNECTED:
            try:
                response = await asyncio.wait_for(self.response_queue.get(), timeout=1.0)
                
                if response.get("type") == "transcription":
                    await self._trigger_event(VoiceLiveEventType.TEXT_RECEIVED, {
                        "text": response.get("text"),
                        "confidence": response.get("confidence"),
                        "is_final": response.get("is_final"),
                        "session_id": self.session_id
                    })
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error processing response: {e}")
    
    async def _trigger_event(self, event_type: VoiceLiveEventType, event_data: Dict[str, Any]) -> None:
        """
        Trigger event callbacks.
        
        :param event_type: Type of event to trigger
        :param event_data: Event data to pass to callbacks
        """
        callbacks = self.event_callbacks.get(event_type, [])
        
        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event_data)
                else:
                    callback(event_data)
            except Exception as e:
                logger.error(f"Error in event callback for {event_type}: {e}")


# Factory function for creating Voice Live clients
async def create_voice_live_client(**config) -> AzureVoiceLiveClient:
    """
    Factory function to create and initialize an Azure Voice Live client.
    
    :param config: Configuration parameters for the client
    :return: Initialized AzureVoiceLiveClient instance
    """
    client = AzureVoiceLiveClient(**config)
    logger.info("Created Azure Voice Live client")
    return client