from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Union

import os
import yaml
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]

@lru_cache(maxsize=None)
def load_config(path: Union[str, Path]) -> Dict[str, Any]:
    """Load a YAML config file (relative to project root)."""
    config_path = (BASE_DIR / path).resolve()
    if not config_path.exists():
        return {}
    with config_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

# Example convenience helpers (optional)
def load_strategies() -> Dict[str, Any]:
    return load_config("configs/trading/strategies.yaml")

def load_broker(broker_name: str) -> Dict[str, Any]:
    return load_config(f"configs/brokers/{broker_name}.yaml")

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore"
    )
    
    # Required fields (loaded from .env)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./ai_trading.db")
    JWT_SECRET: str = ""
    KRAKEN_API_KEY: str = ""
    KRAKEN_API_SECRET: str = ""
    KRAKEN_BASE_URL: str = "https://demo-futures.kraken.com/derivatives/api/v3/"
    KRAKEN_WS_URL: str = "wss://demo-futures.kraken.com/ws/v1"
    
    # Optional fields with defaults
    PROJECT_NAME: str = "AI Trading API"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 30
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    ALLOWED_ORIGINS: str = "http://localhost:3000"
    TRADING_MODE: str = "kraken"
    REDIS_URL: str = "redis://localhost:6379/0"
    API_BASE_URL: str = "https://ai-trading-ujr3.onrender.com"
    WS_URL: str = "wss://ai-trading-ujr3.onrender.com/ws/price"
    HTTP_TIMEOUT_SEC: int = 3
    USE_MOCK_IF_OFFLINE: bool = True

    # Risk profiles
    RISK_PROFILES: Dict[str, Any] = {
        "low": {
            "max_position_size": 0.02,
            "max_daily_loss": 0.01,
            "max_leverage": 1.0
        },
        "medium": {
            "max_position_size": 0.05,
            "max_daily_loss": 0.02,
            "max_leverage": 2.0
        },
        "high": {
            "max_position_size": 0.10,
            "max_daily_loss": 0.05,
            "max_leverage": 5.0
        }
    }

    @field_validator("DEBUG", mode="before")
    @classmethod
    def _parse_debug(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on", "debug", "dev", "development"}:
            return True
        if text in {"0", "false", "no", "off", "release", "prod", "production"}:
            return False
        return False

    @property
    def api_base_url(self) -> str:
        return self.API_BASE_URL

    @property
    def ws_url(self) -> str:
        return self.WS_URL

    @property
    def timeout_sec(self) -> int:
        return self.HTTP_TIMEOUT_SEC

    @property
    def use_mock_if_offline(self) -> bool:
        return self.USE_MOCK_IF_OFFLINE


settings = Settings()
