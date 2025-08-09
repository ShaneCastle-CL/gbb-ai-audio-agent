"""
routers/acs.py
==============
Outbound phone-call flow via Azure Communication Services.

• POST  /api/call/initiate    - start a phone call
• POST  /api/call/callbacks   - receive ACS events
• WS    /ws/stream            - bidirectional PCM media audio stream, also handles acs realtime transcription
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Optional

from azure.communication.callautomation import PhoneNumberIdentifier
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.websockets import WebSocketState
from opentelemetry import trace
from pydantic import BaseModel

# Add project imports
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "src"))

from apps.rtagent.backend.settings import (
    ACS_CALL_OUTBOUND_PATH,
    ACS_CALL_INBOUND_PATH,
    ACS_CALL_CALLBACK_PATH,
    ACS_STREAMING_MODE,
    ACS_WEBSOCKET_PATH,
    ENABLE_AUTH_VALIDATION,
)
from apps.rtagent.backend.src.handlers import (
    ACSHandler,
    ACSMediaHandler,
    TranscriptionHandler,
)
from apps.rtagent.backend.src.latency.latency_tool import LatencyTool
from apps.rtagent.backend.src.utils.auth import (
    AuthError,
    validate_acs_http_auth,
    validate_acs_ws_auth,
)
from src.enums.stream_modes import StreamMode
from src.stateful.state_managment import MemoManager
from utils.ml_logging import get_logger

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
logger = get_logger("routers.acs")
router = APIRouter()
tracer = trace.get_tracer(__name__)

# Tracing configuration
TRACING_ENABLED = os.getenv("ACS_TRACING", os.getenv("ENABLE_TRACING", "false")).lower() == "true"

# Common span attributes
SPAN_ATTRS = {
    "component": "acs_router",
    "service": "acs",
}

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class CallRequest(BaseModel):
    target_number: str


# --------------------------------------------------------------------------- #
# Helper Functions
# --------------------------------------------------------------------------- #
def create_span_attrs(**kwargs) -> dict:
    """Create span attributes with common fields."""
    attrs = SPAN_ATTRS.copy()
    attrs.update(kwargs)
    return attrs


def log_with_context(level: str, message: str, **kwargs):
    """Log with consistent context."""
    extra = {"operation_name": kwargs.pop("operation", None)}
    extra.update(kwargs)
    getattr(logger, level)(message, extra={k: v for k, v in extra.items() if v is not None})


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.post(ACS_CALL_OUTBOUND_PATH or "/api/call/initiate")
async def initiate_call(call: CallRequest, request: Request):
    """Initiate an outbound call through ACS."""
    span_attrs = create_span_attrs(
        operation_name="initiate_call",
        target_number=call.target_number,
    )

    with tracer.start_as_current_span("acs.initiate_call", attributes=span_attrs) as span:
        log_with_context("info", f"Initiating call to {call.target_number}", operation="initiate_call")

        result = await ACSHandler.initiate_call(
            acs_caller=request.app.state.acs_caller,
            target_number=call.target_number,
            redis_mgr=request.app.state.redis,
        )

        if result.get("status") == "success":
            call_id = result.get("callId")
            if TRACING_ENABLED and span and call_id:
                span.set_attribute("call_connection_id", call_id)

            log_with_context(
                "info",
                "Call initiated successfully",
                operation="initiate_call",
                session_id=call_id,
                target_number=call.target_number,
            )
            return {"message": result.get("message"), "callId": call_id}

        # Error handling
        if TRACING_ENABLED and span:
            span.set_attribute("error", True)
            span.set_attribute("error.message", result.get("message", "Unknown error"))

        log_with_context(
            "error",
            "Call initiation failed",
            operation="initiate_call",
            target_number=call.target_number,
            error=result,
        )
        return JSONResponse(
            {"error": "Call initiation failed", "details": result},
            status_code=400
        )


@router.post(ACS_CALL_INBOUND_PATH or "/api/call/answer")
async def answer_call(request: Request):
    """Handle inbound call events."""
    span_attrs = create_span_attrs(operation_name="answer_call")
    
    with tracer.start_as_current_span("acs.answer_call", attributes=span_attrs) as span:
        try:
            body = await request.json()
            acs_caller = request.app.state.acs_caller
            
            inbound_call = await ACSHandler.handle_inbound_call(
                request_body=body, 
                acs_caller=acs_caller
            )
            
            log_with_context("info", "Inbound call handled", operation="answer_call")
            return inbound_call
            
        except Exception as exc:
            if TRACING_ENABLED and span:
                span.set_attribute("error", True)
                span.set_attribute("error.message", str(exc))
            
            log_with_context(
                "error", 
                "Error processing inbound call", 
                operation="answer_call",
                error=str(exc)
            )
            raise HTTPException(400, "Invalid request body") from exc


@router.post(ACS_CALL_CALLBACK_PATH or "/api/call/callbacks")
async def callbacks(request: Request):
    """Handle ACS callback events."""
    # Validate dependencies
    if not request.app.state.acs_caller:
        return JSONResponse({"error": "ACS not initialised"}, status_code=503)
    if not request.app.state.stt_client:
        return JSONResponse({"error": "STT client not initialised"}, status_code=503)
    
    # Validate auth if enabled
    if ENABLE_AUTH_VALIDATION:
        try:
            decoded = validate_acs_http_auth(request)
            logger.debug("JWT token validated successfully")
        except HTTPException as e:
            return JSONResponse({"error": e.detail}, status_code=e.status_code)

    try:
        events = await request.json()
        
        # Extract call connection ID
        call_connection_id = None
        if isinstance(events, dict):
            call_connection_id = events.get("callConnectionId") or events.get("call_connection_id")
        if not call_connection_id:
            call_connection_id = request.headers.get("x-ms-call-connection-id")

        span_attrs = create_span_attrs(
            operation_name="process_callbacks",
            call_connection_id=call_connection_id,
            events_count=len(events) if isinstance(events, list) else 1,
        )
        
        with tracer.start_as_current_span("acs.callbacks", attributes=span_attrs) as span:
            result = await ACSHandler.process_callback_events(
                events=events,
                request=request,
            )

            if "error" in result:
                if TRACING_ENABLED and span:
                    span.set_attribute("error", True)
                    span.set_attribute("error.message", result.get("error"))
                return JSONResponse(result, status_code=500)
            return result

    except Exception as exc:
        log_with_context(
            "error",
            "Callback processing error",
            operation="process_callbacks",
            error=str(exc)
        )
        return JSONResponse({"error": str(exc)}, status_code=500)

@router.websocket(ACS_WEBSOCKET_PATH or "/ws/call/stream")
async def acs_media_ws(ws: WebSocket):
    """Handle WebSocket media streaming for ACS calls."""
    cid = None
    handler = None
    
    try:
        await ws.accept()

        # Validate auth if enabled
        if ENABLE_AUTH_VALIDATION:
            try:
                decoded = await validate_acs_ws_auth(ws)
                logger.info("WebSocket authenticated successfully")
            except AuthError as e:
                logger.warning(f"WebSocket authentication failed: {str(e)}")
                return

        # Initialize connection
        acs = ws.app.state.acs_caller
        redis_mgr = ws.app.state.redis
        cid = ws.headers["x-ms-call-connection-id"]
        cm = MemoManager.from_redis(cid, redis_mgr)

        # Initialize latency tracking
        ws.state.lt = LatencyTool(cm)
        ws.state.lt.start("greeting_ttfb")
        ws.state._greeting_ttfb_stopped = False

        # Set up call context
        target_phone_number = cm.get_context("target_number")
        if target_phone_number:
            ws.app.state.target_participant = PhoneNumberIdentifier(target_phone_number)
        ws.app.state.cm = cm

        # Validate call connection
        call_conn = acs.get_call_connection(cid)
        if not call_conn:
            logger.info(f"Call connection {cid} not found, closing WebSocket")
            await ws.close(code=1000)
            return

        ws.app.state.call_conn = call_conn

        span_attrs = create_span_attrs(
            operation_name="websocket_stream",
            call_connection_id=cid,
            session_id=cm.session_id if hasattr(cm, "session_id") else None,
            stream_mode=ACS_STREAMING_MODE.value if hasattr(ACS_STREAMING_MODE, "value") else str(ACS_STREAMING_MODE),
        )
        
        with tracer.start_as_current_span("acs.websocket", attributes=span_attrs) as span:
            # Initialize appropriate handler
            if ACS_STREAMING_MODE == StreamMode.MEDIA:
                handler = ACSMediaHandler(ws, recognizer=ws.app.state.stt_client, cm=cm)
            elif ACS_STREAMING_MODE == StreamMode.TRANSCRIPTION:
                handler = TranscriptionHandler(ws, cm=cm)
            else:
                logger.error(f"Unknown streaming mode: {ACS_STREAMING_MODE}")
                await ws.close(code=1000)
                return

            ws.app.state.handler = handler

            log_with_context(
                "info",
                "WebSocket stream established",
                operation="websocket_stream",
                call_connection_id=cid,
                mode=str(ACS_STREAMING_MODE),
            )

            # Process messages
            while ws.client_state == WebSocketState.CONNECTED and ws.application_state == WebSocketState.CONNECTED:
                msg = await ws.receive_text()
                if msg:
                    if ACS_STREAMING_MODE == StreamMode.MEDIA:
                        await handler.handle_media_message(msg)
                    elif ACS_STREAMING_MODE == StreamMode.TRANSCRIPTION:
                        await handler.handle_transcription_message(msg)

    except WebSocketDisconnect as e:
        if e.code == 1000:
            log_with_context("info", "WebSocket disconnected normally", operation="websocket_stream")
        else:
            log_with_context(
                "warning",
                f"WebSocket disconnected abnormally",
                operation="websocket_stream",
                disconnect_code=e.code,
                reason=e.reason,
            )
    except asyncio.CancelledError:
        log_with_context("info", "WebSocket cancelled", operation="websocket_stream")
    except Exception as e:
        log_with_context(
            "error",
            "WebSocket error",
            operation="websocket_stream",
            error=str(e),
            call_connection_id=cid,
        )
    finally:
        # Cleanup
        if ws.client_state == WebSocketState.CONNECTED and ws.application_state == WebSocketState.CONNECTED:
            await ws.close()
        
        if handler and ACS_STREAMING_MODE == StreamMode.MEDIA:
            try:
                handler.recognizer.stop()
                logger.info("Speech recognizer stopped")
            except Exception as e:
                logger.error(f"Error stopping recognizer: {e}")
        
        log_with_context(
            "info",
            "WebSocket cleanup complete",
            operation="websocket_stream",
            call_connection_id=cid,
        )