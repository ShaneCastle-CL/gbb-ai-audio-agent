from utils.ml_logging import get_logger
from typing import TypedDict, Dict, Any
from rtagents.RTMedAgent.backend.agents.tool_store.functions_helper import _json

logger = get_logger()


class EscalateEmergencyArgs(TypedDict):
    reason: str


async def escalate_emergency(args: EscalateEmergencyArgs) -> str:
    reason = args["reason"].strip()
    if not reason:
        return _json(False, "Reason for escalation is required.")
    return _json(True, "Emergency escalation triggered.", reason=reason)
