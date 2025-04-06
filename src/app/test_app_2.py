import asyncio
import os
import threading
import pyaudio
import numpy as np
import base64
import logging
import webrtcvad
from dotenv import load_dotenv

from src.realtime_client.client import RealtimeClient
from src.realtime_copy.tools import tools

# Load environment variables from .env file
load_dotenv()

# Config
CHUNK_SIZE = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000  # GPT-4o expects 16kHz PCM16
SPEAKER_BUFFER_MS = 300  # Small buffer for smoother playback

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VoiceAgent:
    def __init__(self, system_prompt: str) -> None:
        self.system_prompt = system_prompt
        self.client = RealtimeClient(system_prompt=self.system_prompt)
        self.audio_interface = pyaudio.PyAudio()
        self.running = False
        self.mic_stream = None
        self.speaker_stream = None
        self.loop = None
        self.vad = webrtcvad.Vad(3)  # Aggressiveness: 0-3

        # Buffer for speaker
        self.speaker_buffer = bytearray()

    async def setup(self) -> None:
        self.client.clear_event_handlers()

        self.client.realtime.on("server.response.audio.delta", self.on_audio_delta)
        self.client.realtime.on("server.error", self.on_error)

        for tool_def, tool_handler in tools:
            await self.client.add_tool(tool_def, tool_handler)

        await self.client.connect()

    async def start(self) -> None:
        await self.setup()

        self.running = True

        self.mic_stream = self.audio_interface.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE
        )

        self.speaker_stream = self.audio_interface.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            output=True
        )

        logger.info("AudioManager: Microphone and Speaker started.")

        try:
            while self.running:
                data = self.mic_stream.read(CHUNK_SIZE, exception_on_overflow=False)

                # VAD: send only if speech detected
                if self.vad.is_speech(data, RATE):
                    logger.info("🎙️ VAD detected speech, sending to GPT...")
                    np_data = np.frombuffer(data, dtype=np.int16)
                    await self.client.append_input_audio(np_data)
                else:
                    logger.debug("🤫 Silence detected, not sending.")

                # Speaker playback
                if len(self.speaker_buffer) >= int(RATE * 2 * (SPEAKER_BUFFER_MS / 1000)):  # 2 bytes per sample
                    chunk = self.speaker_buffer[:CHUNK_SIZE*2]  # Take a small chunk
                    self.speaker_buffer = self.speaker_buffer[CHUNK_SIZE*2:]
                    self.speaker_stream.write(chunk)

                await asyncio.sleep(0.01)

        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        finally:
            await self.stop()

    async def stop(self) -> None:
        if not self.running:
            return
        logger.info("Stopping VoiceAgent...")
        self.running = False

        try:
            if self.mic_stream:
                self.mic_stream.stop_stream()
                self.mic_stream.close()
            if self.speaker_stream:
                self.speaker_stream.stop_stream()
                self.speaker_stream.close()
            self.audio_interface.terminate()

            await self.client.create_response()
            await self.client.disconnect()
        except Exception as e:
            logger.error(f"Error during stop: {e}")

    async def on_audio_delta(self, event: dict) -> None:
        try:
            delta = event.get("delta")
            if delta:
                decoded_audio = base64.b64decode(delta)
                logger.info(f"🔈 Received {len(decoded_audio)} bytes of AI audio response.")
                self.speaker_buffer.extend(decoded_audio)  # Buffer it
        except Exception as e:
            logger.error(f"Error handling audio delta: {e}")

    async def on_error(self, event: dict) -> None:
        logger.error(f"Realtime API error: {event}")

def main():
    system_prompt = """Provide helpful and empathetic support responses to customer inquiries for ShopMe, addressing their requests, concerns, or feedback professionally.
Maintain a friendly and service-oriented tone throughout the interaction to ensure a positive customer experience.
    """

    agent = VoiceAgent(system_prompt=system_prompt)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def run():
        try:
            loop.run_until_complete(agent.start())
        except KeyboardInterrupt:
            pass
        finally:
            loop.run_until_complete(agent.stop())

    t = threading.Thread(target=run, daemon=True)
    t.start()

    try:
        while True:
            cmd = input("Type 'exit' to stop the agent: ")
            if cmd.lower() == 'exit':
                break
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(agent.stop())
        t.join()

if __name__ == "__main__":
    main()
