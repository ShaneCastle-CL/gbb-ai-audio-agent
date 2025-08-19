"""
V1 Call Event Handlers (Simplified)
===================================

Simplified event handlers with DTMF logic moved to DTMFValidationLifecycle.
Focuses on core call lifecycle events only.

Key Features:
- Basic call lifecycle handling (connected, disconnected, etc.)
- Delegates DTMF processing to DTMFValidationLifecycle
- Comprehensive event routing for all ACS webhook events
- Proper OpenTelemetry tracing and error handling
"""

import asyncio
import json
from typing import Any, Dict, List, Optional
from azure.core.messaging import CloudEvent
from azure.communication.callautomation import PhoneNumberIdentifier

from opentelemetry import trace
from opentelemetry.trace import SpanKind

from apps.rtagent.backend.src.shared_ws import broadcast_message
from utils.ml_logging import get_logger
from .types import CallEventContext, ACSEventTypes
from apps.rtagent.backend.api.v1.handlers.dtmf_validation_lifecycle import DTMFValidationLifecycle

logger = get_logger("v1.events.handlers")
tracer = trace.get_tracer(__name__)


class CallEventHandlers:
    """
    Simplified event handlers for Azure Communication Services call events.

    Centralized handlers for core call lifecycle events:
    - API-initiated operations (call initiation, answering)
    - ACS webhook events (connected, disconnected, etc.)
    - Media and recognition events (delegates DTMF to DTMFValidationLifecycle)
    """

    @staticmethod
    async def handle_call_initiated(context: CallEventContext) -> None:
        """Handle call initiation events from API operations."""
        with tracer.start_as_current_span(
            "v1.handle_call_initiated",
            kind=SpanKind.INTERNAL,
            attributes={
                "call.connection.id": context.call_connection_id,
                "event.type": context.event_type,
            },
        ):
            logger.info(f"🚀 Call initiated: {context.call_connection_id}")

            # Log call initiation details
            event_data = context.get_event_data()
            target_number = event_data.get("target_number")
            api_version = event_data.get("api_version", "unknown")

            logger.info(f"   Target: {target_number}, API: {api_version}")

            # Initialize call tracking and state
            if context.memo_manager:
                context.memo_manager.update_context("call_initiated", True)
                context.memo_manager.update_context("target_number", target_number)

    @staticmethod
    async def handle_webhook_events(context: CallEventContext) -> None:
        """
        Handle all ACS webhook events that come through callbacks endpoint.

        This is the central handler for events from /callbacks endpoint,
        routing them to specific handlers based on event type.
        """
        with tracer.start_as_current_span(
            "v1.handle_webhook_events",
            kind=SpanKind.INTERNAL,
            attributes={
                "call.connection.id": context.call_connection_id,
                "event.type": context.event_type,
                "event.source": "acs_webhook",
            },
        ):
            logger.info(f"🌐 Webhook event: {context.event_type} for {context.call_connection_id}")
            
            # Route to specific handlers
            if context.event_type == ACSEventTypes.CALL_CONNECTED:
                await CallEventHandlers.handle_call_connected(context)
            elif context.event_type == ACSEventTypes.CALL_DISCONNECTED:
                await CallEventHandlers.handle_call_disconnected(context)
            elif context.event_type == ACSEventTypes.CREATE_CALL_FAILED:
                await CallEventHandlers.handle_create_call_failed(context)
            elif context.event_type == ACSEventTypes.ANSWER_CALL_FAILED:
                await CallEventHandlers.handle_answer_call_failed(context)
            elif context.event_type == ACSEventTypes.PARTICIPANTS_UPDATED:
                await CallEventHandlers.handle_participants_updated(context)
            elif context.event_type == ACSEventTypes.DTMF_TONE_RECEIVED:
                await DTMFValidationLifecycle.handle_dtmf_tone_received(context)
            elif context.event_type == ACSEventTypes.PLAY_COMPLETED:
                await CallEventHandlers.handle_play_completed(context)
            elif context.event_type == ACSEventTypes.PLAY_FAILED:
                await CallEventHandlers.handle_play_failed(context)
            elif context.event_type == ACSEventTypes.RECOGNIZE_COMPLETED:
                await CallEventHandlers.handle_recognize_completed(context)
            elif context.event_type == ACSEventTypes.RECOGNIZE_FAILED:
                await CallEventHandlers.handle_recognize_failed(context)
            else:
                logger.warning(f"⚠️  Unhandled webhook event type: {context.event_type}")

            # Update webhook statistics
            try:
                if context.memo_manager:
                    context.memo_manager.update_context("last_webhook_event", context.event_type)
                    if context.redis_mgr:
                        context.memo_manager.persist_to_redis(context.redis_mgr)
            except Exception as e:
                logger.error(f"Failed to update webhook stats: {e}")

    @staticmethod
    async def handle_call_connected(context: CallEventContext) -> None:
        """Handle call connected event - set up AWS Connect-style validation flow."""
        with tracer.start_as_current_span(
            "v1.handle_call_connected",
            kind=SpanKind.INTERNAL,
            attributes={
                "call.connection.id": context.call_connection_id,
                "event.type": context.event_type,
            },
        ):
            logger.info(f"📞 Call connected: {context.call_connection_id}")
            
            # Extract target phone from call connected event
            call_conn = context.acs_caller.get_call_connection(context.call_connection_id)
            participants = call_conn.list_participants()

            caller_participant = None
            acs_participant = None
            caller_id = None

            for participant in participants:
                identifier = participant.identifier
                if getattr(identifier, "kind", None) == 'phone_number':
                    caller_participant = participant
                    caller_id = identifier.properties.get('value')
                elif getattr(identifier, "kind", None) == "communicationUser":
                    acs_participant = participant

            if not caller_participant:
                logger.warning("Caller participant not found in participants list.")
            if not acs_participant:
                logger.warning("ACS participant not found in participants list.")

            logger.info(f"   Caller phone number: {caller_id if caller_id else 'unknown'}")


            try:
                await DTMFValidationLifecycle.setup_aws_connect_validation_flow(
                    context, 
                    call_conn,
                )
                # call_conn.start_continuous_dtmf_recognition(
                #     target_participant=caller_participant.identifier,
                #     operation_context=f"dtmf_recognition_{context.call_connection_id}"
                # )
            except Exception as e:
                logger.error(
                    f"❌ Failed to start continuous DTMF recognition for {context.call_connection_id}: {e}"
                )
            # Broadcast connection status to WebSocket clients
            try:
                if context.clients:
                    await broadcast_message(
                        context.clients,
                        json.dumps(
                            {
                                "type": "call_connected",
                                "call_connection_id": context.call_connection_id,
                                "timestamp": context.get_event_data()
                                .get("callConnectionProperties", {})
                                .get("connectedTime"),
                                "validation_flow": "aws_connect_simulation",
                            }
                        ),
                    )
            except Exception as e:
                logger.error(f"Failed to broadcast call connected: {e}")
                
            # Note: Greeting and conversation flow will be triggered AFTER validation succeeds

    @staticmethod
    async def handle_call_disconnected(context: CallEventContext) -> None:
        """Handle call disconnected event - log reason and cleanup."""
        with tracer.start_as_current_span(
            "v1.handle_call_disconnected",
            kind=SpanKind.INTERNAL,
            attributes={
                "call.connection.id": context.call_connection_id,
                "event.type": context.event_type,
            },
        ):
            # Extract disconnect reason
            event_data = context.get_event_data()
            disconnect_reason = event_data.get("callConnectionState")
            
            logger.info(f"📞 Call disconnected: {context.call_connection_id}, reason: {disconnect_reason}")
            
            # Clean up call state
            await CallEventHandlers._cleanup_call_state(context)

    @staticmethod
    async def handle_create_call_failed(context: CallEventContext) -> None:
        """Handle create call failed event - log error details."""
        with tracer.start_as_current_span(
            "v1.handle_create_call_failed",
            kind=SpanKind.INTERNAL,
            attributes={
                "call.connection.id": context.call_connection_id,
                "event.type": context.event_type,
            },
        ):
            result_info = context.get_event_field("resultInformation", {})
            logger.error(f"❌ Create call failed: {context.call_connection_id}, reason: {result_info}")

    @staticmethod
    async def handle_answer_call_failed(context: CallEventContext) -> None:
        """Handle answer call failed event - log error details."""
        with tracer.start_as_current_span(
            "v1.handle_answer_call_failed",
            kind=SpanKind.INTERNAL,
            attributes={
                "call.connection.id": context.call_connection_id,
                "event.type": context.event_type,
            },
        ):
            result_info = context.get_event_field("resultInformation", {})
            logger.error(f"❌ Answer call failed: {context.call_connection_id}, reason: {result_info}")

    @staticmethod
    async def handle_participants_updated(context: CallEventContext) -> None:
        """Handle participant updates."""
        with tracer.start_as_current_span(
            "v1.handle_participants_updated",
            kind=SpanKind.INTERNAL,
            attributes={
                "call.connection.id": context.call_connection_id,
                "event.type": context.event_type,
            },
        ):
            try:
                participants = context.get_event_field("participants", [])
                logger.info(f"👥 Participants updated: {len(participants)} participants")
                
                # Log participant details
                for i, participant in enumerate(participants):
                    identifier = participant.get("identifier", {})
                    is_muted = participant.get("isMuted", False)
                    logger.info(f"   Participant {i+1}: {identifier.get('kind', 'unknown')}, muted: {is_muted}")
                    
            except Exception as e:
                logger.error(f"Error in participants updated handler: {e}")

    @staticmethod
    async def handle_play_completed(context: CallEventContext) -> None:
        """Handle play completed event."""
        logger.info(f"🎵 Play completed: {context.call_connection_id}")

    @staticmethod
    async def handle_play_failed(context: CallEventContext) -> None:
        """Handle play failed event."""
        result_info = context.get_event_field("resultInformation", {})
        logger.error(
            f"🎵 Play failed: {context.call_connection_id}, reason: {result_info}"
        )

    @staticmethod
    async def handle_recognize_completed(context: CallEventContext) -> None:
        """Handle recognize completed event."""
        logger.info(f"🎤 Recognize completed: {context.call_connection_id}")

    @staticmethod
    async def handle_recognize_failed(context: CallEventContext) -> None:
        """Handle recognize failed event."""
        result_info = context.get_event_field("resultInformation", {})
        logger.error(
            f"🎤 Recognize failed: {context.call_connection_id}, reason: {result_info}"
        )

    # ============================================================================
    # Helper Methods
    # ============================================================================

    @staticmethod
    def _extract_caller_id(caller_info: Dict[str, Any]) -> str:
        """Extract caller ID from caller information."""
        if caller_info.get("kind") == "phoneNumber":
            return caller_info.get("phoneNumber", {}).get("value", "unknown")
        return caller_info.get("rawId", "unknown")

    @staticmethod
    async def _cleanup_call_state(context: CallEventContext) -> None:
        """Clean up call state when call disconnects."""
        try:
            # Basic cleanup - delegate DTMF cleanup to lifecycle handler
            logger.info(f"🧹 Cleaning up call state: {context.call_connection_id}")
            
            # Clear memo context if available
            if context.memo_manager:
                context.memo_manager.update_context("call_active", False)
                context.memo_manager.update_context("call_disconnected", True)
                
        except Exception as e:
            logger.error(f"❌ Error cleaning up call state: {e}")
