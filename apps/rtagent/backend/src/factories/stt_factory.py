from __future__ import annotations
"""Factory for creating per-connection StreamingSpeechRecognizerFromBytes instances.

Centralizes configuration so that all websocket handlers use identical
parameters (mirrors the old singleton construction in main.py) while
avoiding shared mutable recognizer state.
"""
from apps.rtagent.backend.settings import (
    VAD_SEMANTIC_SEGMENTATION,
    SILENCE_DURATION_MS,
    RECOGNIZED_LANGUAGE,
    AUDIO_FORMAT,
)
from src.speech.speech_recognizer import StreamingSpeechRecognizerFromBytes

def create_stt_recognizer() -> StreamingSpeechRecognizerFromBytes:
    return StreamingSpeechRecognizerFromBytes(
        use_semantic_segmentation=VAD_SEMANTIC_SEGMENTATION,
        vad_silence_timeout_ms=SILENCE_DURATION_MS,
        candidate_languages=RECOGNIZED_LANGUAGE,
        audio_format=AUDIO_FORMAT,
    )
