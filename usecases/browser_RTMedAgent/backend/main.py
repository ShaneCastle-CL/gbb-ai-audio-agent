"""
voice_agent.main
================
Entrypoint that stitches everything together:

• config / CORS
• shared objects on `app.state`  (Speech, Redis, ACS, TTS, dashboard-clients)
• route registration (routers package)
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from utils.ml_logging import get_logger
import os
from settings import ALLOWED_ORIGINS, AOAI_STT_KEY, AOAI_STT_ENDPOINT
from services.speech_services import SpeechSynthesizer, SpeechCoreTranslator
from services.acs_caller import initialize_acs_caller_instance
from src.redis.redis_client import AzureRedisManager  # existing helper
from src.aoai.aoai_transcribe import AudioTranscriber
from routers import router as api_router

logger = get_logger("main")

# --------------------------------------------------------------------------- #
#  App factory
# --------------------------------------------------------------------------- #
app = FastAPI()
app.state.clients = set()  # /relay dashboard sockets
app.state.greeted_call_ids = set()  # to avoid double greetings

# ---------------- Middleware ------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------- Startup / Shutdown ---------------------------------------
@app.on_event("startup")
async def on_startup() -> None:
    logger.info("🚀 startup…")

    # Speech SDK
    app.state.stt_client = SpeechCoreTranslator()
    VOICE = "en-US-JennyMultilingualNeural"
    app.state.tts_client = SpeechSynthesizer(voice=VOICE)

    # Redis connection
    app.state.redis = AzureRedisManager()

    # Gpt4o-transcribe config
    app.state.aoai_stt_cfg = {
        "url": f"{AOAI_STT_ENDPOINT.replace('https','wss')}"
        "/openai/realtime?api-version=2025-04-01-preview&intent=transcription",
        "headers": {"api-key": AOAI_STT_KEY},
        "rate": 16000,
        "channels": 1,
        "format_": 16,  # PCM16
        "chunk": 1024,
        # VAD settings
        "vad": {
            "threshold": 0.5,
            "prefix_padding_ms": 400,
            "silence_duration_ms": 1300,
        },
    }
    # Outbound ACS caller (may be None if env vars missing)
    app.state.acs_caller = initialize_acs_caller_instance()

    logger.info("startup complete")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("🛑 shutdown…")
    # (Close Redis, ACS sessions, etc. if your helpers expose close() methods)


# ---------------- Routers ---------------------------------------------------
app.include_router(api_router)

# --------------------------------------------------------------------------- #
#  CLI entry-point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",  # Use import string to support reload
        host="0.0.0.0",
        port=8010,
        reload=True,
    )
