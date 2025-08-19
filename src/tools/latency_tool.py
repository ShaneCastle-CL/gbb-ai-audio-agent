from __future__ import annotations

from typing import Any, Dict, Optional

from utils.ml_logging import get_logger
from src.tools.latency_helpers import PersistentLatency

logger = get_logger("tools.latency")

class LatencyTool:
    """
    Backwards-compatible wrapper used at WS layer.

    start(stage) / stop(stage, redis_mgr) keep working,
    but data is written into CoreMemory["latency"] with a per-run grouping.
    """

    def __init__(self, cm):
        self.cm = cm
        self._store = PersistentLatency(cm)

    # Optional: set current run for this connection
    def set_current_run(self, run_id: str) -> None:
        self._store.set_current_run(run_id)

    def get_current_run(self) -> Optional[str]:
        return self._store.current_run_id()

    def begin_run(self, label: str = "turn") -> str:
        rid = self._store.begin_run(label=label)
        return rid

    def start(self, stage: str) -> None:
        self._store.start(stage)

    def stop(self, stage: str, redis_mgr, *, meta: Optional[Dict[str, Any]] = None) -> None:
        self._store.stop(stage, redis_mgr=redis_mgr, meta=meta)

    # convenient summaries for dashboards
    def session_summary(self):
        return self._store.session_summary()

    def run_summary(self, run_id: str):
        return self._store.run_summary(run_id)
