import os
import azure.cognitiveservices.speech as speechsdk
from azure.cognitiveservices.speech.audio import AudioOutputConfig
from dotenv import load_dotenv
from utils.ml_logging import get_logger

logger = get_logger()
load_dotenv()

class SpeechSynthesizer:
    def __init__(self, key: str = None, region: str = None, language: str = "en-US", voice: str = "en-US-JennyMultilingualNeural"):
        self.key = key if key is not None else os.getenv("AZURE_SPEECH_KEY")
        self.region = region if region is not None else os.getenv("AZURE_SPEECH_REGION")
        self.language = language
        self.voice = voice  
        self.synthesizer = self.create_speech_components()

    def create_speech_components(self):
        speech_config = speechsdk.SpeechConfig(subscription=self.key, region=self.region)
        speech_config.speech_recognition_language = self.language

        audio_config = AudioOutputConfig(use_default_speaker=True)

        # Optionally, set a compressed audio format for faster synthesis.
        speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Riff48Khz16BitMonoPcm)

        print(f"Using voice: {self.voice}")
        # Ensure the voice is passed as a string.
        speech_config.speech_synthesis_voice_name = self.voice

        speech_synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
        return speech_synthesizer

    def start_speaking_text(self, text: str) -> None:
        try:
            logger.info(f"[🔊] Starting streaming speech synthesis for text: {text[:30]}...")
            self.synthesizer.start_speaking_text_async(text)
        except Exception as e:
            logger.error(f"[❗] Error starting streaming speech synthesis: {e}")

    def stop_speaking(self) -> None:
        try:
            logger.info("[🛑] Stopping speech synthesis...")
            self.synthesizer.stop_speaking_async()
        except Exception as e:
            logger.error(f"[❗] Error stopping speech synthesis: {e}")
