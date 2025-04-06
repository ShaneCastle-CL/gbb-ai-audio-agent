"""
Event handling system for realtime communication.
Implements a basic pub/sub mechanism with async support.
"""

import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Any, Callable, Awaitable, Dict, List, Union

logger = logging.getLogger(__name__)


class RealtimeEventHandler:
    """
    Base class to manage event listeners and dispatch events
    in a realtime asynchronous environment.
    """

    def __init__(self) -> None:
        self.event_handlers: Dict[str, List[Callable[[Any], Any]]] = defaultdict(list)

    def on(self, event_name: str, handler: Union[Callable[[Any], Any], Callable[[Any], Awaitable[Any]]]) -> None:
        """
        Register a handler function for a specific event.

        Args:
            event_name (str): Name of the event to listen for.
            handler (Callable): Function or coroutine to be called when the event fires.
        """
        if not callable(handler):
            logger.error(f"Tried to register non-callable handler for event '{event_name}'.")
            raise TypeError("Handler must be callable.")
        self.event_handlers[event_name].append(handler)
        logger.debug(f"Handler registered for event '{event_name}'.")

    def dispatch(self, event_name: str, event: Any) -> None:
        """
        Trigger all handlers associated with a specific event.

        Args:
            event_name (str): Name of the event to dispatch.
            event (Any): Data associated with the event.
        """
        if event_name not in self.event_handlers:
            logger.debug(f"No handlers registered for event '{event_name}'.")
            return

        for handler in self.event_handlers[event_name]:
            try:
                if inspect.iscoroutinefunction(handler):
                    asyncio.create_task(handler(event))
                else:
                    handler(event)
                logger.debug(f"Dispatched event '{event_name}' to handler '{handler.__name__}'.")
            except Exception as e:
                logger.error(f"Error dispatching event '{event_name}' to handler: {e}", exc_info=True)

    def clear_event_handlers(self) -> None:
        """
        Remove all registered event handlers.
        """
        self.event_handlers.clear()
        logger.info("All event handlers cleared.")

    async def wait_for_next(self, event_name: str) -> Any:
        """
        Wait for the next occurrence of a specific event asynchronously.

        Args:
            event_name (str): Event to wait for.

        Returns:
            Any: Data of the received event.
        """
        future = asyncio.Future()

        def _handler(event: Any):
            if not future.done():
                future.set_result(event)

        self.on(event_name, _handler)
        logger.debug(f"Waiting for next event '{event_name}'.")
        return await future
