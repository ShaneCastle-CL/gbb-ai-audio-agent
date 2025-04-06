import asyncio
import threading
import logging
import base64
import numpy as np

from src.realtime_agent.audio_manager import AudioManager
from src.realtime.client import RealtimeClient
from src.realtime.tools import tools

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class WSManager:
    """
    Manages the full lifecycle of microphone input and realtime audio response.
    Connects AudioManager and RealtimeClient, streams mic input and plays AI output.
    """

    def __init__(self, system_prompt: str):
        self.audio_manager = AudioManager()
        self.realtime_client = RealtimeClient(system_prompt=system_prompt)
        self.running = False
        self.sender_thread = None
        self.loop = None
        self.audio_frame_counter = 0
        self.volume_threshold = 100  # Adjust this if you need (mic sensitivity)

    def start(self):
        """Start the audio manager and realtime client connection."""
        if self.running:
            logger.warning("WSManager already running.")
            return

        logger.info("Starting WSManager...")
        self.loop = asyncio.new_event_loop()
        self.sender_thread = threading.Thread(target=self._run, args=(self.loop,), daemon=True)
        self.sender_thread.start()
        self.running = True

    def _run(self, loop):
        """Run the async event loop inside a separate thread."""
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._start_runtime())

    async def _start_runtime(self):
        """Start the runtime: connect, setup, and stream audio."""
        await self.setup_realtime_client()
        await self.realtime_client.connect()
        await self.realtime_client.update_session()

        self.audio_manager.start()

        try:
            while self.audio_manager.is_active():
                mic_chunk = self.audio_manager.get_mic_chunk()
                if mic_chunk:
                    if isinstance(mic_chunk, bytearray):
                        mic_chunk = bytes(mic_chunk)

                    mic_array = np.frombuffer(mic_chunk, dtype=np.int16)
                    volume_norm = np.linalg.norm(mic_array) / len(mic_array)

                    if volume_norm > self.volume_threshold:
                        bar_length = int(min(volume_norm / 3000.0, 1.0) * 50)
                        print("\r🎙️ Mic Input: " + "█" * bar_length + " " * (50 - bar_length), end="", flush=True)

                        await self.realtime_client.append_input_audio(mic_array)
                        self.audio_frame_counter += 1

                await asyncio.sleep(0.01)

        except Exception as e:
            logger.exception(f"Error in streaming loop: {e}")
        finally:
            await self.realtime_client.disconnect()
            self.audio_manager.stop()

    async def setup_realtime_client(self):
        """Configure realtime client with event handlers and tools."""
        self.realtime_client.clear_event_handlers()
        self.realtime_client.tools = {}

        async def on_conversation_updated(event):
            pass  # Reserved for future enhancements

        async def on_audio_delta(event):
            try:
                delta = event.get("delta")
                if delta:
                    decoded_audio = base64.b64decode(delta)

                    if isinstance(decoded_audio, bytearray):
                        decoded_audio = bytes(decoded_audio)

                    logger.info("🔈 GPT sent AUDIO!")
                    logger.info(f"Size of decoded audio: {len(decoded_audio)} bytes")

                    self.audio_manager.append_to_speaker(decoded_audio)
                else:
                    logger.info("🛑 GPT sent NO audio back (empty delta).")
            except Exception as e:
                logger.error(f"Error handling audio delta: {e}")

        self.realtime_client.on("conversation.updated", on_conversation_updated)
        self.realtime_client.on("server.response.audio.delta", on_audio_delta)

        for tool_def, tool_handler in tools:
            await self.realtime_client.add_tool(tool_def, tool_handler)

    def stop(self):
        """Stop the connection and audio manager gracefully."""
        if not self.running:
            logger.warning("WSManager not running.")
            return

        logger.info("Stopping WSManager...")
        self.running = False

        self.audio_manager.stop()

        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

        if self.sender_thread:
            self.sender_thread.join()

        logger.info("WSManager stopped.")

    def is_running(self) -> bool:
        """Check if the WSManager is running."""
        return self.running
