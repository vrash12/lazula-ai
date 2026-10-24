"""Application settings, loaded from environment variables (and `.env` locally).

The app refuses to start when a required setting is missing or unsafe for the
environment, so misconfiguration fails loudly at deploy time instead of at
the first request.
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    lazula_env: Environment = "development"
    log_level: str = "INFO"

    database_url: str
    redis_url: str

    master_key: SecretStr | None = None
    master_key_file: Path | None = None
    master_key_id: str = Field(default="k1", pattern=r"^[a-z0-9]{1,8}$")

    signature_max_skew_seconds: int = Field(default=300, ge=30, le=900)
    result_cache_ttl_seconds: int = Field(default=86_400, ge=60)

    # Development, test and staging smoke tests only: appends fake models to every
    # chain so the hub works without real provider keys. Forbidden in production.
    enable_fake_provider: bool = False

    @model_validator(mode="after")
    def _check(self) -> Settings:
        if (self.master_key is None) == (self.master_key_file is None):
            raise ValueError("Set exactly one of MASTER_KEY or MASTER_KEY_FILE")
        if self.lazula_env in ("staging", "production") and self.master_key is not None:
            raise ValueError("Use MASTER_KEY_FILE (a mounted secret) outside development and test")
        if self.lazula_env == "production" and self.enable_fake_provider:
            raise ValueError("ENABLE_FAKE_PROVIDER is not allowed in production")
        return self

    def master_key_bytes(self) -> bytes:
        """Return the 32-byte master key, from the environment or the mounted secret file."""
        if self.master_key is not None:
            encoded = self.master_key.get_secret_value()
        elif self.master_key_file is not None:
            encoded = self.master_key_file.read_text(encoding="utf-8").strip()
        else:  # prevented by the validator above
            raise ValueError("No master key configured")
        try:
            key = base64.b64decode(encoded, validate=True)
        except binascii.Error as exc:
            raise ValueError("Master key is not valid base64") from exc
        if len(key) != 32:
            raise ValueError("Master key must decode to exactly 32 bytes")
        return key


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
