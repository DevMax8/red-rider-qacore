"""Local configuration and safe atomic persistence for the settings screen."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from dotenv import dotenv_values, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path = PROJECT_ROOT / "data"
    env_file: Path = PROJECT_ROOT / ".env"
    team_name: str = "red_rider"
    model_name: str = ""
    model_mode: str = "external_api"
    model_base_url: str = "https://api.openai.com/v1"
    model_api_key: str = field(default="", repr=False)
    model_timeout: float = 90
    max_retries: int = 2
    concurrency: int = 3
    dataset_api_url: str = field(default="", repr=False)
    cors_origins: tuple[str, ...] = ("http://localhost:5500", "http://127.0.0.1:5500", "http://localhost:3000", "http://127.0.0.1:3000")

    def __post_init__(self):
        if self.model_mode not in {"local", "external_api"}:
            raise ValueError("MODEL_MODE must be local or external_api")
        if isinstance(self.model_timeout, bool) or not math.isfinite(self.model_timeout) or not 1 <= self.model_timeout <= 90:
            raise ValueError("MODEL_TIMEOUT must be between 1 and 90 seconds")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 2:
            raise ValueError("MODEL_MAX_RETRIES must be between 0 and 2")
        if type(self.concurrency) is not int or not 1 <= self.concurrency <= 10:
            raise ValueError("EVALUATION_CONCURRENCY must be between 1 and 10")
        if not self.team_name.strip():
            raise ValueError("Team name must not be blank")
        try:
            parts = urlsplit(self.model_base_url)
            parts.port
        except ValueError:
            raise ValueError("Model endpoint must contain a valid host and port") from None
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("Model endpoint must be an HTTP(S) base URL without credentials or query")
        if "\n" in self.model_api_key or "\r" in self.model_api_key:
            raise ValueError("API key must be a single line")
        if self.dataset_api_url:
            try:
                source = urlsplit(self.dataset_api_url)
                source.port
            except ValueError:
                raise ValueError("Dataset endpoint must contain a valid host and port") from None
            if source.scheme != "https" or not source.hostname or not source.hostname.endswith(".api.powerplatform.com") or source.username or source.password or source.fragment:
                raise ValueError("Use the organizer's HTTPS Power Platform dataset URL")

    @classmethod
    def from_env(cls):
        env_file = PROJECT_ROOT / ".env"
        load_dotenv(env_file, override=False, interpolate=False)
        directory = Path(os.getenv("DATA_DIR", "data"))
        if not directory.is_absolute():
            directory = PROJECT_ROOT / directory
        return cls(
            data_dir=directory, env_file=env_file,
            team_name=os.getenv("TEAM_NAME", "red_rider"),
            model_name=os.getenv("MODEL_NAME", "").strip(),
            model_mode=os.getenv("MODEL_MODE", "external_api"),
            model_base_url=os.getenv("MODEL_BASE_URL", "https://api.openai.com/v1"),
            model_api_key=os.getenv("MODEL_API_KEY", ""),
            model_timeout=float(os.getenv("MODEL_TIMEOUT", "90")),
            max_retries=int(os.getenv("MODEL_MAX_RETRIES", "2")),
            concurrency=int(os.getenv("EVALUATION_CONCURRENCY", "3")),
            dataset_api_url=os.getenv("DATASET_API_URL", ""),
            cors_origins=tuple(x.strip() for x in os.getenv("CORS_ORIGINS", ",".join(cls().cors_origins)).split(",") if x.strip()),
        )

    def public(self):
        return {
            "team_name": self.team_name, "model_name": self.model_name,
            "model_mode": self.model_mode, "model_base_url": self.model_base_url,
            "api_key_configured": bool(self.model_api_key),
            "dataset_api_configured": bool(self.dataset_api_url),
            "model_timeout": self.model_timeout, "concurrency": self.concurrency,
        }

    def updated(self, updates: dict):
        return replace(self, **updates)

    def persist(self):
        """Keep unknown user keys while replacing only owned settings, without printing secrets."""
        values = dict(dotenv_values(self.env_file, interpolate=False)) if self.env_file.exists() else {}
        values.update({
            "TEAM_NAME": self.team_name, "MODEL_NAME": self.model_name,
            "MODEL_MODE": self.model_mode, "MODEL_BASE_URL": self.model_base_url,
            "MODEL_API_KEY": self.model_api_key, "MODEL_TIMEOUT": str(self.model_timeout),
            "MODEL_MAX_RETRIES": str(self.max_retries), "EVALUATION_CONCURRENCY": str(self.concurrency),
            "DATASET_API_URL": self.dataset_api_url, "DATA_DIR": str(self.data_dir),
            "CORS_ORIGINS": ",".join(self.cors_origins),
        })
        text = "# Local configuration. Never commit this file.\n" + "".join(
            f"{key}={json.dumps(value or '', ensure_ascii=False)}\n" for key, value in values.items()
        )
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".env-", dir=self.env_file.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(text)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.env_file)
        finally:
            if os.path.exists(name):
                os.unlink(name)
