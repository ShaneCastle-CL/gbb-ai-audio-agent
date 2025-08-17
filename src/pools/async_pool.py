import asyncio
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")

class AsyncPool:
    """
    Minimal async pool backed by asyncio.Queue.
    Use for expensive-but-reusable clients (e.g., STT/TTS).
    """
    def __init__(self, factory: Callable[[], Awaitable[T]], size: int):
        self._factory = factory
        self._size = size
        self._q: asyncio.Queue[T] = asyncio.Queue(maxsize=size)
        self._ready = asyncio.Event()

    async def prepare(self) -> None:
        if self._ready.is_set():
            return
        for _ in range(self._size):
            item = await self._factory()
            await self._q.put(item)
        self._ready.set()

    async def acquire(self, timeout: float | None = None) -> T:
        if timeout is None:
            return await self._q.get()
        try:
            return await asyncio.wait_for(self._q.get(), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise TimeoutError("Pool acquire timeout") from e

    async def release(self, item: T) -> None:
        await self._q.put(item)

    @asynccontextmanager
    async def lease(self, timeout: float | None = None):
        item = await self.acquire(timeout=timeout)
        try:
            yield item
        finally:
            await self.release(item)
