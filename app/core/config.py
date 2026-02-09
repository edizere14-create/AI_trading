from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Union

import yaml

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

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore"
    )
    
    # Required fields (loaded from .env)
    DATABASE_URL: str = ""
    JWT_SECRET: str = ""
    KRAKEN_API_KEY: str = ""
    KRAKEN_API_SECRET: str = ""
    
    # Optional fields with defaults
    PROJECT_NAME: str = "AI Trading API"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 30
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    ALLOWED_ORIGINS: str = "http://localhost:3000"
    TRADING_MODE: str = "paper"
    REDIS_URL: str = "redis://localhost:6379/0"

settings = Settings()
