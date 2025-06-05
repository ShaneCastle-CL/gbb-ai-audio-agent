import uuid
import json
from typing import Any, Dict, List, Optional
import asyncio
from collections import deque

from rtagents.RTAgent.backend.agents.prompt_store.prompt_manager import PromptManager
from src.redis.manager import AzureRedisManager
from statistics import mean
from utils.ml_logging import get_logger

logger = get_logger()


class ConversationManager:
    def __init__(self, auth: bool = False, session_id: Optional[str] = None, 
                 auto_refresh_interval: Optional[float] = None) -> None:
        self.pm: PromptManager = PromptManager()
        self.session_id: str = session_id or str(uuid.uuid4())[:8]
        self.hist: List[Dict[str, Any]] = []
        self.context: Dict[str, Any] = {"authenticated": auth}
        # Message queue for sequential TTS playback
        self.message_queue: deque = deque()
        self.queue_lock = asyncio.Lock()
        self.is_processing_queue = False
        
        # Auto-refresh configuration
        self.auto_refresh_interval = auto_refresh_interval
        self.last_refresh_time = 0
        self._refresh_task: Optional[asyncio.Task] = None
        self._redis_manager: Optional[Any] = None

    @staticmethod
    def build_redis_key(session_id: str) -> str:
        return f"session:{session_id}"

    def to_redis_dict(self) -> Dict[str, str]:
        return {
            "history": json.dumps(self.hist, ensure_ascii=False),
            "context": json.dumps(self.context, ensure_ascii=False),
        }

    @classmethod
    def from_redis(
        cls, session_id: str, redis_mgr: AzureRedisManager
    ) -> "ConversationManager":
        key = cls.build_redis_key(session_id)
        data = redis_mgr.get_session_data(key)
        cm = cls(session_id=session_id)
        if "history" in data:
            cm.hist = json.loads(data["history"])
        if "context" in data:
            cm.context = json.loads(data["context"])
        logger.info(
            f"Restored session {session_id}: "
            f"{len(cm.hist)} msgs, ctx keys={list(cm.context.keys())}"
        )
        return cm

    def persist_to_redis(
        self, redis_mgr: AzureRedisManager, ttl_seconds: Optional[int] = None
    ) -> None:
        key = self.build_redis_key(self.session_id)
        redis_mgr.store_session_data(key, self.to_redis_dict())
        if ttl_seconds:
            redis_mgr.redis_client.expire(key, ttl_seconds)
        logger.info(
            f"Persisted session {self.session_id} – "
            f"history={len(self.hist)}, ctx_keys={list(self.context.keys())}"
        )

    async def persist_to_redis_async(
        self, redis_mgr: AzureRedisManager, ttl_seconds: Optional[int] = None
    ) -> None:
        """Async version of persist_to_redis to avoid blocking the event loop."""
        key = self.build_redis_key(self.session_id)
        await redis_mgr.store_session_data_async(key, self.to_redis_dict())
        if ttl_seconds:
            # Run expire in executor to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, redis_mgr.redis_client.expire, key, ttl_seconds)
        logger.info(
            f"Persisted session {self.session_id} async – "
            f"history={len(self.hist)}, ctx_keys={list(self.context.keys())}"
        )

    def update_context(self, key: str, value: Any) -> None:
        self.context[key] = value

    def get_context(self, key: str, default: Any = None) -> Any:
        return self.context.get(key, default)

    _LAT_KEY = "latency_roundtrip"

    def _latency_bucket(self) -> Dict[str, List[Dict[str, float]]]:
        """
        Return (and lazily create) the latency dictionary that lives
        inside `context`.
        """
        return self.context.setdefault(self._LAT_KEY, {})

    def note_latency(self, stage: str, start_t: float, end_t: float) -> None:
        self._latency_bucket().setdefault(stage, []).append(
            {"start": start_t, "end": end_t, "dur": end_t - start_t}
        )

    def latency_summary(self) -> Dict[str, Dict[str, float]]:
        """
        Compute avg/min/max/count for each stage.  This is *on demand*
        (nothing persisted).
        """
        out: Dict[str, Dict[str, float]] = {}
        for stage, samples in self._latency_bucket().items():
            durations = [s["dur"] for s in samples]
            out[stage] = {
                "count": len(durations),
                "avg": mean(durations) if durations else 0.0,
                "min": min(durations) if durations else 0.0,
                "max": max(durations) if durations else 0.0,
            }
        return out

    def append_to_history(self, role: str, content: str) -> None:
        self.hist.append({"role": role, "content": content})

    def _build_full_name(self) -> str:
        return f"{self.get_context('first_name', 'Alice')} {self.get_context('last_name', 'Brown')}"

    def _generate_system_prompt(self) -> str:
        try:
            if self.get_context("authenticated", False):
                return self.pm.create_prompt_system_main(
                    patient_phone_number=self.get_context("phone_number", "5552971078"),
                    patient_name=self._build_full_name(),
                    patient_dob=self.get_context("patient_dob", "1987-04-12"),
                    patient_id=self.get_context("patient_id", "P54321"),
                )
            return self.pm.get_prompt("voice_agent_authentication.jinja")
        except Exception as exc:  # noqa: BLE001
            logger.error("Unable to generate system prompt", exc_info=True)
            raise exc

    def ensure_system_prompt(self) -> None:
        if not any(m["role"] == "system" for m in self.hist):
            self.hist.insert(
                0, {"role": "system", "content": self._generate_system_prompt()}
            )

    def upsert_system_prompt(self) -> None:
        new_prompt = self._generate_system_prompt()
        for msg in self.hist:
            if msg["role"] == "system":
                msg["content"] = new_prompt
                return
        self.hist.insert(0, {"role": "system", "content": new_prompt})

    # --- MESSAGE QUEUE MANAGEMENT -------------------------------------

    async def enqueue_message(
        self, 
        response_text: str,
        use_ssml: bool = False,
        voice_name: str = None,
        locale: str = "en-US",
        participants: list = None,
        max_retries: int = 5,
        initial_backoff: float = 0.5,
        transcription_resume_delay: float = 1.0
    ) -> None:
        """Add a message to the queue for sequential playback."""
        async with self.queue_lock:
            message_data = {
                "response_text": response_text,
                "use_ssml": use_ssml,
                "voice_name": voice_name,
                "locale": locale,
                "participants": participants,
                "max_retries": max_retries,
                "initial_backoff": initial_backoff,
                "transcription_resume_delay": transcription_resume_delay,
                "timestamp": asyncio.get_event_loop().time()
            }
            self.message_queue.append(message_data)
            logger.info(f"📝 Enqueued message for session {self.session_id}. Queue size: {len(self.message_queue)}")

    async def get_next_message(self) -> Optional[Dict[str, Any]]:
        """Get the next message from the queue."""
        async with self.queue_lock:
            if self.message_queue:
                return self.message_queue.popleft()
            return None

    async def clear_queue(self) -> None:
        """Clear all queued messages."""
        async with self.queue_lock:
            self.message_queue.clear()
            logger.info(f"🗑️ Cleared message queue for session {self.session_id}")

    def get_queue_size(self) -> int:
        """Get the current queue size."""
        return len(self.message_queue)

    async def set_queue_processing_status(self, is_processing: bool) -> None:
        """Set the queue processing status."""
        async with self.queue_lock:
            self.is_processing_queue = is_processing
            logger.debug(f"🔄 Queue processing status for session {self.session_id}: {is_processing}")

    def is_queue_processing(self) -> bool:
        """Check if the queue is currently being processed."""
        return self.is_processing_queue

    async def refresh_from_redis_async(self, redis_mgr) -> bool:
        """
        Refresh the current session with live data from Redis.
        Returns True if data was found and loaded, False otherwise.
        """
        key = self.build_redis_key(self.session_id)
        try:
            data = await redis_mgr.get_session_data_async(key)
            if not data:
                logger.warning(f"No live data found for session {self.session_id}")
                return False
                
            # Update history if present
            if "history" in data:
                new_history = json.loads(data["history"])
                if new_history != self.hist:
                    logger.info(f"Refreshed history for session {self.session_id}")
                    self.hist = new_history
            
            # Update context if present (preserving queue state)
            if "context" in data:
                new_context = json.loads(data["context"])
                # Preserve queue state and locks
                current_queue_size = self.get_queue_size()
                
                self.context = new_context
                
                # Update queue from context if it exists
                if "message_queue" in new_context:
                    async with self.queue_lock:
                        self.message_queue = deque(new_context["message_queue"])
                        del self.context["message_queue"]  # Remove from context to avoid duplication
                
            logger.info(f"Successfully refreshed live data for session {self.session_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to refresh live data for session {self.session_id}: {e}")
            return False

    async def get_live_context_value(self, redis_mgr, key: str, default: Any = None) -> Any:
        """Get a specific context value from live Redis data."""
        try:
            redis_key = self.build_redis_key(self.session_id)
            data = await redis_mgr.get_session_data_async(redis_key)
            if data and "context" in data:
                context = json.loads(data["context"])
                return context.get(key, default)
            return default
        except Exception as e:
            logger.error(f"Failed to get live context value '{key}' for session {self.session_id}: {e}")
            return default

    async def set_live_context_value(self, redis_mgr, key: str, value: Any) -> bool:
        """Set a specific context value in both local state and Redis."""
        try:
            self.context[key] = value
            await self.persist_async(redis_mgr)
            logger.debug(f"Set live context value '{key}' = {value} for session {self.session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to set live context value '{key}' for session {self.session_id}: {e}")
            return False

    def enable_auto_refresh(self, redis_mgr, interval_seconds: float = 30.0):
        """Enable automatic refresh of data from Redis at specified intervals."""
        self._redis_manager = redis_mgr
        self.auto_refresh_interval = interval_seconds
        
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            
        self._refresh_task = asyncio.create_task(self._auto_refresh_loop())
        logger.info(f"Enabled auto-refresh every {interval_seconds}s for session {self.session_id}")

    def disable_auto_refresh(self):
        """Disable automatic refresh."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = None
        self._redis_manager = None
        logger.info(f"Disabled auto-refresh for session {self.session_id}")

    async def _auto_refresh_loop(self):
        """Internal method to handle automatic refresh loop."""
        while self.auto_refresh_interval and self._redis_manager:
            try:
                await asyncio.sleep(self.auto_refresh_interval)
                await self.refresh_from_redis_async(self._redis_manager)
                self.last_refresh_time = asyncio.get_event_loop().time()
            except asyncio.CancelledError:
                logger.info(f"Auto-refresh cancelled for session {self.session_id}")
                break
            except Exception as e:
                logger.error(f"Auto-refresh error for session {self.session_id}: {e}")
                # Continue the loop despite errors
