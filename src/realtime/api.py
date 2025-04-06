"""
Realtime WebSocket API communication handler.
Handles connection to Azure OpenAI or OpenAI Realtime Endpoints.
"""

import os
import json
import asyncio
import logging
import websockets
from datetime import datetime
from typing import Optional, Dict, Any
from dotenv import load_dotenv
load_dotenv()

from src.realtime.event_handler import RealtimeEventHandler

logger = logging.getLogger(__name__)


class RealtimeAPI(RealtimeEventHandler):
    """
    WebSocket client for connecting and interacting with the Realtime API.
    """

    def __init__(self) -> None:
        super().__init__()
        self.default_url: str = "wss://api.openai.com/v1/realtime"
        self.url: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        self.api_key: str = os.getenv("AZURE_OPENAI_API_KEY", "")
        self.api_version: str = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_VERSION", "")
        self.azure_deployment: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
        self.ws: Optional[websockets.WebSocketClientProtocol] = None

    def is_connected(self) -> bool:
        """
        Check if the WebSocket connection is active.

        Returns:
            bool: True if connected, False otherwise.
        """
        return self.ws is not None

    async def connect(self) -> None:
        """
        Establish a WebSocket connection to the Realtime API endpoint.
        """
        if self.is_connected():
            raise Exception("Already connected.")

        if not all([self.url, self.api_key, self.azure_deployment]):
            raise ValueError("RealtimeAPI missing configuration: endpoint, API key, or deployment")

        connection_url = (
            f"{self.url}/openai/realtime"
            f"?api-version={self.api_version}"
            f"&deployment={self.azure_deployment}"
            f"&api-key={self.api_key}"
        )
        print(f"Connecting to {connection_url}")
        logger.info(f"Connecting to Realtime API at {connection_url}")

        try:
            self.ws = await websockets.connect(connection_url)
            logger.info(f"Connected to {self.url}")
            asyncio.create_task(self._receive_messages())
        except Exception as e:
            logger.error(f"Failed to connect to Realtime API: {e}")
            raise

    async def disconnect(self) -> None:
        """
        Gracefully close the WebSocket connection.
        """
        if self.ws:
            try:
                await self.ws.close()
                logger.info(f"Disconnected from Realtime API at {self.url}")
            except Exception as e:
                logger.error(f"Error during WebSocket disconnect: {e}", exc_info=True)
            finally:
                self.ws = None

    async def send(self, event_name: str, data: Optional[Dict[str, Any]] = None) -> None:
        """
        Send an event to the Realtime API through WebSocket.

        Args:
            event_name (str): Type/name of the event.
            data (Optional[Dict[str, Any]]): Payload dictionary.

        Raises:
            RuntimeError: If WebSocket is not connected.
            TypeError: If data is not a dictionary.
        """
        if not self.is_connected():
            raise RuntimeError("Cannot send event. Not connected to Realtime API.")

        data = data or {}
        if not isinstance(data, dict):
            logger.error("Provided data is not a dictionary.")
            raise TypeError("Data must be a dictionary.")

        event = {
            "event_id": self._generate_id(prefix="evt_"),
            "type": event_name,
            **data,
        }

        try:
            await self.ws.send(json.dumps(event))
            logger.debug(f"Sent event: {event}")
            self.dispatch(f"client.{event_name}", event)
            self.dispatch("client.*", event)
        except Exception as e:
            logger.error(f"Failed to send event '{event_name}': {e}", exc_info=True)
            raise

    async def _receive_messages(self) -> None:
        """
        Continuously listen for incoming WebSocket messages and dispatch events.
        """
        if not self.ws:
            return

        try:
            async for message in self.ws:
                try:
                    event = json.loads(message)
                    logger.debug(f"Received event: {event}")

                    if event.get("type") == "error":
                        logger.error(f"Realtime API Error: {event}")

                    self.dispatch(f"server.{event['type']}", event)
                    self.dispatch("server.*", event)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to decode incoming message: {e}", exc_info=True)
        except websockets.ConnectionClosed as e:
            logger.warning(f"WebSocket connection closed: {e}")
        except Exception as e:
            logger.error(f"Error in WebSocket receive loop: {e}", exc_info=True)
        finally:
            await self.disconnect()

    def _generate_id(self, prefix: str) -> str:
        """
        Generate a unique event ID based on the current UTC timestamp.

        Args:
            prefix (str): Prefix to prepend to the ID.

        Returns:
            str: Unique event ID.
        """
        return f"{prefix}{int(datetime.utcnow().timestamp() * 1000)}"
