import os
import azure.cognitiveservices.speech as speechsdk
from azure.cognitiveservices.speech.audio import AudioOutputConfig
from dotenv import load_dotenv
from utils.ml_logging import get_logger

logger = get_logger()
load_dotenv()
class SpeechSynthesizer:
    def __init__(
        self,
        key: str = None,
        region: str = None,
        language: str = "en-US",
        voice: str = "en-US-JennyMultilingualNeural"
    ):
        self.key = key if key is not None else os.getenv("AZURE_SPEECH_KEY")
        self.region = region if region is not None else os.getenv("AZURE_SPEECH_REGION")
        self.language = language
        self.voice = voice
        self.speaker_synthesizer = self._create_speaker_synthesizer()

    def _create_speech_config(self):
        """Helper for building the base SpeechConfig each time."""
        speech_config = speechsdk.SpeechConfig(subscription=self.key, region=self.region)
        speech_config.speech_recognition_language = self.language
        speech_config.speech_synthesis_voice_name = self.voice
        # 48kHz 16-bit mono PCM WAV format
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff48Khz16BitMonoPcm
        )
        return speech_config

    def _create_speaker_synthesizer(self):
        """Create a synthesizer that plays audio on the server's default speaker."""
        speech_config = self._create_speech_config()
        audio_config = speechsdk.audio.AudioOutputConfig(use_default_speaker=True)
        return speechsdk.SpeechSynthesizer(speech_config, audio_config)

    def start_speaking_text(self, text: str) -> None:
        """Play audio on the server's default speaker asynchronously."""
        try:
            logger.info(f"[🔊] Speaking text (server speaker): {text[:30]}...")
            self.speaker_synthesizer.start_speaking_text_async(text)
        except Exception as e:
            logger.error(f"[❗] Error starting streaming speech synthesis: {e}")

    def stop_speaking(self) -> None:
        """Stops playback on the server’s speaker synthesizer."""
        try:
            logger.info("[🛑] Stopping speech synthesis on server speaker...")
            self.speaker_synthesizer.stop_speaking_async()
        except Exception as e:
            logger.error(f"[❗] Error stopping speech synthesis: {e}")

    def synthesize_speech_to_wav(self, text: str) -> bytes:
        """
        Synthesize speech in memory and return WAV bytes.
        Does NOT play on server speakers.
        """
        try:
            # 1) Make a config with the desired voice & format
            speech_config = self._create_speech_config()

            # 2) No AudioOutputConfig => Synthesis happens in memory
            inmem_synthesizer = speechsdk.SpeechSynthesizer(speech_config, audio_config=None)

            # 3) Perform the synthesis
            result = inmem_synthesizer.speak_text_async(text).get()
            
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                # 4) Convert result to an AudioDataStream so we can read the WAV bytes
                audio_data_stream = speechsdk.AudioDataStream(result)
                # 5) Grab all bytes
                audio_data = audio_data_stream.readall()
                return audio_data
            else:
                logger.error(f"Synthesis not completed. Reason: {result.reason}")
                if result.cancellation_details:
                    logger.error(f"Cancellation details: {result.cancellation_details}")
                return b""
        except Exception as e:
            logger.error(f"[❗] Error synthesizing speech in memory: {e}")
            return b""

