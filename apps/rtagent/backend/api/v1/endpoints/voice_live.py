"""
Voice Live API Endpoints - Azure AI Speech Integration
======================================================

WebSocket endpoints for Azure AI Speech Voice Live API integration.
Provides real-time voice interaction with generative AI models.

Key Features:
- Real-time audio streaming with WebSocket connections
- Azure AI Speech Voice Live API integration
- Session management with proper resource cleanup
- Authentication and authorization support
- Comprehensive error handling and recovery
- OpenTelemetry tracing and observability

WebSocket Flow:
1. Accept connection and validate dependencies
2. Initialize Voice Live session with Azure AI Speech
3. Stream audio bidirectionally with voice activity detection
4. Process responses through AI models
5. Clean up resources on disconnect/error
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Optional, Set
from datetime import datetime

from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.websockets import WebSocketState
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

# Core application imports
from config.app_config import ENABLE_AUTH_VALIDATION
from utils.ml_logging import get_logger

# V1 components
from ..dependencies.orchestrator import get_orchestrator
from ..schemas import (
    VoiceLiveStatusResponse,
    VoiceLiveSessionResponse,
    VoiceLiveConfigRequest,
)
from ..handlers.voice_live_handler import VoiceLiveHandler
from apps.rtagent.backend.src.utils.tracing import log_with_context
from apps.rtagent.backend.src.utils.auth import validate_acs_ws_auth, AuthError

logger = get_logger("api.v1.endpoints.voice_live")
tracer = trace.get_tracer(__name__)

# Global registry for Voice Live session tracking
_active_voice_live_sessions: Set[WebSocket] = set()

router = APIRouter()


@router.get(
    "/status",
    response_model=VoiceLiveStatusResponse,
    summary="Get Voice Live Service Status",
    description="""
    Get the current status of the Voice Live service integration.
    
    Returns information about:
    - Service availability and Azure AI Speech integration status
    - Supported features and capabilities
    - Active session counts
    - WebSocket endpoint configurations
    - Azure AI Speech client health
    """,
    tags=["Voice Live Status"],
    responses={
        200: {
            "description": "Voice Live service status retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "status": "available",
                        "azure_speech_status": "connected",
                        "websocket_endpoints": {
                            "voice_live_session": "/api/v1/voice-live/session",
                        },
                        "features": {
                            "real_time_audio": True,
                            "voice_activity_detection": True,
                            "noise_reduction": True,
                            "echo_cancellation": True,
                            "session_management": True,
                        },
                        "active_connections": {
                            "voice_live_sessions": 0,
                        },
                        "version": "v1",
                    }
                }
            },
        }
    },
)
async def get_voice_live_status(request: Request):
    """
    Retrieve comprehensive status of the Voice Live service integration.

    This endpoint provides detailed information about Azure AI Speech integration,
    WebSocket endpoints, active session counts, and service capabilities for
    real-time voice interaction with AI models.

    :param request: FastAPI request object providing access to application state
    :return: VoiceLiveStatusResponse containing service status and configuration
    :raises: None (endpoint designed to always return current service status)
    """
    # Check Voice Live client pool status
    azure_speech_status = "unknown"
    if hasattr(request.app.state, "voice_live_pool"):
        pool = request.app.state.voice_live_pool
        azure_speech_status = "connected" if pool and pool.size > 0 else "disconnected"

    return VoiceLiveStatusResponse(
        status="available",
        azure_speech_status=azure_speech_status,
        websocket_endpoints={
            "voice_live_session": "/api/v1/voice-live/session",
        },
        features={
            "real_time_audio": True,
            "voice_activity_detection": True,
            "noise_reduction": True,
            "echo_cancellation": True,
            "session_management": True,
            "ai_model_integration": True,
        },
        active_connections={
            "voice_live_sessions": len(_active_voice_live_sessions),
        },
        protocols_supported=["WebSocket"],
        version="v1",
    )


@router.websocket("/session")
async def voice_live_session_endpoint(
    websocket: WebSocket,
    orchestrator: Optional[callable] = Depends(get_orchestrator)
):
    """
    Voice Live session WebSocket endpoint with Azure AI Speech integration.

    Establishes a WebSocket connection for real-time voice interaction using
    Azure AI Speech Voice Live API. Supports bidirectional audio streaming,
    voice activity detection, and AI model integration.

    :param websocket: WebSocket connection from client
    :param orchestrator: Injected conversation orchestrator (optional)
    :return: None
    :raises WebSocketDisconnect: When client disconnects from WebSocket
    :raises HTTPException: For authentication or dependency validation failures
    :raises Exception: For any other errors during session processing
    """
    session_id = None
    voice_live_handler = None

    try:
        # Accept connection and initialize session
        await websocket.accept()

        # Generate unique session ID
        session_id = str(uuid.uuid4())

        with tracer.start_as_current_span(
            "api.v1.voice_live.session_connect",
            kind=SpanKind.SERVER,
            attributes={
                "api.version": "v1",
                "voice_live.session_id": session_id,
                "voice_live.endpoint": "session",
                "network.protocol.name": "websocket",
                "orchestrator.name": getattr(orchestrator, "name", "unknown")
                if orchestrator
                else "default",
            },
        ) as connect_span:
            # Validate dependencies
            await _validate_voice_live_dependencies(websocket)

            # Authenticate if required
            if ENABLE_AUTH_VALIDATION:
                await _validate_voice_live_auth(websocket)

            # Initialize Voice Live session
            voice_live_handler = await _initialize_voice_live_session(
                websocket, session_id, orchestrator
            )

            # Add session to global registry
            if websocket not in _active_voice_live_sessions:
                _active_voice_live_sessions.add(websocket)
                logger.info(
                    f"Voice Live session {session_id} connected. Total sessions: {len(_active_voice_live_sessions)}"
                )

            # Track session metrics
            if hasattr(websocket.app.state, "session_metrics"):
                await websocket.app.state.session_metrics.increment_connected()

            connect_span.set_attribute("voice_live.sessions.total", len(_active_voice_live_sessions))
            connect_span.set_status(Status(StatusCode.OK))

            log_with_context(
                logger,
                "info",
                "Voice Live session initialized successfully",
                operation="voice_live_connect",
                session_id=session_id,
                total_sessions=len(_active_voice_live_sessions),
                api_version="v1",
            )

        # Process Voice Live messages
        await _process_voice_live_messages(
            websocket, session_id, voice_live_handler, orchestrator
        )

    except WebSocketDisconnect as e:
        _log_voice_live_disconnect(e, session_id)
    except Exception as e:
        _log_voice_live_error(e, session_id)
        raise
    finally:
        await _cleanup_voice_live_session(websocket, session_id, voice_live_handler)


# ============================================================================
# Voice Live Helper Functions
# ============================================================================


async def _validate_voice_live_dependencies(websocket: WebSocket) -> None:
    """
    Validate required app state dependencies for Voice Live endpoints.

    :param websocket: WebSocket connection containing app state
    :return: None
    :raises HTTPException: If required dependencies are not initialized
    """
    # Check Azure AI Speech Voice Live pool
    if not hasattr(websocket.app.state, "voice_live_pool") or not websocket.app.state.voice_live_pool:
        logger.error("Voice Live client pool not initialized")
        await websocket.close(code=1011, reason="Voice Live client pool not initialized")
        raise HTTPException(503, "Voice Live client pool not initialized")

    # Check Redis for session management
    if not hasattr(websocket.app.state, "redis") or not websocket.app.state.redis:
        logger.error("Redis client not initialized")
        await websocket.close(code=1011, reason="Redis not initialized")
        raise HTTPException(503, "Redis client not initialized")


async def _validate_voice_live_auth(websocket: WebSocket) -> None:
    """
    Validate WebSocket authentication for Voice Live endpoints.

    :param websocket: WebSocket connection to authenticate
    :return: None
    :raises HTTPException: If authentication fails
    :raises AuthError: If WebSocket authentication validation fails
    """
    try:
        _ = await validate_acs_ws_auth(websocket)
        logger.info("Voice Live WebSocket authenticated successfully")
    except AuthError as e:
        logger.warning(f"Voice Live WebSocket authentication failed: {str(e)}")
        await websocket.close(code=4001, reason="Authentication failed")
        raise HTTPException(401, f"Authentication failed: {str(e)}")


async def _initialize_voice_live_session(
    websocket: WebSocket, session_id: str, orchestrator: Optional[callable]
) -> VoiceLiveHandler:
    """
    Initialize Voice Live session with Azure AI Speech integration.

    :param websocket: WebSocket connection for the Voice Live session
    :param session_id: Unique identifier for the Voice Live session
    :param orchestrator: Optional orchestrator for conversation routing
    :return: Initialized VoiceLiveHandler instance for session management
    :raises Exception: If session initialization fails
    """
    # Create Voice Live handler
    voice_live_handler = VoiceLiveHandler(
        session_id=session_id,
        websocket=websocket,
        voice_live_pool=websocket.app.state.voice_live_pool,
        redis_client=websocket.app.state.redis,
        orchestrator=orchestrator
    )

    # Initialize the session
    await voice_live_handler.initialize()

    # Set up WebSocket state
    websocket.state.voice_live_handler = voice_live_handler
    websocket.state.session_id = session_id

    logger.info(f"Voice Live session initialized for session {session_id}")
    return voice_live_handler


async def _process_voice_live_messages(
    websocket: WebSocket,
    session_id: str,
    voice_live_handler: VoiceLiveHandler,
    orchestrator: Optional[callable],
) -> None:
    """
    Process incoming Voice Live messages with enhanced error handling.

    :param websocket: WebSocket connection for Voice Live client
    :param session_id: Unique identifier for the Voice Live session
    :param voice_live_handler: VoiceLiveHandler instance for session management
    :param orchestrator: Optional orchestrator for conversation routing
    :return: None
    :raises WebSocketDisconnect: When client disconnects normally
    :raises Exception: For any other errors during message processing
    """
    with tracer.start_as_current_span(
        "api.v1.voice_live.process_messages",
        attributes={"session_id": session_id},
    ) as span:
        try:
            message_count = 0
            while (
                websocket.client_state == WebSocketState.CONNECTED
                and websocket.application_state == WebSocketState.CONNECTED
            ):
                msg = await websocket.receive()
                message_count += 1

                # Handle different message types
                if msg.get("type") == "websocket.receive":
                    if msg.get("bytes") is not None:
                        # Handle audio data
                        await voice_live_handler.handle_audio_data(msg["bytes"])
                    elif msg.get("text") is not None:
                        # Handle text messages (configuration, control)
                        await voice_live_handler.handle_text_message(msg["text"])

                elif msg.get("type") == "websocket.disconnect":
                    break

            span.set_attribute("messages.processed", message_count)
            span.set_status(Status(StatusCode.OK))

        except WebSocketDisconnect:
            span.set_status(Status(StatusCode.OK, "Normal disconnect"))
            raise
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, f"Message processing error: {e}"))
            logger.error(
                f"Error processing Voice Live messages for session {session_id}: {e}"
            )
            raise


def _log_voice_live_disconnect(e: WebSocketDisconnect, session_id: Optional[str]) -> None:
    """
    Log Voice Live session disconnection.

    :param e: WebSocketDisconnect exception containing disconnect details
    :param session_id: Optional unique identifier for the Voice Live session
    :return: None
    """
    if e.code == 1000:
        log_with_context(
            logger,
            "info",
            "Voice Live session disconnected normally",
            operation="voice_live_disconnect",
            session_id=session_id,
            disconnect_code=e.code,
            api_version="v1",
        )
    else:
        log_with_context(
            logger,
            "warning",
            "Voice Live session disconnected abnormally",
            operation="voice_live_disconnect",
            session_id=session_id,
            disconnect_code=e.code,
            reason=e.reason,
            api_version="v1",
        )


def _log_voice_live_error(e: Exception, session_id: Optional[str]) -> None:
    """
    Log Voice Live session errors.

    :param e: Exception that occurred during Voice Live operation
    :param session_id: Optional unique identifier for the Voice Live session
    :return: None
    """
    log_with_context(
        logger,
        "error",
        "Voice Live session error",
        operation="voice_live_error",
        session_id=session_id,
        error=str(e),
        error_type=type(e).__name__,
        api_version="v1",
    )


async def _cleanup_voice_live_session(
    websocket: WebSocket,
    session_id: Optional[str],
    voice_live_handler: Optional[VoiceLiveHandler],
) -> None:
    """
    Clean up Voice Live session resources.

    :param websocket: WebSocket connection to clean up
    :param session_id: Optional unique identifier for the Voice Live session
    :param voice_live_handler: Optional VoiceLiveHandler instance to clean up
    :return: None
    :raises Exception: If cleanup operations fail (logged but not re-raised)
    """
    with tracer.start_as_current_span(
        "api.v1.voice_live.cleanup_session", attributes={"session_id": session_id}
    ) as span:
        try:
            # Clean up Voice Live handler
            if voice_live_handler:
                await voice_live_handler.cleanup()

            # Remove from global registry
            if websocket in _active_voice_live_sessions:
                _active_voice_live_sessions.remove(websocket)
                logger.info(
                    f"Voice Live session {session_id} removed. Remaining sessions: {len(_active_voice_live_sessions)}"
                )

            # Track session metrics
            if hasattr(websocket.app.state, "session_metrics"):
                await websocket.app.state.session_metrics.increment_disconnected()

            # Close WebSocket if still connected
            if (
                websocket.client_state == WebSocketState.CONNECTED
                and websocket.application_state == WebSocketState.CONNECTED
            ):
                await websocket.close()

            span.set_status(Status(StatusCode.OK))
            log_with_context(
                logger,
                "info",
                "Voice Live session cleanup complete",
                operation="voice_live_cleanup",
                session_id=session_id,
                api_version="v1",
            )

        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, f"Cleanup error: {e}"))
            logger.error(f"Error during Voice Live cleanup: {e}")