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

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Trading API"
    DATABASE_URL: str = "sqlite+aiosqlite:///./ai_trading.db"
    REDIS_URL: str = "redis://localhost:6379"
    JWT_SECRET: str = "super-secret-jwt-token-change-in-prod"
    COINGECKO_API_KEY: str | None = None
    
    class Config:
        env_file = ".env"

settings = Settings()
