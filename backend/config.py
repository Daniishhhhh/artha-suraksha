"""Configuration and safe local defaults for the Artha-Suraksha backend."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

KeyProfile = Literal["DEV", "TELEMETRY", "DEMO"]


# MappingProxyType prevents a later phase from accidentally mutating the
# protocol headers shared by concurrent Prisma connections.
FAST_TURN_VAD: Final[Mapping[str, str]] = MappingProxyType(
    {
        "x-min-speech-ms": "250",
        "x-min-silence-ms": "350",
        "x-min-segment-ms": "120",
        # OQ-3 is deliberately isolated until the upstream payload schema is
        # documented.  The local mock and future controller can override it.
        "x-speech-confidence": "0.75",
    }
)

DIGIT_CAPTURE_VAD: Final[Mapping[str, str]] = MappingProxyType(
    {
        "x-min-speech-ms": "250",
        "x-min-silence-ms": "800",
        "x-min-segment-ms": "120",
    }
)

DEFAULT_BIAS_LIST: Final[list[str]] = ["EMI", "PMFBY", "KCC", "IRDAI", "किस्त"]
DEFAULT_BIAS_SCORE: Final[float] = 1.5


class Settings(BaseSettings):
    """Environment-backed settings with an explicit three-key firewall."""

    GNANI_API_KEY_DEV: SecretStr = SecretStr("")
    GNANI_API_KEY_TELEMETRY: SecretStr = SecretStr("")
    GNANI_API_KEY_DEMO: SecretStr = SecretStr("")
    ACTIVE_KEY_PROFILE: KeyProfile = "DEV"
    USE_MOCK_GNANI: bool = True

    GNANI_STT_WSS_URL: str = "wss://api.vachana.ai/stt/v3/stream"
    GNANI_TTS_WSS_URL: str = "wss://api.vachana.ai/api/v1/tts"
    GNANI_BATCH_STT_URL: str = "https://api.vachana.ai/stt/v3/batch/jobs"
    EVON_BASE_URL: str = "http://localhost:11434/v1"
    EVON_MODEL_ID: str = "gnani/gnani-evon-v3.3-30B-A3B"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def get_active_api_key(self) -> str:
        """Return only the key selected by ``ACTIVE_KEY_PROFILE``.

        DEMO credentials are intentionally unavailable to automated tests so
        a unit-test run cannot spend the programme's live credit budget.
        ``SecretStr`` is unwrapped only at this narrow boundary.
        """

        if self.ACTIVE_KEY_PROFILE == "DEMO" and (
            "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules
        ):
            raise RuntimeError(
                "GNANI_API_KEY_DEMO is disabled while automated tests are running"
            )

        key_by_profile = {
            "DEV": self.GNANI_API_KEY_DEV,
            "TELEMETRY": self.GNANI_API_KEY_TELEMETRY,
            "DEMO": self.GNANI_API_KEY_DEMO,
        }
        return key_by_profile[self.ACTIVE_KEY_PROFILE].get_secret_value()


settings = Settings()
