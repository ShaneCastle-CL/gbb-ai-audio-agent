import os
import json
import asyncio
import websockets
import logging
from datetime import datetime

from src.realtime_client.event_handler import RealtimeEventHandler

logger = logging.getLogger(__name__)

class RealtimeAPI(RealtimeEventHandler):
    """
    Handles WebSocket connection to the Azure OpenAI Realtime API.
    """

    def __init__(self) -> None:
        super().__init__()
        self.default_url = 'wss://api.openai.com/v1/realtime'
        self.url = os.getenv("AZURE_OPENAI_ENDPOINT", self.default_url)
        self.api_key = os.getenv("AZURE_OPENAI_API_KEY")
        self.api_version = "2024-10-01-preview"
        self.azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        self.ws = None

    def is_connected(self) -> bool:
        """
        Check if WebSocket connection is active.

        Returns:
            bool: True if connected, False otherwise.
        """
        return self.ws is not None

    def log(self, *args) -> None:
        """
        Log a debug message with a timestamp.
        """
        logger.debug(f"[Websocket/{datetime.utcnow().isoformat()}]", *args)

    async def connect(self) -> None:
        """
        Connect to the Azure OpenAI Realtime WebSocket endpoint.
        """
        if self.is_connected():
            raise Exception("Already connected")

        connection_url = (
            f"{self.url}/openai/realtime"
            f"?api-version={self.api_version}"
            f"&deployment={self.azure_deployment}"
            f"&api-key={self.api_key}"
        )

        logger.info(f"Connecting to Realtime API at {connection_url}")
        try:
            self.ws = await websockets.connect(connection_url)
            self.log(f"Connected to {self.url}")
            asyncio.create_task(self._receive_messages())
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket: {e}")
            raise

    async def _receive_messages(self) -> None:
        """
        Listen for messages from the WebSocket and dispatch them.
        """
        try:
            async for message in self.ws:
                try:
                    event = json.loads(message)
                    self.log("Received:", event)

                    if event.get('type') == "error":
                        logger.error(f"Realtime API error event: {event}")

                    self.dispatch(f"server.{event['type']}", event)
                    self.dispatch("server.*", event)
                except Exception as e:
                    logger.error(f"Error handling received message: {e}")
        except Exception as e:
            logger.error(f"WebSocket connection error: {e}")

    async def send(self, event_name: str, data: dict = None) -> None:
        """
        Send an event over the WebSocket connection.

        Args:
            event_name (str): The event type.
            data (dict, optional): Additional payload data.
        """
        if not self.is_connected():
            raise Exception("RealtimeAPI is not connected")

        data = data or {}
        if not isinstance(data, dict):
            raise Exception("Data must be a dictionary")

        event = {
            "event_id": self._generate_id("evt_"),
            "type": event_name,
            **data
        }

        self.dispatch(f"client.{event_name}", event)
        self.dispatch("client.*", event)
        self.log("Sent:", event)

        try:
            await self.ws.send(json.dumps(event))
        except Exception as e:
            logger.error(f"Error sending WebSocket message: {e}")
            raise

    def _generate_id(self, prefix: str) -> str:
        """
        Generate a unique ID for events.

        Args:
            prefix (str): Prefix string for the ID.

        Returns:
            str: Generated unique ID.
        """
        return f"{prefix}{int(datetime.utcnow().timestamp() * 1000)}"

    async def disconnect(self) -> None:
        """
        Disconnect from the WebSocket server.
        """
        if self.ws:
            try:
                await self.ws.close()
                self.ws = None
                self.log(f"Disconnected from {self.url}")
            except Exception as e:
                logger.error(f"Error during WebSocket disconnect: {e}")
                raise
