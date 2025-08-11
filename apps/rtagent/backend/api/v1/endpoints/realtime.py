"""
V1 Realtime API Endpoints
=========================

V1 realtime endpoints for enhanced WebSocket communication.

This module provides:
- Dashboard relay endpoints with enhanced monitoring
- Browser conversation endpoints with orchestrator injection
- Advanced tracing and observability
- Production-ready session management
- Enhanced security and authentication

Migrated from legacy realtime router while maintaining backward compatibility
and core conversation functionality.
"""

from __future__ import annotations

import time
from typing import Optional, Any

from fastapi import APIRouter, WebSocket, Depends
from opentelemetry import trace

from ..dependencies.orchestrator import get_orchestrator
from ..handlers.realtime import V1RealtimeHandler, create_v1_realtime_handler
from utils.ml_logging import get_logger

logger = get_logger("api.v1.endpoints.realtime")
tracer = trace.get_tracer(__name__)

# Create V1 router
router = APIRouter(prefix="/realtime", tags=["realtime"])


@router.websocket("/dashboard/relay")
async def dashboard_relay_endpoint(websocket: WebSocket) -> None:
    """
    V1 Dashboard relay WebSocket endpoint.

    Enhanced dashboard broadcasting endpoint with:
    - Advanced connection tracking and monitoring
    - Enhanced error handling and recovery
    - Detailed tracing and observability
    - Production-ready session management

    This endpoint maintains backward compatibility with legacy dashboard clients
    while providing enhanced V1 features.

    **Connection Flow:**
    1. Client connects to dashboard relay
    2. Connection is tracked and monitored
    3. Messages are broadcasted to all connected dashboard clients
    4. Connection lifecycle is managed with proper cleanup

    **Enhanced Features:**
    - Client connection tracking with unique IDs
    - Advanced error handling and graceful degradation
    - OpenTelemetry tracing for observability
    - Production-ready session management

    Args:
        websocket: WebSocket connection from dashboard client
    """
    handler = create_v1_realtime_handler()
    await handler.handle_dashboard_relay(websocket)


@router.websocket("/conversation")
async def browser_conversation_endpoint(
    websocket: WebSocket,
    orchestrator: Optional[callable] = Depends(get_orchestrator)
) -> None:
    """
    V1 Browser conversation WebSocket endpoint with orchestrator injection.

    Enhanced browser conversation endpoint with:
    - Pluggable orchestrator support for different conversation engines
    - Advanced session management and state tracking
    - Enhanced audio streaming with interruption handling
    - Production-ready error handling and recovery
    - Detailed tracing and performance monitoring

    This endpoint provides enhanced conversation capabilities while maintaining
    full backward compatibility with existing browser clients.

    **Conversation Flow:**
    1. Browser connects and session is initialized
    2. Audio streams are processed with STT
    3. Conversation is routed through pluggable orchestrator
    4. Response is synthesized with TTS and streamed back
    5. Session state is maintained and persisted

    **V1 Features:**
    - Pluggable orchestrator injection (GPT, Anthropic, custom agents)
    - Advanced session state management with Redis persistence
    - Enhanced audio processing with interruption handling
    - Comprehensive tracing and observability

    **Orchestrator Support:**
    - GPT-based orchestrators for standard conversations
    - Anthropic orchestrators for specialized use cases
    - Custom agent orchestrators for domain-specific logic
    - Fallback to legacy orchestrator for backward compatibility

    Args:
        websocket: WebSocket connection from browser client
        orchestrator: Injected conversation orchestrator (optional)
    """
    handler = create_v1_realtime_handler(orchestrator)
    await handler.handle_browser_conversation(websocket, orchestrator)


# Legacy compatibility endpoints
@router.websocket("/ws/relay")
async def legacy_dashboard_relay(websocket: WebSocket) -> None:
    """
    Legacy dashboard relay endpoint for backward compatibility.

    This endpoint maintains full backward compatibility with existing
    dashboard clients while internally using the enhanced V1 handler.

    **Migration Path:**
    - Existing clients continue to work without changes
    - Internal processing uses enhanced V1 handler
    - Monitoring and tracing are enhanced automatically
    - Gradual migration to /v1/realtime/dashboard/relay recommended

    Args:
        websocket: WebSocket connection from legacy dashboard client
    """
    logger.info("Legacy dashboard relay endpoint accessed - consider migrating to /v1/realtime/dashboard/relay")
    handler = create_v1_realtime_handler()
    await handler.handle_dashboard_relay(websocket)


@router.websocket("/ws/conversation")
async def legacy_browser_conversation(
    websocket: WebSocket,
    orchestrator: Optional[callable] = Depends(get_orchestrator)
) -> None:
    """
    Legacy browser conversation endpoint for backward compatibility.

    This endpoint maintains full backward compatibility with existing
    browser clients while internally using the enhanced V1 handler with
    orchestrator injection.

    **Migration Path:**
    - Existing browser clients continue to work without changes
    - Internal processing uses enhanced V1 handler
    - Orchestrator injection is available automatically
    - Monitoring and tracing are enhanced automatically
    - Gradual migration to /v1/realtime/conversation recommended

    Args:
        websocket: WebSocket connection from legacy browser client
        orchestrator: Injected conversation orchestrator (optional)
    """
    logger.info("Legacy browser conversation endpoint accessed - consider migrating to /v1/realtime/conversation")
    handler = create_v1_realtime_handler(orchestrator)
    await handler.handle_browser_conversation(websocket, orchestrator)
