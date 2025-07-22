import os
from typing import Callable, Optional, List

import azure.cognitiveservices.speech as speechsdk
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from utils.ml_logging import get_logger
import json

# OpenTelemetry imports for tracing
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

# Import centralized span attributes enum
from src.enums.monitoring import SpanAttr

# Set up logger
logger = get_logger()

# Load environment variables from .env file
load_dotenv()

class StreamingSpeechRecognizerFromBytes:
    """
    Real-time streaming speech recognizer using Azure Speech SDK with PushAudioInputStream.
    
    Authentication:
    - If key is provided: Uses API key authentication
    - If key is None: Uses Azure Default Credentials (managed identity, service principal, etc.)
    
    Supports:
    - PCM 16kHz 16-bit mono audio in bytes
    - Compressed audio (webm, mp3, ogg) via GStreamer
    - Auto language detection
    - Real-time callbacks for partial and final recognition
    - Azure Monitor OpenTelemetry tracing with call correlation
    """

    def __init__(
        self,
        *,
        key: Optional[str] = None,
        region: Optional[str] = None,
        candidate_languages: Optional[List[str]] = None,
        vad_silence_timeout_ms: int = 800,
        audio_format: str = "pcm",  # "pcm" or "any"
        call_connection_id: Optional[str] = None,
        enable_tracing: bool = True,
    ):
        """
        Initialize the StreamingSpeechRecognizerFromBytes.
        
        Args:
            key: Azure Speech API key. If None, will use Azure Default Credentials
            region: Azure region (required for both API key and credential authentication)
            candidate_languages: List of language codes for auto-detection
            vad_silence_timeout_ms: Voice activity detection silence timeout
            audio_format: "pcm" for raw PCM audio or "any" for compressed formats
            call_connection_id: Call connection ID for correlation in Azure Monitor
            enable_tracing: Whether to enable Azure Monitor tracing
        """
        self.key = key or os.getenv("AZURE_SPEECH_KEY")
        self.region = region or os.getenv("AZURE_SPEECH_REGION")
        self.candidate_languages = candidate_languages or ["en-US", "es-ES", "fr-FR"]
        self.vad_silence_timeout_ms = vad_silence_timeout_ms
        self.audio_format = audio_format  # either "pcm" or "any"
        self.call_connection_id = call_connection_id or "unknown"
        self.enable_tracing = enable_tracing

        self.final_callback: Optional[Callable[[str, str], None]] = None
        self.partial_callback: Optional[Callable[[str, str], None]] = None
        self.cancel_callback: Optional[Callable[[speechsdk.SessionEventArgs], None]] = None

        self.push_stream = None
        self.speech_recognizer = None
        
        # Initialize tracing
        self.tracer = None
        self._session_span = None
        if self.enable_tracing:
            try:
                # Initialize Azure Monitor if not already done
                # init_logging_and_monitoring("speech_recognizer")
                self.tracer = trace.get_tracer(__name__)
                logger.info("Azure Monitor tracing initialized for speech recognizer")
            except Exception as e:
                logger.warning(f"Failed to initialize Azure Monitor tracing: {e}")
                self.enable_tracing = False
    def set_call_connection_id(self, call_connection_id: str) -> None:
        """
        Set the call connection ID for correlation in tracing and logging.
        """
        self.call_connection_id = call_connection_id
    def _create_speech_config(self) -> speechsdk.SpeechConfig:
        """
        Create SpeechConfig using either API key or Azure Default Credentials
        Following Azure best practices for authentication
        """
        if self.key:
            # Use API key authentication if provided
            logger.info("Creating SpeechConfig with API key authentication")
            return speechsdk.SpeechConfig(subscription=self.key, region=self.region)
        else:
            # Use Azure Default Credentials (managed identity, service principal, etc.)
            logger.info("Creating SpeechConfig with Azure Default Credentials")
            if not self.region:
                raise ValueError("Region must be specified when using Azure Default Credentials")

            endpoint = os.getenv("AZURE_SPEECH_ENDPOINT")
            credential = DefaultAzureCredential()

            if endpoint:
                # Use endpoint if provided
                speech_config = speechsdk.SpeechConfig(endpoint=endpoint)
            else:
                speech_config = speechsdk.SpeechConfig(region=self.region)

            # Set the authorization token
            try:
                # Get token for Cognitive Services scope
                token_result = credential.get_token("https://cognitiveservices.azure.com/.default")
                speech_config.authorization_token = token_result.token
                logger.info("Successfully configured SpeechConfig with Azure Default Credentials")
            except Exception as e:
                logger.error(f"Failed to get Azure token: {e}")
                raise ValueError(f"Failed to authenticate with Azure Default Credentials: {e}")

            return speech_config

    def set_partial_result_callback(self,  callback: Callable[[str, str], None]) -> None:
        self.partial_callback = callback

    def set_final_result_callback(self, callback: Callable[[str, str], None]) -> None:
        self.final_callback = callback

    def set_cancel_callback(self, callback: Callable[[speechsdk.SessionEventArgs], None]) -> None:
        """
        Set a callback to handle cancellation events.
        This can be used to log or handle errors when recognition is canceled.
        """
        self.cancel_callback = callback

    def prepare_stream(self) -> None:
        """
        Prepare the audio stream for recognition.
        This method initializes the PushAudioInputStream based on the specified audio format.
        """
        if self.audio_format == "pcm":
            stream_format = speechsdk.audio.AudioStreamFormat(
                samples_per_second=16000,
                bits_per_sample=16,
                channels=1
            )
        elif self.audio_format == "any":
            stream_format = speechsdk.audio.AudioStreamFormat(
                compressed_stream_format=speechsdk.AudioStreamContainerFormat.ANY
            )
        else:
            raise ValueError(f"Unsupported audio_format: {self.audio_format}")

        self.push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)

    def start(self) -> None:
        """Start speech recognition with Azure Monitor tracing"""
        if self.enable_tracing and self.tracer:
            # Start a session-level span for the entire speech recognition session
            self._session_span = self.tracer.start_span(
                "speech_recognition_session",
                kind=SpanKind.CLIENT
            )
            
            # Set session attributes for correlation
            self._session_span.set_attribute("ai.operation.id", self.call_connection_id)
            self._session_span.set_attribute("speech.session.id", self.call_connection_id)
            self._session_span.set_attribute("speech.region", self.region)
            self._session_span.set_attribute("speech.audio_format", self.audio_format)
            self._session_span.set_attribute("speech.candidate_languages", ",".join(self.candidate_languages))
            self._session_span.set_attribute("speech.vad_timeout_ms", self.vad_silence_timeout_ms)
            
            # Set standard attributes if available
            self._session_span.set_attribute(SpanAttr.SERVICE_NAME, "azure-speech-recognition")
            self._session_span.set_attribute(SpanAttr.SERVICE_VERSION, "1.0.0")
            
            # Make this span current for the duration of setup
            with trace.use_span(self._session_span):
                self._start_recognition()
        else:
            self._start_recognition()

    def _start_recognition(self) -> None:
        """Internal method to start recognition"""
        logger.info("Starting recognition from byte stream...")

        # Create speech config with proper authentication
        speech_config = self._create_speech_config()

        # switch to continuous LID mode
        speech_config.set_property(
            speechsdk.PropertyId.SpeechServiceConnection_LanguageIdMode, "Continuous"
        )
        lid_cfg = speechsdk.languageconfig.AutoDetectSourceLanguageConfig(
            languages=self.candidate_languages
        )

        speech_config.set_property(
            speechsdk.PropertyId.SpeechServiceResponse_StablePartialResultThreshold,
            "1"
        )

        # PCM format: for raw PCM/linear audio
        if self.audio_format == "pcm":
            stream_format = speechsdk.audio.AudioStreamFormat(
                samples_per_second=16000,
                bits_per_sample=16,
                channels=1
            )
        # ANY format: for browser/native/mobile compressed formats (webm, ogg, mp3, etc)
        elif self.audio_format == "any":
            stream_format = speechsdk.audio.AudioStreamFormat(
                compressed_stream_format=speechsdk.AudioStreamContainerFormat.ANY
            )
        else:
            raise ValueError(f"Unsupported audio_format: {self.audio_format}")

        self.push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
        audio_config = speechsdk.audio.AudioConfig(stream=self.push_stream)

        self.speech_recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
            auto_detect_source_language_config=lid_cfg
        )

        self.speech_recognizer.properties.set_property(
            speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs,
            str(self.vad_silence_timeout_ms)
        )
        # self.speech_recognizer.properties.set_property(
        #     speechsdk.PropertyId.Speech_SegmentationStrategy, "Semantic"
        # )

        if self.partial_callback:
            self.speech_recognizer.recognizing.connect(self._on_recognizing)
        if self.final_callback:
            self.speech_recognizer.recognized.connect(self._on_recognized)
        if self.cancel_callback:
            self.speech_recognizer.canceled.connect(self.cancel_callback)

        self.speech_recognizer.canceled.connect(self._on_canceled)
        self.speech_recognizer.session_stopped.connect(self._on_session_stopped)

        self.speech_recognizer.start_continuous_recognition_async().get()
        # self.speech_recognizer.start_continuous_recognition()
        logger.info("Recognition started.")
        
        # Add event to session span if tracing is enabled
        if self._session_span:
            self._session_span.add_event("speech_recognition_started")

    def prepare_start(self) -> None:
        logger.info("Starting recognition from byte stream...")

        # Create speech config with proper authentication
        speech_config = self._create_speech_config()

        # switch to continuous LID mode
        speech_config.set_property(
            speechsdk.PropertyId.SpeechServiceConnection_LanguageIdMode, "Continuous"
        )
        lid_cfg = speechsdk.languageconfig.AutoDetectSourceLanguageConfig(
            languages=self.candidate_languages
        )

        speech_config.set_property(
            speechsdk.PropertyId.SpeechServiceResponse_StablePartialResultThreshold,
            "1"
        )

        # PCM format: for raw PCM/linear audio
        if self.audio_format == "pcm":
            stream_format = speechsdk.audio.AudioStreamFormat(
                samples_per_second=16000,
                bits_per_sample=16,
                channels=1
            )
        # ANY format: for browser/native/mobile compressed formats (webm, ogg, mp3, etc)
        elif self.audio_format == "any":
            stream_format = speechsdk.audio.AudioStreamFormat(
                compressed_stream_format=speechsdk.AudioStreamContainerFormat.ANY
            )
        else:
            raise ValueError(f"Unsupported audio_format: {self.audio_format}")

        self.push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
        audio_config = speechsdk.audio.AudioConfig(stream=self.push_stream)

        self.speech_recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
            auto_detect_source_language_config=lid_cfg
        )

        self.speech_recognizer.properties.set_property(
            speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs,
            str(self.vad_silence_timeout_ms)
        )
        # self.speech_recognizer.properties.set_property(
        #     speechsdk.PropertyId.Speech_SegmentationStrategy, "Semantic"
        # )

        if self.partial_callback:
            self.speech_recognizer.recognizing.connect(self._on_recognizing)
        if self.final_callback:
            self.speech_recognizer.recognized.connect(self._on_recognized)
        if self.cancel_callback:
            self.speech_recognizer.canceled.connect(self.cancel_callback)

        self.speech_recognizer.canceled.connect(self._on_canceled)
        self.speech_recognizer.session_stopped.connect(self._on_session_stopped)

        logger.info("Recognition ready to start.")

    def write_bytes(self, audio_chunk: bytes) -> None:
        """Write audio bytes to the stream with optional tracing"""
        if self.push_stream:
            # Add tracing for audio data flow if enabled
            if self.enable_tracing and self.tracer:
                with self.tracer.start_as_current_span(
                    "speech_audio_write",
                    kind=SpanKind.CLIENT,
                    attributes={
                        "speech.audio.chunk_size": len(audio_chunk),
                        "speech.session.id": self.call_connection_id,
                        "ai.operation.id": self.call_connection_id,
                    }
                ):
                    self.push_stream.write(audio_chunk)
            else:
                self.push_stream.write(audio_chunk)

    def stop(self) -> None:
        """Stop recognition with tracing cleanup"""
        if self.speech_recognizer:
            # Add event to session span before stopping
            if self._session_span:
                self._session_span.add_event("speech_recognition_stopping")
            
            self.speech_recognizer.stop_continuous_recognition_async().get()
            logger.info("Recognition stopped.")
            
            # Finish session span if it's still active
            if self._session_span:
                self._session_span.add_event("speech_recognition_stopped")
                self._session_span.set_status(Status(StatusCode.OK, "Recognition stopped"))
                self._session_span.end()
                self._session_span = None

    def close_stream(self) -> None:
        """Close the audio stream with tracing"""
        if self.push_stream:
            # Add event to session span before closing
            if self._session_span:
                self._session_span.add_event("audio_stream_closing")
            
            self.push_stream.close()
            
            # Final cleanup of session span if still active
            if self._session_span:
                self._session_span.add_event("audio_stream_closed")
                self._session_span.end()
                self._session_span = None

    @staticmethod
    def _extract_lang(evt) -> str:
        """
        Return detected language code regardless of LID mode.

        Priority:
        1. evt.result.language   (direct field, works in Continuous)
        2. AutoDetectSourceLanguageResult property
        3. fallback ''  (caller will switch to default)
        """
        if getattr(evt.result, "language", None):
            return evt.result.language

        prop = evt.result.properties.get(
            speechsdk.PropertyId.SpeechServiceConnection_AutoDetectSourceLanguageResult,
            "")
        if prop:
            return prop

        return ""

    # callbacks → wrap user callbacks with tracing
    def _on_recognizing(self, evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        """Handle partial recognition results with tracing"""
        txt = evt.result.text
        if txt and self.partial_callback:
            # Create a span for partial recognition
            if self.enable_tracing and self.tracer:
                with self.tracer.start_as_current_span(
                    "speech_partial_recognition",
                    kind=SpanKind.CLIENT,
                    attributes={
                        "speech.result.type": "partial",
                        "speech.result.text_length": len(txt),
                        "speech.session.id": self.call_connection_id,
                        "ai.operation.id": self.call_connection_id,
                    }
                ) as span:
                    # extract whatever lang Azure selected (or fallback to first candidate)
                    detected = speechsdk.AutoDetectSourceLanguageResult(evt.result).language \
                               or self.candidate_languages[0]
                    span.set_attribute("speech.detected_language", detected)
                    
                    # Add event to session span
                    if self._session_span:
                        self._session_span.add_event("partial_recognition_received", {
                            "text_length": len(txt),
                            "detected_language": detected
                        })
                    
            self.partial_callback(txt, detected)
        else:
            # extract whatever lang Azure selected (or fallback to first candidate)
            detected = speechsdk.AutoDetectSourceLanguageResult(evt.result).language \
                        or self.candidate_languages[0]
            self.partial_callback(txt, detected)

    def _on_recognized(self, evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        """Handle final recognition results with tracing"""
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            detected_lang = speechsdk.AutoDetectSourceLanguageResult(evt.result).language
            
            if self.enable_tracing and self.tracer and evt.result.text:
                with self.tracer.start_as_current_span(
                    "speech_final_recognition",
                    kind=SpanKind.CLIENT,
                    attributes={
                        "speech.result.type": "final",
                        "speech.result.text_length": len(evt.result.text),
                        "speech.detected_language": detected_lang,
                        "speech.session.id": self.call_connection_id,
                        "ai.operation.id": self.call_connection_id,
                        "speech.result.reason": str(evt.result.reason),
                    }
                ) as span:
                    # Add event to session span
                    if self._session_span:
                        self._session_span.add_event("final_recognition_received", {
                            "text_length": len(evt.result.text),
                            "detected_language": detected_lang,
                            "text_preview": evt.result.text[:50] + "..." if len(evt.result.text) > 50 else evt.result.text
                        })
                    
                    if self.final_callback:
                        self.final_callback(evt.result.text, detected_lang)
            elif self.final_callback and evt.result.text:
                self.final_callback(evt.result.text, detected_lang)

    def _on_canceled(self, evt: speechsdk.SessionEventArgs) -> None:
        """Handle cancellation events with tracing"""
        logger.warning("Recognition canceled: %s", evt)
        
        # Add error event to session span
        if self._session_span:
            self._session_span.set_status(Status(StatusCode.ERROR, "Recognition canceled"))
            self._session_span.add_event("recognition_canceled", {
                "event_details": str(evt)
            })
        
        if evt.result and evt.result.cancellation_details:
            details = evt.result.cancellation_details
            error_msg = f"Reason: {details.reason}, Error: {details.error_details}"
            logger.warning(error_msg)
            
            # Add detailed error information to span
            if self._session_span:
                self._session_span.add_event("cancellation_details", {
                    "cancellation_reason": str(details.reason),
                    "error_details": details.error_details
                })

    def _on_session_stopped(self, evt: speechsdk.SessionEventArgs) -> None:
        """Handle session stopped events with tracing"""
        logger.info("Session stopped.")
        
        # Add event to session span and finish it
        if self._session_span:
            self._session_span.add_event("speech_session_stopped")
            self._session_span.set_status(Status(StatusCode.OK, "Session completed"))
            self._session_span.end()
            self._session_span = None
