from enum import Enum

# Span attribute keys for Azure App Insights OpenTelemetry logging
class SpanAttr(str, Enum):
    CORRELATION_ID = "correlation.id"
    CALL_CONNECTION_ID = "call.connection.id"
    SESSION_ID = "session.id"
    # deepcode ignore NoHardcodedCredentials: This is not a credential, but an attribute label used for Azure App Insights OpenTelemetry logging.
    USER_ID = "user.id"
    OPERATION_NAME = "operation.name"
    SERVICE_NAME = "service.name"
    SERVICE_VERSION = "service.version"
    STATUS_CODE = "status.code"
    ERROR_TYPE = "error.type"
    ERROR_MESSAGE = "error.message"
    TRACE_ID = "trace.id"
    SPAN_ID = "span.id"