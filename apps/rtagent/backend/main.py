"""
voice_agent.main
================
Entrypoint that stitches everything together:

• config / CORS
• shared objects on `app.state`  (Speech, Redis, ACS, TTS, dashboard-clients)
• route registration (routers package)
"""
from __future__ import annotations
from utils.telemetry_config import setup_azure_monitor


# ---------------- Monitoring ------------------------------------------------
setup_azure_monitor(logger_name="rtagent")


from utils.ml_logging import get_logger
logger = get_logger("main")

import uvicorn
import os
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apps.rtagent.backend.src.routers import router as api_router
from apps.rtagent.backend.src.agents.prompt_store.prompt_manager import PromptManager
from apps.rtagent.backend.src.services.acs.acs_caller import (
    initialize_acs_caller_instance,
)
from apps.rtagent.backend.src.services.openai_services import (
    client as azure_openai_client,
)
from apps.rtagent.backend.settings import (
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
    AGENT_GENERAL_INFO_CONFIG,
    VAD_SEMANTIC_SEGMENTATION
)
from apps.rtagent.backend.src.services import (
    AzureRedisManager,
    CosmosDBMongoCoreManager,
    SpeechSynthesizer,
    StreamingSpeechRecognizerFromBytes,
)

from apps.rtagent.backend.src.agents.base import RTAgent
from opentelemetry import trace
import time
# --------------------------------------------------------------------------- #
#  Lifecycle Management
# --------------------------------------------------------------------------- #
async def lifespan(app: FastAPI):
    """Manage application lifecycle: startup and shutdown events."""

    tracer = trace.get_tracer(__name__)

    # Startup
    with tracer.start_as_current_span("startup-lifespan") as span:
        logger.info("🚀 startup…")
        start_time = time.perf_counter()

        # Set span attributes for better correlation
        span.set_attributes({
            "service.name": "rtagent-api",
            "service.version": "1.0.0",
            "startup.stage": "initialization"
        })

        # Initialize app state
        app.state.clients = set()  # /relay dashboard sockets
        app.state.greeted_call_ids = set()  # to avoid double greetings

        # Speech SDK
        span.set_attribute("startup.stage", "speech_sdk")
        # Speech SDK
        app.state.tts_client = SpeechSynthesizer(voice=VOICE_TTS, playback="always")
        app.state.stt_client = StreamingSpeechRecognizerFromBytes(
            use_semantic_segmentation=VAD_SEMANTIC_SEGMENTATION,
            vad_silence_timeout_ms=SILENCE_DURATION_MS,
            candidate_languages=RECOGNIZED_LANGUAGE,
            audio_format=AUDIO_FORMAT,
        )

        # Redis connection
        span.set_attribute("startup.stage", "redis")
        app.state.redis = AzureRedisManager()

        # Cosmos DB connection
        span.set_attribute("startup.stage", "cosmos_db")
        app.state.cosmos = CosmosDBMongoCoreManager(
            connection_string=AZURE_COSMOS_CONNECTION_STRING,
            database_name=AZURE_COSMOS_DATABASE_NAME,
            collection_name=AZURE_COSMOS_COLLECTION_NAME,
        )
        
        span.set_attribute("startup.stage", "openai_clients")
        app.state.azureopenai_client = azure_openai_client
        app.state.promptsclient = PromptManager()

        # Outbound ACS caller (may be None if env vars missing)
        span.set_attribute("startup.stage", "acs_agents")
        app.state.acs_caller = initialize_acs_caller_instance()
        app.state.auth_agent = RTAgent(config_path=AGENT_AUTH_CONFIG)
        app.state.claim_intake_agent = RTAgent(config_path=AGENT_CLAIM_INTAKE_CONFIG)
        app.state.general_info_agent = RTAgent(config_path=AGENT_GENERAL_INFO_CONFIG)

        elapsed = time.perf_counter() - start_time
        logger.info(f"startup complete in {elapsed:.2f}s")
        
        # Set final span attributes
        span.set_attributes({
            "startup.duration_sec": elapsed,
            "startup.stage": "complete",
            "startup.success": True
        })

    # Yield control to the application
    yield

    # Shutdown
    with tracer.start_as_current_span("shutdown-lifespan") as span:
        logger.info("🛑 shutdown…")
        span.set_attributes({
            "service.name": "rtagent-api",
            "shutdown.stage": "cleanup"
        })
        # Close Redis, ACS sessions, etc. if your helpers expose close() methods
        # Add any cleanup logic here as needed
        span.set_attribute("shutdown.success", True)


# --------------------------------------------------------------------------- #
#  App factory
# --------------------------------------------------------------------------- #
app = FastAPI(lifespan=lifespan)

# ---------------- Middleware ------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    max_age=86400,
)

# ---------------- Routers ---------------------------------------------------
app.include_router(api_router)

# ---------------- Health Check Endpoint -------------------------------------
@app.get("/health")
async def health_check():
    """Simple health check endpoint to generate traces for Application Insights testing."""
    tracer = trace.get_tracer(__name__)
    
    with tracer.start_as_current_span("health_check") as span:
        span.set_attributes({
            "service.name": "rtagent-api",
            "health.check.endpoint": "/health",
            "health.status": "healthy"
        })
        
        logger.info("Health check endpoint called")
        
        return {
            "status": "healthy",
            "service": "rtagent-api",
            "timestamp": time.time()
        }

# --------------------------------------------------------------------------- #
#  CLI entry-point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8010))
    uvicorn.run(
        "main:app",  
        host="0.0.0.0",  # nosec: B104
        port=port,
        reload=True,
    )
