from pydantic_settings import BaseSettings
from typing import Optional
from pydantic import Field, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

class Settings(BaseSettings):
    # API Configuration
    API_TITLE: str = "AI Trading API"
    API_VERSION: str = "0.1.0"
    DEBUG: bool = False
    
    # Database Configuration
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@postgres:5432/ai_trading"
    
    # JWT Configuration
    JWT_SECRET: str = "your-secret-key-change-this"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24
    JWT_EXPIRATION_MINUTES: int = 30
    
    # AI Model Configuration
    MODEL_TYPE: str = "random_forest"
    RANDOM_FOREST_N_ESTIMATORS: int = 100
    
    # Trading Configuration
    TRADING_ENABLED: bool = False
    MAX_POSITION_SIZE: float = 1000.0
    STOP_LOSS_PERCENT: float = 2.0
    TRADING_PAPER_MODE: bool = False
    EXECUTION_EXCHANGE_ID: str = "krakenfutures"
    MARKET_DATA_EXCHANGE_ID: str = "krakenfutures"
    KRAKEN_FUTURES_DEMO: bool = True
    MOMENTUM_DEFAULT_SYMBOL: str = "PI_XBTUSD"
    MOMENTUM_AUTO_START: bool = True
    MOMENTUM_BUY_THRESHOLD: float = 0.01
    MOMENTUM_SELL_THRESHOLD: float = -0.01
    TRADING_MODE: str = "kraken"
    TRADING_API_KEY: str = ""
    TRADING_API_SECRET: str = ""
    
    # Azure Configuration (optional)
    AZURE_TENANT_ID: str = ""
    AZURE_CLIENT_ID: str = ""
    AZURE_CLIENT_SECRET: str = ""
    AZURE_SUBSCRIPTION_ID: str = ""
    AZURE_STORAGE_CONNECTION_STRING: str = ""
    AZURE_STORAGE_CONTAINER_NAME: str = ""
    AZURE_KEY_VAULT_URL: str = ""
    
    # Broker Configuration
    KRAKEN_API_KEY: str = ""
    KRAKEN_API_SECRET: str = ""
    
    # Redis Configuration
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # Environment
    ENVIRONMENT: str = "production"
    LOG_LEVEL: str = "INFO"
    ALLOWED_ORIGINS: str = "*"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="allow",  # Allow extra fields from .env
    )

    @field_validator("DEBUG", mode="before")
    @classmethod
    def _parse_debug(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on", "debug", "dev", "development"}:
            return True
        if text in {"0", "false", "no", "off", "release", "prod", "production"}:
            return False
        return False

class RiskProfile(BaseSettings):
    max_risk_per_trade: float = Field(0.02, ge=0.001, le=0.5)
    max_daily_drawdown: float = Field(0.05, ge=0.01, le=1.0)

    @model_validator(mode="after")
    def drawdown_greater_than_risk(self):
        if self.max_daily_drawdown < self.max_risk_per_trade:
            raise ValueError("Daily drawdown must be > risk per trade")
        return self

settings = Settings()
