"""
AudioManager for capturing microphone input and playing speaker output
in real-time, chunk-by-chunk.

This module is independent and ready to plug into RealtimeClient.
"""

import threading
import queue
import time
import logging
import pyaudio

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

class AudioManager:
    def __init__(
        self,
        rate: int = 24000,
        chunk_size: int = 1024,
        format_type = pyaudio.paInt16,
    ):
        self.rate = rate
        self.chunk_size = chunk_size
        self.format_type = format_type
        self.channels = 1

        self.audio = pyaudio.PyAudio()
        self.mic_stream = None
        self.speaker_stream = None

        self.mic_queue = queue.Queue()
        self.audio_buffer = bytearray()

        self.running = False
        self._lock = threading.Lock()

    def _mic_callback(self, in_data, frame_count, time_info, status):
        """
        Callback for microphone input stream.
        """
        self.mic_queue.put(in_data)
        return (None, pyaudio.paContinue)

    def _speaker_callback(self, in_data, frame_count, time_info, status):
        """
        Callback for speaker output stream.
        """
        bytes_needed = frame_count * 2  # because int16
        with self._lock:
            if len(self.audio_buffer) >= bytes_needed:
                out_data = self.audio_buffer[:bytes_needed]
                self.audio_buffer = self.audio_buffer[bytes_needed:]
            else:
                out_data = bytes(self.audio_buffer) + b'\x00' * (bytes_needed - len(self.audio_buffer))
                self.audio_buffer.clear()
        return (out_data, pyaudio.paContinue)

    def start(self):
        """
        Start the microphone and speaker streams.
        """
        self.running = True

        self.mic_stream = self.audio.open(
            format=self.format_type,
            channels=self.channels,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunk_size,
            stream_callback=self._mic_callback,
        )

        self.speaker_stream = self.audio.open(
            format=self.format_type,
            channels=self.channels,
            rate=self.rate,
            output=True,
            frames_per_buffer=self.chunk_size,
            stream_callback=self._speaker_callback,
        )

        self.mic_stream.start_stream()
        self.speaker_stream.start_stream()

        logger.info("AudioManager: Microphone and Speaker streams started.")

    def stop(self):
        """
        Stop the audio streams gracefully.
        """
        self.running = False

        if self.mic_stream is not None:
            self.mic_stream.stop_stream()
            self.mic_stream.close()

        if self.speaker_stream is not None:
            self.speaker_stream.stop_stream()
            self.speaker_stream.close()

        self.audio.terminate()

        logger.info("AudioManager: Streams stopped and resources released.")

    def get_mic_chunk(self) -> bytes:
        """
        Retrieve the next audio chunk recorded from the microphone.
        """
        if not self.mic_queue.empty():
            return self.mic_queue.get()
        return None

    def append_to_speaker(self, audio_chunk: bytes):
        """
        Append audio data to speaker buffer to be played out.
        """
        with self._lock:
            self.audio_buffer.extend(audio_chunk)

    def is_active(self) -> bool:
        """
        Check if streams are active.
        """
        return self.running

