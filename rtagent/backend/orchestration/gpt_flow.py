from __future__ import annotations

"""OpenAI streaming + tool‑call orchestration layer.

This module handles *all* GPT chat‑completion streaming, Text‑to‑Speech (TTS)
relay, and tool‑call plumbing for a real‑time voice agent. Behaviour is kept
1:1 with the original implementation; only code style, typing, logging, and
error handling have been improved to meet project standards.

Public API
----------
process_gpt_response() – Stream chat completions, emit TTS chunks, run tools.
"""

import asyncio
import json
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi import WebSocket
from rtagent.backend.agents.tool_store.tools import (
    available_tools as DEFAULT_TOOLS,
)
from rtagent.backend.agents.tool_store.tools_helper import (
    function_mapping,
    push_tool_end,
    push_tool_start,
)
from rtagent.backend.helpers import add_space
from rtagent.backend.services.openai_services import client as az_openai_client
from rtagent.backend.settings import AZURE_OPENAI_CHAT_DEPLOYMENT_ID, TTS_END
from rtagent.backend.shared_ws import (
    broadcast_message,
    push_final,
    send_response_to_acs,
    send_tts_audio,
)

from utils.ml_logging import get_logger

if TYPE_CHECKING:  # pragma: no cover – typing‑only import
    from src.stateful.state_managment import MemoManager  # noqa: F401

logger = get_logger("gpt_flow")


# ---------------------------------------------------------------------------
# Main entry‑point
# ---------------------------------------------------------------------------
async def process_gpt_response(  # noqa: D401
    cm: "MemoManager",  # MemoManager instance (runtime import avoided)
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
) -> Optional[Dict[str, Any]]:
    """Stream a chat completion, emitting TTS and handling tool calls.

    Args:
        cm: Active :class:`MemoManager`.
        user_prompt: The raw user prompt string.
        ws: WebSocket connection to the client.
        agent_name: Identifier used to fetch the agent‑specific chat history.
        is_acs: Flag indicating Azure Communication Services pathway.
        model_id: Azure OpenAI deployment ID.
        temperature: Sampling temperature.
        top_p: Nucleus sampling value.
        max_tokens: Max tokens for the completion.
        available_tools: Tool definitions to expose; *None* defaults to the
            global *DEFAULT_TOOLS* list.

    Returns:
        Optional tool result dictionary if a tool was executed; otherwise *None*.
    """

    agent_history: List[Dict[str, Any]] = cm.get_history(agent_name)
    agent_history.append({"role": "user", "content": user_prompt})

    tool_set = available_tools or DEFAULT_TOOLS

    chat_kwargs: Dict[str, Any] = {
        "stream": True,
        "messages": agent_history,
        "model": model_id,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "tools": tool_set,
        "tool_choice": "auto" if tool_set else "none",
    }

    logger.debug("process_gpt_response – chat kwargs prepared: %s", chat_kwargs)

    response = az_openai_client.chat.completions.create(**chat_kwargs)

    collected: List[str] = []  # Temporary buffer for partial tokens.
    final_chunks: List[str] = []  # All streamed assistant chunks.

    tool_started = False
    tool_name = ""
    tool_id = ""
    args = ""

    for chunk in response:
        if not chunk.choices:
            continue  # Skip empty chunks.

        delta = chunk.choices[0].delta

        if delta.tool_calls:
            tc = delta.tool_calls[0]
            tool_id = tc.id or tool_id
            tool_name = tc.function.name or tool_name
            args += tc.function.arguments or ""
            tool_started = True
            continue

        if delta.content:
            collected.append(delta.content)
            if delta.content in TTS_END:  # Time to flush a sentence.
                streaming = add_space("".join(collected).strip())
                await _emit_streaming_text(streaming, ws, is_acs)
                final_chunks.append(streaming)
                agent_history.append({"role": "assistant", "content": streaming})
                collected.clear()

    if collected:
        pending = "".join(collected).strip()
        await _emit_streaming_text(pending, ws, is_acs)
        final_chunks.append(pending)

    full_text = "".join(final_chunks).strip()
    if full_text:
        agent_history.append({"role": "assistant", "content": full_text})
        await push_final(ws, "assistant", full_text, is_acs=is_acs)

    # ------------------------------------------------------------------
    # Handle follow‑up tool call (if any)
    # ------------------------------------------------------------------
    if tool_started:
        agent_history.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_id,
                        "type": "function",
                        "function": {"name": tool_name, "arguments": args},
                    }
                ],
            }
        )
        result = await _handle_tool_call(
            tool_name,
            tool_id,
            args,
            cm,
            ws,
            agent_name,
            is_acs,
            model_id,
            temperature,
            top_p,
            max_tokens,
            tool_set,
        )
        if result is not None:
            cm.persist_tool_output(tool_name, result)
            if isinstance(result, dict) and "slots" in result:
                cm.update_slots(result["slots"])
        return result

    return None


# ===========================================================================
# Helper routines – kept functionally identical
# ===========================================================================


async def _emit_streaming_text(
    text: str, ws: WebSocket, is_acs: bool
) -> None:  # noqa: D401,E501
    """Emit one assistant text chunk via either ACS or WebSocket + TTS."""
    if is_acs:
        await broadcast_message(ws.app.state.clients, text, "Assistant")
        await send_response_to_acs(ws, text, latency_tool=ws.state.lt)
    else:
        await send_tts_audio(text, ws, latency_tool=ws.state.lt)
        await ws.send_text(json.dumps({"type": "assistant_streaming", "content": text}))


async def _handle_tool_call(  # noqa: D401,E501,PLR0913
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
) -> Dict[str, Any]:
    """Execute a tool, emit telemetry events, and trigger GPT follow‑up."""
    params: Dict[str, Any] = json.loads(args or "{}")
    fn = function_mapping.get(tool_name)
    if fn is None:
        raise ValueError(f"Unknown tool '{tool_name}'")

    call_id = uuid.uuid4().hex[:8]
    await push_tool_start(ws, call_id, tool_name, params, is_acs=is_acs)

    t0 = time.perf_counter()
    result_raw = await fn(params)  # Tool functions are expected to be async.
    elapsed_ms = (time.perf_counter() - t0) * 1000
    result: Dict[str, Any] = (
        json.loads(result_raw) if isinstance(result_raw, str) else result_raw
    )

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
        ws, call_id, tool_name, "success", elapsed_ms, result=result, is_acs=is_acs
    )

    if is_acs:
        await broadcast_message(ws.app.state.clients, f"🛠️ {tool_name} ✔️", "Assistant")

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
    )
    return result


async def _process_tool_followup(  # noqa: D401,E501,PLR0913
    cm: "MemoManager",
    ws: WebSocket,
    agent_name: str,
    is_acs: bool,
    model_id: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    available_tools: List[Dict[str, Any]],
) -> None:
    """Invoke GPT once more after tool execution (no new user input)."""
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
    )
