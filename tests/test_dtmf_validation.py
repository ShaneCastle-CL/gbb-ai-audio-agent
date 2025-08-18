# Ensure telemetry is disabled for unit tests to avoid the ProxyLogger/resource issue
import os

# Disable cloud telemetry so utils/ml_logging avoids attaching OpenTelemetry LoggingHandler.
# This must be set before importing modules that call get_logger() at import time.
os.environ.setdefault("DISABLE_CLOUD_TELEMETRY", "true")
# Also ensure Application Insights connection string is not set (prevents other code paths)
os.environ.pop("APPLICATIONINSIGHTS_CONNECTION_STRING", None)

import asyncio
import json
import pytest
from types import SimpleNamespace

from apps.rtagent.backend.api.v1.events.handlers import CallEventHandlers

class DummyMemo:
    def __init__(self):
        self._d = {}
    def get_context(self, k, default=None):
        return self._d.get(k, default)
    def update_context(self, k, v):
        self._d[k] = v
    def persist_to_redis(self, redis_mgr):
        pass

class FakeAuthService:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []
    async def validate_pin(self, call_id, phone, pin):
        self.calls.append((call_id, phone, pin))
        # small delay to emulate I/O
        await asyncio.sleep(0.01)
        return {"ok": self.ok, "user_id": "u1"} if self.ok else {"ok": False}

@pytest.mark.asyncio
async def test_async_validate_sequence_success(monkeypatch):
    memo = DummyMemo()
    memo.update_context("dtmf_sequence", "1234")
    memo.update_context("dtmf_finalized", True)

    context = SimpleNamespace(
        call_connection_id="call-1",
        memo_manager=memo,
        redis_mgr=None,
        clients=None,
        acs_caller=None,
    )
    context.auth_service = FakeAuthService(ok=True)

    # run validator
    await CallEventHandlers._async_validate_sequence(context)

    assert memo.get_context("dtmf_validated") is True
    assert memo.get_context("entered_pin") == "1234"

@pytest.mark.asyncio
async def test_async_validate_sequence_failure(monkeypatch):
    memo = DummyMemo()
    memo.update_context("dtmf_sequence", "9999")
    memo.update_context("dtmf_finalized", True)

    context = SimpleNamespace(
        call_connection_id="call-2",
        memo_manager=memo,
        redis_mgr=None,
        clients=None,
        acs_caller=None,
    )
    context.auth_service = FakeAuthService(ok=False)

    await CallEventHandlers._async_validate_sequence(context)

    assert memo.get_context("dtmf_validated") is False
    assert memo.get_context("entered_pin") is None