from __future__ import annotations

"""OpenAI streaming + tool-call orchestration layer.

Handles GPT chat-completion streaming, TTS relay, and function-calling for the
real-time voice agent.

Public API
----------
process_gpt_response() – Stream completions, emit TTS chunks, run tools.
"""
import asyncio
import json
import os
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from fastapi import WebSocket
from opentelemetry import trace
from opentelemetry.trace import SpanKind
from urllib.parse import urlparse

from apps.rtagent.backend.settings import AZURE_OPENAI_CHAT_DEPLOYMENT_ID, TTS_END
from apps.rtagent.backend.src.agents.tool_store.tool_registry import (
    available_tools as DEFAULT_TOOLS,
)
from apps.rtagent.backend.src.agents.tool_store.tools_helper import (
    function_mapping,
    push_tool_end,
    push_tool_start,
)
from apps.rtagent.backend.src.helpers import add_space
from apps.rtagent.backend.src.services.openai_services import client as az_openai_client
from apps.rtagent.backend.src.shared_ws import (
    broadcast_message,
    push_final,
    send_response_to_acs,
    send_tts_audio,
)
from apps.rtagent.backend.settings import AZURE_OPENAI_ENDPOINT
from utils.ml_logging import get_logger
from utils.trace_context import create_trace_context
from apps.rtagent.backend.src.utils.tracing_utils import (
    create_service_handler_attrs,
    create_service_dependency_attrs,
)

if TYPE_CHECKING:  # pragma: no cover – typing-only import
    from src.stateful.state_managment import MemoManager  # noqa: F401

logger = get_logger("gpt_flow")

# Get OpenTelemetry tracer for Application Map
tracer = trace.get_tracer(__name__)

# Performance optimization: Cache tracing configuration
_GPT_FLOW_TRACING = os.getenv("GPT_FLOW_TRACING", "true").lower() == "true"
_STREAM_TRACING = os.getenv("STREAM_TRACING", "false").lower() == "true"  # High freq

JSONDict = Dict[str, Any]

# ---------------------------------------------------------------------------
# Voice + sender helpers
# ---------------------------------------------------------------------------
def _get_agent_voice_config(cm: "MemoManager") -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract agent voice configuration from memory manager.

    :param cm: The active MemoManager instance for conversation state
    :return: Tuple of (voice_name, voice_style, voice_rate) or (None, None, None)
    :raises: None (handles exceptions gracefully with fallback values)
    """
    if cm is None:
        logger.warning("MemoManager is None, using default voice configuration")
        return None, None, None

    try:
        voice_name = cm.get_value_from_corememory("current_agent_voice")
        voice_style = cm.get_value_from_corememory("current_agent_voice_style", "chat")
        voice_rate = cm.get_value_from_corememory("current_agent_voice_rate", "+3%")
        return voice_name, voice_style, voice_rate
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to get agent voice config: %s", exc)
        return None, None, None


def _get_agent_sender_name(cm: "MemoManager", *, include_autoauth: bool = True) -> str:
    """Resolve the visible sender name for dashboard / UI.

    :param cm: MemoManager instance for reading conversation context
    :param include_autoauth: When True, map active_agent=="AutoAuth" to "Auth Agent"
    :return: Human-friendly speaker label for display
    :raises: None (handles exceptions gracefully with fallback to "Assistant")
    """
    try:
        active_agent = cm.get_value_from_corememory("active_agent") if cm else None
        authenticated = cm.get_value_from_corememory("authenticated") if cm else False

        if active_agent == "Claims":
            return "Claims Specialist"
        if active_agent == "General":
            return "General Info"
        if include_autoauth and active_agent == "AutoAuth":
            return "Auth Agent"
        if not authenticated:
            return "Auth Agent"
        return "Assistant"
    except Exception:  # noqa: BLE001
        return "Assistant"


# ---------------------------------------------------------------------------
# Emission helpers
# ---------------------------------------------------------------------------

async def _emit_streaming_text(
    text: str,
    ws: WebSocket,
    is_acs: bool,
    cm: "MemoManager",
    call_connection_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Emit one assistant text chunk via either ACS or WebSocket + TTS.

    :param text: The text chunk to emit to client
    :param ws: Active WebSocket connection instance
    :param is_acs: Whether to route via Azure Communication Services
    :param cm: MemoManager for voice config and speaker labels
    :param call_connection_id: Optional correlation ID for tracing
    :param session_id: Optional session ID for tracing correlation
    :return: None
    :raises Exception: Re-raises any exceptions from TTS or ACS emission
    """
    voice_name, voice_style, voice_rate = _get_agent_voice_config(cm)

    if _STREAM_TRACING:
        span_attrs = create_service_handler_attrs(
            service_name="gpt_flow",
            call_connection_id=call_connection_id,
            session_id=session_id,
            operation="emit_streaming_text",
            text_length=len(text),
            is_acs=is_acs,
            chunk_type="streaming_text",
        )
        with tracer.start_as_current_span(
            "gpt_flow.emit_streaming_text", attributes=span_attrs
        ) as span:
            try:
                if is_acs:
                    span.set_attribute("output_channel", "acs")
                    await send_response_to_acs(
                        ws,
                        text,
                        latency_tool=ws.state.lt,
                        voice_name=voice_name,
                        voice_style=voice_style,
                        rate=voice_rate,
                    )
                else:
                    span.set_attribute("output_channel", "websocket_tts")
                    await send_tts_audio(
                        text,
                        ws,
                        latency_tool=ws.state.lt,
                        voice_name=voice_name,
                        voice_style=voice_style,
                        rate=voice_rate,
                    )
                    speaker = _get_agent_sender_name(cm, include_autoauth=True)
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "assistant_streaming",
                                "content": text,
                                "speaker": speaker,
                            }
                        )
                    )
                span.add_event("text_emitted", {"text_length": len(text)})
            except Exception as exc:  # noqa: BLE001
                span.record_exception(exc)
                logger.exception("Failed to emit streaming text")
                raise
    else:
        # Fast path when high-frequency tracing is disabled
        if is_acs:
            await send_response_to_acs(
                ws,
                text,
                latency_tool=ws.state.lt,
                voice_name=voice_name,
                voice_style=voice_style,
                rate=voice_rate,
            )
        else:
            await send_tts_audio(
                text,
                ws,
                latency_tool=ws.state.lt,
                voice_name=voice_name,
                voice_style=voice_style,
                rate=voice_rate,
            )
            speaker = _get_agent_sender_name(cm, include_autoauth=True)
            await ws.send_text(
                json.dumps(
                    {"type": "assistant_streaming", "content": text, "speaker": speaker}
                )
            )


async def _broadcast_dashboard(
    ws: WebSocket,
    cm: "MemoManager",
    message: str,
    *,
    include_autoauth: bool,
) -> None:
    """Broadcast a message to the relay dashboard with correct speaker label.

    :param ws: WebSocket connection carrying application state
    :param cm: MemoManager instance for resolving speaker labels
    :param message: Text message to broadcast to dashboard
    :param include_autoauth: Flag to match legacy behavior at call-sites
    :return: None
    :raises: None (handles exceptions gracefully with logging)
    """
    try:
        sender = _get_agent_sender_name(cm, include_autoauth=include_autoauth)
        logger.info(f"🎯 _broadcast_dashboard called: sender='{sender}', include_autoauth={include_autoauth}, message='{message[:50]}...'")
        clients = await ws.app.state.websocket_manager.get_clients_snapshot()
        await broadcast_message(clients, message, sender)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to broadcast dashboard message: %s", exc)


# ---------------------------------------------------------------------------
# Chat + streaming helpers
# ---------------------------------------------------------------------------

def _build_chat_kwargs(
    *,
    history: List[JSONDict],
    model_id: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    tools: Optional[List[JSONDict]],
) -> JSONDict:
    """Build Azure OpenAI chat-completions kwargs.

    :param history: List of conversation messages for chat context
    :param model_id: Azure OpenAI model deployment identifier
    :param temperature: Sampling temperature for response generation
    :param top_p: Nucleus sampling parameter for response diversity
    :param max_tokens: Maximum number of tokens to generate
    :param tools: Optional list of tool definitions for function calling
    :return: Dictionary suitable for az_openai_client.chat.completions.create
    :raises: None
    """
    return {
        "stream": True,
        "messages": history,
        "model": model_id,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "tools": tools or [],
        "tool_choice": "auto" if (tools or []) else "none",
    }

class _ToolCallState:
    """Minimal state carrier for a single tool call parsed from stream deltas."""

    def __init__(self) -> None:
        self.started: bool = False
        self.name: str = ""
        self.call_id: str = ""
        self.args_json: str = ""

async def _consume_openai_stream(
    response_stream: Any,
    ws: WebSocket,
    is_acs: bool,
    cm: "MemoManager",
    call_connection_id: Optional[str],
    session_id: Optional[str],
) -> Tuple[str, _ToolCallState]:
    """Consume the AOAI stream, emitting TTS chunks as punctuation arrives.

    :param response_stream: Azure OpenAI streaming response object
    :param ws: WebSocket connection for client communication
    :param is_acs: Flag indicating Azure Communication Services pathway
    :param cm: MemoManager instance for conversation state
    :param call_connection_id: Optional correlation ID for tracing
    :param session_id: Optional session ID for tracing correlation
    :return: Tuple of (full_assistant_text, tool_call_state)
    :raises: May raise exceptions from streaming or emission operations
    """
    collected: List[str] = []  # temporary sentence buffer
    final_chunks: List[str] = []  # full assistant text
    tool = _ToolCallState()

    for chunk in response_stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        # Tool-call aggregation (function name + arguments as they stream)
        if getattr(delta, "tool_calls", None):
            tc = delta.tool_calls[0]
            tool.call_id = tc.id or tool.call_id
            tool.name = getattr(tc.function, "name", None) or tool.name
            tool.args_json += getattr(tc.function, "arguments", None) or ""
            if not tool.started:
                tool.started = True
            continue

        # Text streaming (flush on boundaries in TTS_END)
        if getattr(delta, "content", None):
            collected.append(delta.content)
            if delta.content in TTS_END:
                streaming = add_space("".join(collected).strip())
                logger.info("process_gpt_response – streaming text chunk: %s", streaming)
                await _emit_streaming_text(
                    streaming, ws, is_acs, cm, call_connection_id, session_id
                )
                final_chunks.append(streaming)
                collected.clear()

    # Handle trailing content (no terminating punctuation)
    if collected:
        pending = "".join(collected).strip()
        if pending:
            await _emit_streaming_text(pending, ws, is_acs, cm, call_connection_id, session_id)
            final_chunks.append(pending)

    return "".join(final_chunks).strip(), tool


async def process_gpt_response(  
    cm: "MemoManager",
    user_prompt: str,
    ws: WebSocket,
    *,
    agent_name: str,
    is_acs: bool = False,
    model_id: str = AZURE_OPENAI_CHAT_DEPLOYMENT_ID,
    temperature: float = 0.5,
    top_p: float = 1.0,
    max_tokens: int = 4096,
    available_tools: Optional[List[Dict[str, Any]]] = None,
    call_connection_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Stream a chat completion, emitting TTS and handling tool calls.

    :param cm: Active MemoManager instance for conversation state
    :param user_prompt: The raw user prompt string input
    :param ws: WebSocket connection to the client
    :param agent_name: Identifier used to fetch agent-specific chat history
    :param is_acs: Flag indicating Azure Communication Services pathway
    :param model_id: Azure OpenAI deployment ID for model selection
    :param temperature: Sampling temperature for response generation
    :param top_p: Nucleus sampling value for response diversity
    :param max_tokens: Maximum tokens for the completion response
    :param available_tools: Tool definitions to expose, defaults to DEFAULT_TOOLS
    :param call_connection_id: ACS call connection ID for tracing correlation
    :param session_id: Session ID for tracing correlation
    :return: Optional tool result dictionary if a tool was executed, None otherwise
    :raises: May raise exceptions from Azure OpenAI streaming or tool execution
    """
    # Create handler span for GPT flow service
    span_attrs = create_service_handler_attrs(
        service_name="gpt_flow",
        call_connection_id=call_connection_id,
        session_id=session_id,
        operation="process_response",
        agent_name=agent_name,
        model_id=model_id,
        is_acs=is_acs,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        tools_available=len(available_tools or DEFAULT_TOOLS),
        prompt_length=len(user_prompt) if user_prompt else 0,
    )

    with tracer.start_as_current_span(
        "gpt_flow.process_response", attributes=span_attrs
    ) as span:
        # Build history and tools
        agent_history: List[JSONDict] = cm.get_history(agent_name)
        agent_history.append({"role": "user", "content": user_prompt})
        tool_set = available_tools or DEFAULT_TOOLS
        span.set_attribute("tools.count", len(tool_set))

        chat_kwargs = _build_chat_kwargs(
            history=agent_history,
            model_id=model_id,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            tools=tool_set,
        )
        span.set_attribute("chat.history_length", len(agent_history))
        logger.debug("process_gpt_response – chat kwargs prepared: %s", chat_kwargs)

        # Create dependency span for calling Azure OpenAI
        azure_openai_attrs = create_service_dependency_attrs(
            source_service="gpt_flow",
            target_service="azure_openai",
            call_connection_id=call_connection_id,
            session_id=session_id,
            operation="stream_completion",
            model=model_id,
            stream=True,
        )
        aoai_endpoint = AZURE_OPENAI_ENDPOINT
        host = urlparse(aoai_endpoint).netloc or "api.openai.azure.com"

        tool_state = _ToolCallState()
        try:
            with tracer.start_as_current_span(
                "gpt_flow.stream_completion",
                kind=SpanKind.CLIENT,
                attributes={
                    **azure_openai_attrs,
                    "peer.service": "azure-openai",
                    "server.address": host,
                    "server.port": 443,
                    "http.method": "POST",
                    "http.url": f"https://{host}/openai/deployments/{model_id}/chat/completions",
                    "pipeline.stage": "orchestrator -> aoai",
                },
            ) as stream_span:
                response = az_openai_client.chat.completions.create(**chat_kwargs)
                stream_span.add_event("openai_stream_started")

                # Consume the stream and emit chunks as before
                full_text, tool_state = await _consume_openai_stream(
                    response, ws, is_acs, cm, call_connection_id, session_id
                )

                stream_span.set_attribute("tool_call_detected", tool_state.started)
                if tool_state.started:
                    stream_span.set_attribute("tool_name", tool_state.name)
        except Exception as exc:  # noqa: BLE001
            logger.exception("AOAI streaming failed")
            span.record_exception(exc)
            raise

        # Finalize assistant text
        if full_text:
            agent_history.append({"role": "assistant", "content": full_text})
            await push_final(ws, "assistant", full_text, is_acs=is_acs)
            # Broadcast the final assistant response to relay dashboard
            await _broadcast_dashboard(
                ws, cm, full_text, include_autoauth=False  # preserve legacy behavior
            )
            span.set_attribute("response.length", len(full_text))

        # Handle follow-up tool call (if any)
        if tool_state.started:
            span.add_event(
                "tool_execution_starting",
                {"tool_name": tool_state.name, "tool_id": tool_state.call_id},
            )

            agent_history.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tool_state.call_id,
                            "type": "function",
                            "function": {
                                "name": tool_state.name,
                                "arguments": tool_state.args_json,
                            },
                        }
                    ],
                }
            )
            result = await _handle_tool_call(
                tool_state.name,
                tool_state.call_id,
                tool_state.args_json,
                cm,
                ws,
                agent_name,
                is_acs,
                model_id,
                temperature,
                top_p,
                max_tokens,
                tool_set,
                call_connection_id,
                session_id,
            )
            if result is not None:
                # Persist tool output and update slots in the background
                async def persist_tool_results() -> None:
                    cm.persist_tool_output(tool_state.name, result)
                    if isinstance(result, dict) and "slots" in result:
                        cm.update_slots(result["slots"])

                asyncio.create_task(persist_tool_results())
                span.set_attribute("tool.execution_success", True)
                span.add_event("tool_execution_completed", {"tool_name": tool_state.name})
            return result

        span.set_attribute("completion_type", "text_only")
        return None


# ---------------------------------------------------------------------------
# Tool handling
# ---------------------------------------------------------------------------


async def _handle_tool_call(  # noqa: PLR0913
    tool_name: str,
    tool_id: str,
    args: str,
    cm: "MemoManager",
    ws: WebSocket,
    agent_name: str,
    is_acs: bool,
    model_id: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    available_tools: List[Dict[str, Any]],
    call_connection_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a tool, emit telemetry events, and trigger GPT follow-up.

    :param tool_name: Name of the tool function to execute
    :param tool_id: Unique identifier for this tool call instance
    :param args: JSON string containing tool function arguments
    :param cm: MemoManager instance for conversation state
    :param ws: WebSocket connection for client communication
    :param agent_name: Identifier for the calling agent context
    :param is_acs: Flag indicating Azure Communication Services pathway
    :param model_id: Azure OpenAI model deployment identifier
    :param temperature: Sampling temperature for follow-up responses
    :param top_p: Nucleus sampling value for follow-up responses
    :param max_tokens: Maximum tokens for follow-up completions
    :param available_tools: List of available tool definitions
    :param call_connection_id: Optional correlation ID for tracing
    :param session_id: Optional session ID for tracing correlation
    :return: Parsed result dictionary from the tool execution
    :raises ValueError: If tool_name does not exist in function_mapping
    """
    with create_trace_context(
        name="gpt_flow.handle_tool_call",
        call_connection_id=call_connection_id,
        session_id=session_id,
        metadata={
            "tool_name": tool_name,
            "tool_id": tool_id,
            "agent_name": agent_name,
            "is_acs": is_acs,
            "args_length": len(args) if args else 0,
        },
    ) as trace_ctx:
        params: JSONDict = json.loads(args or "{}")
        fn = function_mapping.get(tool_name)
        if fn is None:
            trace_ctx.set_attribute("error", f"Unknown tool '{tool_name}'")
            raise ValueError(f"Unknown tool '{tool_name}'")

        trace_ctx.set_attribute("tool.parameters_count", len(params))
        call_short_id = uuid.uuid4().hex[:8]
        trace_ctx.set_attribute("tool.call_id", call_short_id)

        await push_tool_start(ws, call_short_id, tool_name, params, is_acs=is_acs)
        trace_ctx.add_event("tool_start_pushed", {"call_id": call_short_id})

        # Execute tool with nested tracing
        with create_trace_context(
            name=f"gpt_flow.execute_tool.{tool_name}",
            call_connection_id=call_connection_id,
            session_id=session_id,
            metadata={"tool_name": tool_name, "call_id": call_short_id, "parameters": params},
        ) as exec_ctx:
            t0 = time.perf_counter()
            result_raw = await fn(params)  # Tool functions are expected to be async.
            elapsed_ms = (time.perf_counter() - t0) * 1000

            exec_ctx.set_attribute("execution.duration_ms", elapsed_ms)
            exec_ctx.set_attribute("execution.success", True)

            result: JSONDict = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
            exec_ctx.set_attribute("result.type", type(result).__name__)

        agent_history = cm.get_history(agent_name)
        agent_history.append(
            {
                "tool_call_id": tool_id,
                "role": "tool",
                "name": tool_name,
                "content": json.dumps(result),
            }
        )

        await push_tool_end(
            ws, call_short_id, tool_name, "success", elapsed_ms, result=result, is_acs=is_acs
        )
        trace_ctx.add_event("tool_end_pushed", {"elapsed_ms": elapsed_ms})

        # Broadcast tool completion to relay dashboard (only for ACS calls)
        if is_acs:
            await _broadcast_dashboard(ws, cm, f"🛠️ {tool_name} ✔️", include_autoauth=False)

        # Handle tool follow-up with tracing
        trace_ctx.add_event("starting_tool_followup")
        await _process_tool_followup(
            cm,
            ws,
            agent_name,
            is_acs,
            model_id,
            temperature,
            top_p,
            max_tokens,
            available_tools,
            call_connection_id,
            session_id,
        )

        trace_ctx.set_attribute("tool.execution_complete", True)
        return result


async def _process_tool_followup(  # noqa: PLR0913
    cm: "MemoManager",
    ws: WebSocket,
    agent_name: str,
    is_acs: bool,
    model_id: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    available_tools: List[Dict[str, Any]],
    call_connection_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Invoke GPT once more after tool execution (no new user input).

    :param cm: MemoManager instance for conversation state
    :param ws: WebSocket connection for client communication
    :param agent_name: Identifier for the calling agent context
    :param is_acs: Flag indicating Azure Communication Services pathway
    :param model_id: Azure OpenAI model deployment identifier
    :param temperature: Sampling temperature for follow-up responses
    :param top_p: Nucleus sampling value for follow-up responses
    :param max_tokens: Maximum tokens for follow-up completions
    :param available_tools: List of available tool definitions
    :param call_connection_id: Optional correlation ID for tracing
    :param session_id: Optional session ID for tracing correlation
    :return: None
    :raises: May raise exceptions from process_gpt_response call
    """
    with create_trace_context(
        name="gpt_flow.tool_followup",
        call_connection_id=call_connection_id,
        session_id=session_id,
        metadata={
            "agent_name": agent_name,
            "model_id": model_id,
            "is_acs": is_acs,
            "followup_type": "post_tool_execution",
        },
    ) as trace_ctx:
        trace_ctx.add_event("starting_followup_completion")

        await process_gpt_response(
            cm,
            "",  # No new user prompt.
            ws,
            agent_name=agent_name,
            is_acs=is_acs,
            model_id=model_id,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            available_tools=available_tools,
            call_connection_id=call_connection_id,
            session_id=session_id,
        )

        trace_ctx.add_event("followup_completion_finished")