import asyncio
import logging

from aiohttp import web
from azure.communication.callautomation import (
    CallAutomationClient,
    CallConnectionClient,
    PhoneNumberIdentifier,
    SsmlSource,
    TextSource,
    TranscriptionOptions, 
    TranscriptionTransportType,
    RecordingChannel,
    RecordingContent,
    RecordingFormat,
    AzureBlobContainerRecordingStorage
)

from azure.core.exceptions import HttpResponseError
from azure.core.messaging import CloudEvent
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)


class AcsCaller:
    def __init__(
        self,
        source_number: str,
        callback_url: str,
        recording_callback_url: str = None,  # Optional, if using a specific callback URL for recording
        websocket_url: str = None,
        acs_connection_string: str = None,
        acs_endpoint: str = None,  # Optional, if using a specific ACS endpoint
        cognitive_services_endpoint: str = None,  # Optional, if using TTS/STT
        speech_recognition_model_endpoint_id: str = None,  # Optional, for custom speech models
        recording_configuration: dict = None,  # Optional, for recording settings
        recording_storage_container_url: str = None,  # Optional, for recording storage
    ):
        # Required
        if not (acs_connection_string or acs_endpoint):
            raise ValueError("Provide either acs_connection_string or acs_endpoint")

        self.source_number = source_number
        self.callback_url = callback_url
        self.cognitive_services_endpoint = cognitive_services_endpoint
        self.speech_recognition_model_endpoint_id = speech_recognition_model_endpoint_id

        # Recording Settings
        if not recording_callback_url: 
            recording_callback_url = callback_url
        self.recording_callback_url = recording_callback_url
        self.recording_configuration = recording_configuration or {}
        self.recording_storage_container_url = recording_storage_container_url

        # Live Transcription Settings (ACS <--> STT/TTS)
        self.transcription_opts = (
            TranscriptionOptions(
                transport_url=websocket_url,
                transport_type=TranscriptionTransportType.WEBSOCKET,
                locale="en-US",
                start_transcription=True,
                enable_intermediate_results=True,
            )
            if websocket_url
            else None
        )

        # build the ACS client
        self.client = (
            CallAutomationClient.from_connection_string(acs_connection_string)
            if acs_connection_string
            else CallAutomationClient(endpoint=acs_endpoint, credential=DefaultAzureCredential())
        )
        logger.info("AcsCaller initialized")

    async def initiate_call(self, target_number: str) -> dict:
        """Start a new call with live transcription over websocket."""
        call = self.client
        src = PhoneNumberIdentifier(self.source_number)
        dest = PhoneNumberIdentifier(target_number)

        try:
            logger.debug("Creating call to %s via callback %s", target_number, self.callback_url)
            result = call.create_call(
                target_participant=dest,
                source_caller_id_number=src,
                callback_url=self.callback_url,
                cognitive_services_endpoint=self.cognitive_services_endpoint,
                transcription=self.transcription_opts,
            )
            logger.info("Call created: %s", result.call_connection_id)
            return {"status": "created", "call_id": result.call_connection_id}

        except HttpResponseError as e:
            logger.error("ACS call failed [%s]: %s", e.status_code, e.message)
            raise
        except Exception:
            logger.exception("Unexpected error in initiate_call")
            raise

    def get_call_connection(self, call_connection_id: str) -> CallConnectionClient:
        """
        Retrieve the CallConnectionClient for the given call_connection_id.
        """
        try:
            return self.client.get_call_connection(call_connection_id)
        except Exception as e:
            logger.error(f"Error retrieving CallConnectionClient: {e}", exc_info=True)
            return None

    def start_recording(self, server_call_id: str):
        """
        Start recording the call.
        """
        try:
            self.client.start_recording(
                server_call_id=server_call_id,
                recording_state_callback_url=self.recording_callback_url,
                recording_content_type=RecordingContent.AUDIO,
                recording_channel_type=RecordingChannel.UNMIXED,
                recording_format_type=RecordingFormat.WAV,
                recording_storage=AzureBlobContainerRecordingStorage(
                    container_url=self.recording_storage_container_url,
                ),

            )
            logger.info(f"🎤 Started recording for call {server_call_id}")
        except Exception as e:
            logger.error(f"Error starting recording for call {server_call_id}: {e}")

    def stop_recording(self, server_call_id: str):
        """
        Stop recording the call.
        """
        try:
            self.client.stop_recording(server_call_id=server_call_id)
            logger.info(f"🎤 Stopped recording for call {server_call_id}")
        except Exception as e:
            logger.error(f"Error stopping recording for call {server_call_id}: {e}")

