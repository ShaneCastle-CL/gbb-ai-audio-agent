from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, Dict, TYPE_CHECKING

from fastapi import WebSocket

from utils.ml_logging import get_logger

if TYPE_CHECKING:                                     # pragma: no cover
    from src.stateful.state_managment import MemoManager

logger = get_logger("fnol_route")


def _cm_get(cm: "MemoManager", key: str, default: Any = None) -> Any:
    return cm.get_value_from_corememory(key, default)


def _cm_set(cm: "MemoManager", **kwargs: Dict[str, Any]) -> None:
    for k, v in kwargs.items():
        cm.update_corememory(k, v)

@asynccontextmanager
async def track_latency(timer, label: str, redis_mgr):
    """Start/stop latency instrumentation with guaranteed cleanup."""
    timer.start(label)
    try:
        yield
    finally:
        timer.stop(label, redis_mgr)

async def handle_authentication(
    memo: "MemoManager",
    utterance: str,
    ws: WebSocket,
    *,
    is_acs: bool,
) -> bool:  # True = authenticated or already authenticated
    if _cm_get(memo, "authenticated", False):
        return True

    auth_agent = ws.app.state.auth_agent  # type: ignore[attr-defined]
    async with track_latency(ws.state.lt, "auth_agent", ws.app.state.redis):
        result = await auth_agent.respond(memo, utterance, ws, is_acs=is_acs)

    if isinstance(result, dict) and result.get("authenticated"):
        _cm_set(
            memo,
            authenticated=True,
            caller_name=result.get("caller_name"),
            policy_id=result.get("policy_id"),
            call_reason=result.get("call_reason"),
        )
        logger.info(
            "✅ Session %s authenticated – %s / %s",
            memo.session_id,
            result.get("caller_name"),
            result.get("policy_id"),
        )
        return True

    return False

async def handle_intake(
    memo: "MemoManager",
    utterance: str,
    ws: WebSocket,
    *,
    is_acs: bool,
) -> None:
    fnol_agent = ws.app.state.claim_intake_agent  # type: ignore[attr-defined]
    async with track_latency(ws.state.lt, "fnol_agent", ws.app.state.redis):
        result = await fnol_agent.respond(
            memo,
            utterance,
            ws,
            is_acs=is_acs,
            call_reason=_cm_get(memo, "call_reason"),
            caller_name=_cm_get(memo, "caller_name"),
            policy_id=_cm_get(memo, "policy_id"),
        )

    if isinstance(result, dict) and result.get("claim_success"):
        claim_id: str = result["claim_id"]
        _cm_set(memo, intake_completed=True)
        logger.info("📄 FNOL completed – %s – session %s", claim_id, memo.session_id)
        await ws.send_text(json.dumps({"type": "claim_submitted", "claim_id": claim_id}))

async def route_turn(  # noqa: D401
    cm: "MemoManager",
    transcript: str,
    ws: WebSocket,
    *,
    is_acs: bool,
) -> None:
    """Handle exactly one user turn (utterance → agents → response)."""
    redis_mgr = ws.app.state.redis

    try:
        if await handle_authentication(cm, transcript, ws, is_acs=is_acs):
            # Either already authenticated or just succeeded; proceed to intake.
            await handle_intake(cm, transcript, ws, is_acs=is_acs)

    except Exception as exc:
        logger.exception("💥 Error in route_turn for session %s", cm.session_id)
        raise exc

    finally:
        # Persist every turn—success, validation error, or crash.
        cm.persist_to_redis(redis_mgr)
