import logging
import asyncio
from azure.core.exceptions import HttpResponseError

from aiohttp import web
from azure.core.messaging import CloudEvent
from azure.communication.callautomation import (
    CallAutomationClient,
    CallInvite,
    PhoneNumberIdentifier,
    MediaStreamingOptions,
    MediaStreamingTransportType,
    MediaStreamingContentType,
    MediaStreamingAudioChannelType,
    AudioFormat,
    TextSource, 
    SsmlSource
)

logger = logging.getLogger(__name__)

class AcsCaller:
    source_number: str
    acs_connection_string: str
    acs_callback_path: str
    websocket_url: str
    media_streaming_configuration: MediaStreamingOptions
    call_automation_client: CallAutomationClient

    def __init__(
            self, 
            source_number:str, 
            acs_connection_string: str, 
            acs_callback_path: str, 
            acs_media_streaming_websocket_path: str, 
            # tts_translator: SpeechCoreTranslator
            ):
        self.source_number = source_number
        self.acs_connection_string = acs_connection_string
        self.acs_callback_path = acs_callback_path # Should be the full URL
        self.websocket_url = acs_media_streaming_websocket_path # Should be the full wss:// URL
        logger.info(f"AcsCaller initialized. Callback URL: {self.acs_callback_path}, WebSocket URL: {self.websocket_url}")
        self.media_streaming_configuration = MediaStreamingOptions(
            transport_url=self.websocket_url, # Use the full websocket URL
            transport_type=MediaStreamingTransportType.WEBSOCKET,
            content_type=MediaStreamingContentType.AUDIO,
            audio_channel_type=MediaStreamingAudioChannelType.UNMIXED,
            start_media_streaming=True,
            enable_bidirectional=True, # Important for sending audio back if needed
            audio_format=AudioFormat.PCM16_K_MONO # Ensure this matches what your STT expects
        )
        # self.translator = tts_translator

        # Initialize CallAutomationClient here to reuse it
        try:
            # self.call_automation_client = CallAutomationClient.from_connection_string(self.acs_connection_string)
            self.call_automation_client = CallAutomationClient.from_connection_string(self.acs_connection_string)
            logger.info("CallAutomationClient initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize CallAutomationClient: {e}", exc_info=True)
            self.call_automation_client = None # Ensure it's None if init fails

    def initiate_call(self, target_number: str):
        if not self.call_automation_client:
             logger.error("CallAutomationClient not initialized. Cannot initiate call.")
             raise RuntimeError("CallAutomationClient failed to initialize.") # Or handle appropriately

        try:
            # Ensure target and source are correctly formatted identifiers
            self.target_participant = PhoneNumberIdentifier(target_number)
            self.source_caller = PhoneNumberIdentifier(self.source_number)

            # Log the exact parameters being used for the call
            logger.info(f"Initiating call to: {target_number}")
            logger.info(f"Source phone number: {self.source_number}")
            logger.info(f"Callback URI for ACS events: {self.acs_callback_path}")
            logger.info(f"Media Streaming WebSocket URI: {self.websocket_url}")
            logger.info(f"Media Streaming Configuration: {self.media_streaming_configuration}")

            CallInvite(target=self.target_participant, source_caller_id_number=self.source_caller) 
            response = self.call_automation_client.create_call(
                target_participant=self.target_participant,
                callback_url=self.acs_callback_path, # Pass the full callback URL
                media_streaming=self.media_streaming_configuration,
                source_caller_id_number=self.source_caller,
                cognitive_services_endpoint='https://eus2aigatewaytestnyw255pldeos4.cognitiveservices.azure.com/'
            )
            # Note: create_call is sync. Response contains call_connection_properties like callConnectionId if successful immediately,
            # but the actual connection state comes via callbacks.
            call_connection_id = response.call_connection_id
            logger.info(f"create_call request sent successfully. Call Connection ID (initial): {call_connection_id}")
            return web.Response(status=200, text=f"Call initiated successfully. Call Connection ID: {call_connection_id}")
        except HttpResponseError as e:
            # Log detailed error information from ACS
            logger.error(f"ACS HTTP Error creating call: Status Code={e.status_code}, Reason={e.reason}, Message={e.message}", exc_info=True)
            # Consider re-raising or handling specific error codes (e.g., 400 for bad request, 401/403 for auth, 500 for server error)
            raise # Re-raise the exception to be handled by the caller API endpoint
        except Exception as e:
            logger.error(f"An unexpected error occurred during initiate_call: {e}", exc_info=True)
            raise # Re-raise the exception

    async def outbound_call_handler(self, request):
        cloudevent = await request.json() 
        for event_dict in cloudevent:
            event = CloudEvent.from_dict(event_dict)
            if event.data is None:
                continue
                
            call_connection_id = event.data['callConnectionId']
            print(f"{event.type} event received for call connection id: {call_connection_id}")

            if event.type == "Microsoft.Communication.CallConnected":
                print("Call connected")            

        return web.Response(status=200)

    def get_call_connection(self, call_connection_id: str):
        """
        Retrieve the call connection details using the call connection ID.
        """
        try:
            call_connection = self.call_automation_client.get_call_connection(call_connection_id)
            return call_connection
        except Exception as e:
            logger.error(f"Error retrieving call connection: {e}", exc_info=True)
            return None
        
    async def play_agent_tts(self, call_connection_id: str, text: str):
        call_conn = self.call_automation_client.get_call_connection(call_connection_id)
        tts = TextSource(text=text, voice_name="en-US-JennyNeural")
        # Always use the interrupt flag to preempt any ongoing media operation
        await call_conn.play_media_to_all(
            play_source=tts,
            loop=False,
            interrupt_call_media_operation=True
        )

    def play_response(
            self, 
            call_connection_id: str, 
            response_text: str, 
            use_ssml: bool = False, 
            voice_name: str = "en-US-JennyMultilingualNeural",
            locale: str = "en-US"
            ):
        """
        Plays `response_text` into the given ACS call, using the SpeechConfig
        :param call_connection_id: ACS callConnectionId
        :param response_text:      Plain text or SSML to speak
        :param use_ssml:           If True, wrap in SsmlSource; otherwise TextSource
        """
        # 2) Build the Source with the same settings
        if use_ssml:
            # Assume response_text is a full SSML document
            source = SsmlSource(ssml_text=response_text)
        else:
            source = TextSource(
                text=response_text,
                voice_name=voice_name,
                source_locale=locale
            )

        # 3) Get the call-specific client and play the prompt
        call_conn = self.call_automation_client.get_call_connection(call_connection_id)
        # 2. In `async def play_response`, before obtaining `call_conn`, insert:
        self.call_automation_client.get_call_connection(call_connection_id).cancel_all_media_operations()
        # Do not call sync cancel_all_media_operations; rely on interrupt_call_media_operation
        call_conn.play_media(play_source=source, loop=False, interrupt_call_media_operation=True)