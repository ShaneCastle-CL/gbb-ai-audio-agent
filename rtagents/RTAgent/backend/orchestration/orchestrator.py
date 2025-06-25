# routes/fnol_route.py
from __future__ import annotations

import json, time
from fastapi import WebSocket
from utils.ml_logging import get_logger

logger = get_logger("fnol_route")


async def route_turn(cm, transcript: str, ws: WebSocket, *, is_acs: bool) -> None:
    """
    Two-stage FNOL flow:
        1) authenticate_caller  (AuthAgent)
        2) record_fnol          (FNOLIntakeAgent)
    After a successful FNOL record the websocket is closed by the caller.
    """
    redis_mgr     = ws.app.state.redis
    latency_tool  = ws.state.lt

    if not cm.get_context("authenticated", False):
        latency_tool.start("auth_agent")
        auth_agent = getattr(ws.app.state, "auth_agent")
        result     = await auth_agent.respond(cm, transcript, ws, is_acs=is_acs)
        latency_tool.stop("auth_agent", redis_mgr)

        if isinstance(result, dict) and result.get("authenticated"):
            cm.update_context("authenticated", True)
            cm.update_context("caller_name", result["caller_name"])
            cm.update_context("policy_id",  result["policy_id"])
            logger.info(f"✅ Session {cm.session_id} authenticated for "
                        f"{result['caller_name']} / {result['policy_id']}")
        else:
            # AuthAgent handles retry messaging itself; just persist state.
            cm.persist_to_redis(redis_mgr)
            return
    else:
        fnol_agent = getattr(ws.app.state, "claim_intake_agent")
        latency_tool.start("fnol_agent")
        result     = await fnol_agent.respond(cm, transcript, ws, is_acs=is_acs)
        latency_tool.stop("fnol_agent", redis_mgr)

        if isinstance(result, dict) and result.get("claim_success"):
            # Intake finished → mark and log
            cm.update_context("intake_completed", True)
            claim_id = result["claim_id"]
            logger.info(f"📄 FNOL completed – {claim_id} – "
                        f"session {cm.session_id}")
            await ws.send_text(json.dumps({
                "type": "claim_submitted",
                "claim_id": claim_id,
                
            }))
            # Up to you: close socket now or let caller hang up
            # await ws.close(code=1000)

    cm.persist_to_redis(redis_mgr)
