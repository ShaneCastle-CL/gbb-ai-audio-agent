"""
voice_agent.main
================
Entrypoint that stitches everything together:

• config / CORS
• shared objects on `app.state`  (Speech, Redis, ACS, TTS, dashboard-clients)
• route registration (routers package)
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import router as api_router
from rtagent.backend.agents.prompt_store.prompt_manager import PromptManager
from rtagent.backend.services.acs.acs_caller import (
    initialize_acs_caller_instance,
)
from rtagent.backend.services.openai_services import (
    client as azure_openai_client,
)
from rtagent.backend.settings import (
    ALLOWED_ORIGINS,
    AZURE_COSMOS_COLLECTION_NAME,
    AZURE_COSMOS_CONNECTION_STRING,
    AZURE_COSMOS_DATABASE_NAME,
    SILENCE_DURATION_MS,
    VOICE_TTS,
    RECOGNIZED_LANGUAGE,
    AUDIO_FORMAT,
    AGENT_AUTH_CONFIG,
    AGENT_CLAIM_INTAKE_CONFIG,
)
from services import (
    AzureRedisManager,
    CosmosDBMongoCoreManager,
    SpeechSynthesizer,
    StreamingSpeechRecognizerFromBytes,
)

from src.agents.base import RTAgent
from utils.ml_logging import get_logger

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
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    max_age=86400,
)


# ---------------- Startup / Shutdown ---------------------------------------
@app.on_event("startup")
async def on_startup() -> None:
    logger.info("🚀 startup…")

    # Speech SDK
    app.state.tts_client = SpeechSynthesizer(voice=VOICE_TTS)
    app.state.stt_client = StreamingSpeechRecognizerFromBytes(
        vad_silence_timeout_ms=SILENCE_DURATION_MS,
        candidate_languages=RECOGNIZED_LANGUAGE,
        audio_format=AUDIO_FORMAT,
    )
    # Redis connection
    app.state.redis = AzureRedisManager()

    # Cosmos DB connection
    app.state.cosmos = CosmosDBMongoCoreManager(
        connection_string=AZURE_COSMOS_CONNECTION_STRING,
        database_name=AZURE_COSMOS_DATABASE_NAME,
        collection_name=AZURE_COSMOS_COLLECTION_NAME,
    )
    app.state.azureopenai_client = azure_openai_client
    app.state.promptsclient = PromptManager()

    # Outbound ACS caller (may be None if env vars missing)
    app.state.acs_caller = initialize_acs_caller_instance()
    app.state.auth_agent = RTAgent(
        config_path=AGENT_AUTH_CONFIG
    )
    app.state.claim_intake_agent = RTAgent(
        config_path=AGENT_CLAIM_INTAKE_CONFIG
    )
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
