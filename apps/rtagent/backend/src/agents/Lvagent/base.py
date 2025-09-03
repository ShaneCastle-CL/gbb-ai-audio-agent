# apps/rtagent/backend/src/lva/base.py
from __future__ import annotations

import base64
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

import numpy as np
from azure.identity import DefaultAzureCredential
from utils.ml_logging import get_logger

from .transport import WebSocketTransport
from .audio_io import MicSource, SpeakerSink, pcm_to_base64

logger = get_logger(__name__)

# ── BAKED DEFAULTS (override via env if present) ──────────────────────────────
DEFAULT_API_VERSION: str = "2025-05-01-preview"
DEFAULT_ENDPOINT: str = "https://<your-resource>.services.ai.azure.com"
DEFAULT_AUTH_MODE: Literal["entra", "api_key"] = "entra"
DEFAULT_API_KEY_ENV: str = "AZURE_VOICE_LIVE_API_KEY"
DEFAULT_TOKEN_SCOPE: str = "https://ai.azure.com/.default"

ENV_ENDPOINT = "AZURE_VOICE_LIVE_ENDPOINT"      # optional override
ENV_AUTH_MODE = "LVA_AUTH_MODE"                 # optional override: "entra" | "api_key"
ENV_API_VERSION = "LVA_API_VERSION"             # optional override
ENV_API_KEY_ENV = "LVA_API_KEY_ENV"             # optional override (name of the key var)
ENV_TOKEN_SCOPE = "LVA_TOKEN_SCOPE"             # optional override

DEFAULT_SAMPLE_RATE_HZ = 24_000
DEFAULT_CHUNK_MS = 20


@dataclass(frozen=True)
class LvaModel:
    """
    Minimal model config.

    :param deployment_id: Voice Live–compatible model deployment (e.g., 'gpt-4o-realtime').
    """
    deployment_id: str


@dataclass(frozen=True)
class LvaAgentBinding:
    """
    Agent Service binding.

    :param agent_id: Azure AI Agent ID to bind the session to.
    :param project_name: Project name (use this OR connection_string).
    :param connection_string: Hub connection string (use this OR project_name).
    """
    agent_id: str
    project_name: Optional[str] = None
    connection_string: Optional[str] = None


@dataclass(frozen=True)
class LvaSessionCfg:
    """
    Voice/VAD/noise/echo configuration applied via session.update.

    :param voice_name: TTS voice name.
    :param voice_type: Voice type (e.g., 'azure-standard').
    :param voice_temperature: Voice randomness.
    :param vad_type: Turn detection type (e.g., 'azure_semantic_vad').
    :param vad_threshold: VAD sensitivity.
    :param vad_prefix_ms: VAD prefix padding (ms).
    :param vad_silence_ms: VAD silence duration (ms).
    :param vad_eou_model: End-of-utterance model id.
    :param vad_eou_threshold: EOU threshold.
    :param vad_eou_timeout: EOU timeout (s).
    :param noise_reduction_type: Input noise reduction type.
    :param echo_cancellation_type: Echo cancellation type.
    """
    voice_name: str
    voice_type: str
    voice_temperature: float
    vad_type: str
    vad_threshold: float
    vad_prefix_ms: int
    vad_silence_ms: int
    vad_eou_model: str
    vad_eou_threshold: float
    vad_eou_timeout: int
    noise_reduction_type: str
    echo_cancellation_type: str


class LiveVoiceAgent:
    """
    Live Voice Agent bound to Azure AI Agent Service + Azure Voice Live API.

    Endpoint, API version, and auth mode are baked into this module
    (with optional environment overrides). Session behavior (prompts, tools)
    comes from the bound Agent — no instructions are sent here.

    :param model: Voice Live deployment id.
    :param binding: Agent Service binding (agent_id + project_name OR connection_string).
    :param session: Voice/VAD/noise/echo configuration.
    :param reconnect_backoff_s: Optional reconnect backoff sequence.
    """

    def __init__(
        self,
        *,
        model: LvaModel,
        binding: LvaAgentBinding,
        session: LvaSessionCfg,
        reconnect_backoff_s: Optional[List[int]] = None,
    ) -> None:
        self._model = model
        self._binding = binding
        self._session = session
        self._backoff = reconnect_backoff_s or [1, 2, 4, 8]

        # Resolve baked + env overrides
        self._api_version = os.getenv(ENV_API_VERSION, DEFAULT_API_VERSION)
        endpoint = os.getenv(ENV_ENDPOINT, DEFAULT_ENDPOINT)
        self._auth_mode: Literal["entra", "api_key"] = os.getenv(ENV_AUTH_MODE, DEFAULT_AUTH_MODE)  # type: ignore[assignment]
        self._api_key_env = os.getenv(ENV_API_KEY_ENV, DEFAULT_API_KEY_ENV)
        self._token_scope = os.getenv(ENV_TOKEN_SCOPE, DEFAULT_TOKEN_SCOPE)

        if "<your-resource>" in endpoint:
            logger.warning(
                "AZURE_VOICE_LIVE_ENDPOINT not set; using DEFAULT_ENDPOINT placeholder. "
                "Set AZURE_VOICE_LIVE_ENDPOINT in your environment."
            )

        azure_ws = endpoint.rstrip("/").replace("https://", "wss://")

        # Acquire agent access token (required for Agent binding).
        agent_access_token = self._get_agent_access_token()

        # Build agent-bound WS URL
        if self._binding.project_name:
            q = (
                f"api-version={self._api_version}"
                f"&agent-project-name={self._binding.project_name}"
                f"&agent-id={self._binding.agent_id}"
                f"&agent-access-token={agent_access_token}"
            )
        else:
            q = (
                f"api-version={self._api_version}"
                f"&agent-connection-string={self._binding.connection_string}"
                f"&agent-id={self._binding.agent_id}"
                f"&agent-access-token={agent_access_token}"
            )
        self._url = f"{azure_ws}/voice-live/realtime?{q}"

        # Voice Live auth header (api-key only if selected)
        headers: Dict[str, str] = {"x-ms-client-request-id": str(uuid.uuid4())}
        if self._auth_mode == "api_key":
            api_key = os.getenv(self._api_key_env, "")
            if not api_key:
                raise ValueError(f"{self._api_key_env} is not set.")
            headers["api-key"] = api_key

        self._ws = WebSocketTransport(self._url, headers)

        # Local dev audio adapters (24 kHz / 20 ms)
        self._src = MicSource(sample_rate=DEFAULT_SAMPLE_RATE_HZ)
        self._sink = SpeakerSink(sample_rate=DEFAULT_SAMPLE_RATE_HZ)
        self._frames = int(DEFAULT_SAMPLE_RATE_HZ * (DEFAULT_CHUNK_MS / 1000))

    def _get_agent_access_token(self) -> str:
        """
        Acquire Agent Service access token via Entra (DefaultAzureCredential).

        :return: Access token string.
        :raises RuntimeError: If token acquisition fails.
        """
        try:
            cred = DefaultAzureCredential()
            token = cred.get_token(self._token_scope)
            return token.token
        except Exception as exc:
            logger.exception("Agent access token acquisition failed.")
            raise RuntimeError("Agent access token acquisition failed.") from exc

    def _session_update(self) -> Dict[str, Any]:
        """
        Build session.update without instructions (Agent supplies behavior).

        :return: Voice Live session.update payload.
        """
        return {
            "type": "session.update",
            "session": {
                "turn_detection": {
                    "type": self._session.vad_type,
                    "threshold": self._session.vad_threshold,
                    "prefix_padding_ms": self._session.vad_prefix_ms,
                    "silence_duration_ms": self._session.vad_silence_ms,
                    "end_of_utterance_detection": {
                        "model": self._session.vad_eou_model,
                        "threshold": self._session.vad_eou_threshold,
                        "timeout": self._session.vad_eou_timeout,
                    },
                },
                "input_audio_noise_reduction": {"type": self._session.noise_reduction_type},
                "input_audio_echo_cancellation": {"type": self._session.echo_cancellation_type},
                "voice": {
                    "name": self._session.voice_name,
                    "type": self._session.voice_type,
                    "temperature": self._session.voice_temperature,
                },
            },
            "event_id": "",
        }

    def _handle_event(self, raw: str) -> None:
        """
        Handle Voice Live events. Audio deltas are decoded and sent to the sink.

        :param raw: Raw JSON event string from WebSocket.
        """
        try:
            evt = json.loads(raw)
        except Exception:
            logger.exception("Event parse failed.")
            return

        et = evt.get("type")
        if et == "response.audio.delta":
            try:
                delta_b64 = evt.get("delta", "")
                pcm = np.frombuffer(base64.b64decode(delta_b64), dtype=np.int16)
                self._sink.write(pcm)
            except Exception:
                logger.exception("Audio delta handling failed.")
        elif et == "input_audio_buffer.speech_started":
            logger.debug("Barge-in detected.")
            # Optionally: self._sink.stop() if you buffer TTS
        elif et == "error":
            err = evt.get("error", {})
            logger.error("Voice Live error %s: %s", err.get("code"), err.get("message"))
        else:
            logger.info("Event: %s", et)

    def run(self) -> None:
        """
        Connect to Voice Live WS, bind to Agent, send session.update,
        and stream mic→model and model→speaker full-duplex.
        """
        for delay in [0, 1, 2, 4, 8]:
            try:
                if delay:
                    time.sleep(delay)
                self._ws.connect()
                break
            except Exception:
                logger.exception("WS connect failed (backing off %ss).", delay)
        else:
            raise ConnectionError("Unable to connect to Voice Live WS.")

        self._ws.send_dict(self._session_update())

        self._src.start()
        self._sink.start()

        try:
            while True:
                pcm = self._src.read(self._frames)
                if pcm is not None and len(pcm) > 0:
                    self._ws.send_dict(
                        {"type": "input_audio_buffer.append", "audio": pcm_to_base64(pcm), "event_id": ""}
                    )
                raw = self._ws.recv(timeout_s=0.01)
                if raw:
                    self._handle_event(raw)
        except KeyboardInterrupt:
            logger.info("Interrupted by user.")
        except Exception:
            logger.exception("Run loop failed.")
        finally:
            try:
                self._src.stop()
            except Exception:
                logger.exception("Source stop failed.")
            try:
                self._sink.stop()
            except Exception:
                logger.exception("Sink stop failed.")
            self._ws.close()
