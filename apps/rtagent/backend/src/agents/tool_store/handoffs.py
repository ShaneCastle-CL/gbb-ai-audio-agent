from __future__ import annotations

"""FNOL voice‑agent *escalation and hand‑off* utilities.

This module exposes **three** async callables that the LLM can invoke to
redirect the conversation flow:

1. ``handoff_general_agent`` – transfer to the *General Insurance Questions*
   AI agent whenever the caller seeks broad, non‑claim‑specific information
   (e.g., "What is covered under comprehensive?", "How do deductibles work?").
2. ``escalate_human`` – cold‑transfer to a live adjuster for fraud,
   validation loops, backend errors, or customer frustration.

All functions adhere to the project’s engineering standards (PEP 8 typing,
structured logging, error handling, and JSON responses via ``_json``).
"""

from datetime import datetime, timezone
from typing import Any, Dict, TypedDict

from apps.rtagent.backend.src.agents.tool_store.functions_helper import _json
from utils.ml_logging import get_logger

logger = get_logger("fnol_escalations")


class HandoffGeneralArgs(TypedDict):
    """Input schema for :pyfunc:`handoff_general_agent`."""

    topic: str  # e.g. "coverage", "billing", etc.
    caller_name: str


async def handoff_general_agent(args: HandoffGeneralArgs) -> Dict[str, Any]:
    """Transfer the caller to the *General Insurance Questions* AI agent.

    This path is used when the caller requests information that is **not**
    claim‑specific (e.g., "How does roadside assistance work?"), allowing the
    specialised FAQ agent to handle the inquiry.

    Returns a lightweight payload instructing the orchestrator to route the
    conversation to the named agent.
    """
    topic = args.get("topic", "").strip()
    caller_name = args.get("caller_name", "").strip()
    if not topic or not caller_name:
        return _json(False, "Both 'topic' and 'caller_name' must be provided.")

    logger.info("🤖 Hand‑off to General‑Info agent – topic: %s (caller: %s)", topic, caller_name)
    return _json(
        True,
        "Caller transferred to General Insurance Questions agent.",
        handoff="ai_agent",
        target_agent="General Insurance Questions",
        topic=topic,
    )


class EscalateHumanArgs(TypedDict):
    """Input schema for :pyfunc:`escalate_human`."""
    route_reason: str  # e.g. "validation_loop", "backend_error", "fraud_flags"
    caller_name: str
    policy_id: str


async def escalate_human(args: EscalateHumanArgs) -> Dict[str, Any]:
    """Escalate *non‑emergency* scenarios to a human insurance adjuster.

    Typical triggers include:
    • Backend errors after multiple retries.
    • Repeated validation loops (e.g., missing passenger info).
    • ≥ 2 high‑risk fraud indicators.
    • Caller frustration or profanity.
    """
    try:
        route_reason = args["route_reason"].strip()
        caller_name = args["caller_name"].strip()
        policy_id = args["policy_id"].strip()
    except KeyError as exc:  # pragma: no cover – JSON schema validation should catch
        missing = exc.args[0]
        return _json(False, f"Missing required field: {missing}.")

    if not route_reason:
        return _json(False, "route_reason must be provided.")

    logger.info(
        "🤝 Human hand‑off for %s (%s) – reason: %s", caller_name, policy_id, route_reason
    )
    return _json(
        True,
        "Caller transferred to human insurance agent.",
        route_reason=route_reason,
        handoff="human_agent",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
