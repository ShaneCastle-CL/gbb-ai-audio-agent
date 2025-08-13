"""
Webhook-related API schemas.

Pydantic schemas for webhook payloads and responses.
"""

from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field


class WebhookEvent(BaseModel):
    """Base webhook event model following CloudEvents specification."""

    specversion: str = Field(
        default="1.0", description="CloudEvents specification version", example="1.0"
    )
    type: str = Field(
        ...,
        description="Event type identifier",
        example="Microsoft.Communication.CallConnected",
    )
    source: str = Field(
        ..., description="Event source identifier", example="/acs/calls/abc123"
    )
    id: str = Field(..., description="Unique event identifier", example="event-123-abc")
    time: Optional[str] = Field(
        None,
        description="Event timestamp in ISO 8601 format",
        example="2025-08-10T13:45:00Z",
    )
    datacontenttype: Optional[str] = Field(
        default="application/json",
        description="Content type of the event data",
        example="application/json",
    )
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Event payload data",
        example={"callConnectionId": "abc123", "serverCallId": "server-abc123"},
    )
    subject: Optional[str] = Field(
        None,
        description="Subject of the event within the context of the source",
        example="call/abc123",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "specversion": "1.0",
                "type": "Microsoft.Communication.CallConnected",
                "source": "/acs/calls/abc123",
                "id": "event-123-abc",
                "time": "2025-08-10T13:45:00Z",
                "datacontenttype": "application/json",
                "data": {"callConnectionId": "abc123", "serverCallId": "server-abc123"},
            }
        }


class ACSWebhookEvent(WebhookEvent):
    """Azure Communication Services specific webhook event."""

    type: str = Field(
        ...,
        description="ACS event type",
        example="Microsoft.Communication.CallConnected",
        enum=[
            "Microsoft.Communication.CallConnected",
            "Microsoft.Communication.CallDisconnected",
            "Microsoft.Communication.CallTransferAccepted",
            "Microsoft.Communication.CallTransferFailed",
            "Microsoft.Communication.ParticipantsUpdated",
            "Microsoft.Communication.DtmfToneReceived",
            "Microsoft.Communication.PlayCompleted",
            "Microsoft.Communication.PlayFailed",
            "Microsoft.Communication.RecognizeCompleted",
            "Microsoft.Communication.RecognizeFailed",
        ],
    )
    source: str = Field(
        ...,
        description="ACS source identifier",
        pattern=r"^/acs/calls/[a-zA-Z0-9\-_]+$",
        example="/acs/calls/abc123",
    )
    data: Dict[str, Any] = Field(
        ...,
        description="ACS event data containing callConnectionId and other ACS-specific fields",
        example={
            "callConnectionId": "abc123",
            "serverCallId": "server-abc123",
            "correlationId": "correlation-123",
        },
    )

    class Config:
        json_schema_extra = {
            "example": {
                "specversion": "1.0",
                "type": "Microsoft.Communication.CallConnected",
                "source": "/acs/calls/abc123",
                "id": "acs-event-123",
                "time": "2025-08-10T13:45:00Z",
                "datacontenttype": "application/json",
                "data": {
                    "callConnectionId": "abc123",
                    "serverCallId": "server-abc123",
                    "correlationId": "correlation-123",
                },
            }
        }


class MediaWebhookEvent(WebhookEvent):
    """Media streaming webhook event."""

    type: str = Field(
        ...,
        description="Media event type",
        example="Microsoft.Media.AudioReceived",
        enum=[
            "Microsoft.Media.AudioReceived",
            "Microsoft.Media.AudioSent",
            "Microsoft.Media.TranscriptionReceived",
            "Microsoft.Media.ConnectionEstablished",
            "Microsoft.Media.ConnectionTerminated",
        ],
    )
    source: str = Field(
        ...,
        description="Media source identifier",
        pattern=r"^/media/sessions/[a-zA-Z0-9\-_]+$",
        example="/media/sessions/session123",
    )
    data: Dict[str, Any] = Field(
        ...,
        description="Media event data",
        example={
            "sessionId": "session123",
            "participantId": "participant123",
            "audioData": "base64-encoded-audio",
            "format": "PCM16",
            "sampleRate": 16000,
        },
    )

    class Config:
        json_schema_extra = {
            "example": {
                "specversion": "1.0",
                "type": "Microsoft.Media.AudioReceived",
                "source": "/media/sessions/session123",
                "id": "media-event-123",
                "time": "2025-08-10T13:45:00Z",
                "datacontenttype": "application/json",
                "data": {
                    "sessionId": "session123",
                    "participantId": "participant123",
                    "audioData": "base64-encoded-audio",
                    "format": "PCM16",
                    "sampleRate": 16000,
                },
            }
        }


class WebhookResponse(BaseModel):
    """Standard webhook response model."""

    status: str = Field(
        ...,
        description="Processing status",
        example="success",
        enum=["success", "error", "retry"],
    )
    message: str = Field(
        ...,
        description="Human-readable status message",
        example="Event processed successfully",
    )
    event_id: str = Field(
        ..., description="ID of the processed event", example="event-123-abc"
    )
    processing_time_ms: Optional[int] = Field(
        None, description="Time taken to process the event in milliseconds", example=25
    )
    details: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional processing details",
        example={
            "handler": "call_connected_handler",
            "actions_taken": ["update_call_status", "emit_notification"],
        },
    )

    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "message": "Event processed successfully",
                "event_id": "event-123-abc",
                "processing_time_ms": 25,
                "details": {
                    "handler": "call_connected_handler",
                    "actions_taken": ["update_call_status", "emit_notification"],
                },
            }
        }


class WebhookBatchRequest(BaseModel):
    """Request model for batch webhook processing."""

    events: List[Union[WebhookEvent, ACSWebhookEvent, MediaWebhookEvent]] = Field(
        ..., description="List of webhook events to process", min_items=1, max_items=100
    )
    batch_id: Optional[str] = Field(
        None, description="Optional batch identifier for tracking", example="batch-123"
    )
    processing_options: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Options for batch processing",
        example={
            "fail_fast": False,
            "parallel_processing": True,
            "max_retry_attempts": 3,
        },
    )

    class Config:
        json_schema_extra = {
            "example": {
                "events": [
                    {
                        "specversion": "1.0",
                        "type": "Microsoft.Communication.CallConnected",
                        "source": "/acs/calls/abc123",
                        "id": "event-1",
                        "time": "2025-08-10T13:45:00Z",
                        "data": {"callConnectionId": "abc123"},
                    }
                ],
                "batch_id": "batch-123",
                "processing_options": {"fail_fast": False, "parallel_processing": True},
            }
        }


class WebhookBatchResponse(BaseModel):
    """Response model for batch webhook processing."""

    batch_id: Optional[str] = Field(
        None, description="Batch identifier", example="batch-123"
    )
    total_events: int = Field(
        ..., description="Total number of events in batch", example=5
    )
    successful_events: int = Field(
        ..., description="Number of successfully processed events", example=4
    )
    failed_events: int = Field(..., description="Number of failed events", example=1)
    processing_time_ms: int = Field(
        ..., description="Total processing time in milliseconds", example=150
    )
    results: List[WebhookResponse] = Field(
        ..., description="Individual event processing results"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "batch_id": "batch-123",
                "total_events": 5,
                "successful_events": 4,
                "failed_events": 1,
                "processing_time_ms": 150,
                "results": [
                    {
                        "status": "success",
                        "message": "Event processed successfully",
                        "event_id": "event-1",
                        "processing_time_ms": 25,
                    }
                ],
            }
        }
