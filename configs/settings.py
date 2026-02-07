from pydantic_settings import BaseSettings
from typing import Optional
from pydantic import Field, validator

class Settings(BaseSettings):
    # API Configuration
    API_TITLE: str = "AI Trading API"
    API_VERSION: str = "0.1.0"
    DEBUG: bool = False
    
    # Database Configuration
    DATABASE_URL: str = "sqlite+aiosqlite:///./ai_trading.db"
    
    # JWT Configuration
    JWT_SECRET: str = "your-secret-key-change-this"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24
    
    # AI Model Configuration
    MODEL_TYPE: str = "random_forest"  # or "tensorflow"
    RANDOM_FOREST_N_ESTIMATORS: int = 100
    
    # Trading Configuration
    TRADING_ENABLED: bool = False
    MAX_POSITION_SIZE: float = 1000.0
    STOP_LOSS_PERCENT: float = 2.0
    
    class Config:
        env_file = ".env"
        case_sensitive = True

class RiskProfile(BaseSettings):
    max_risk_per_trade: float = Field(0.02, ge=0.001, le=0.5)
    max_daily_drawdown: float = Field(0.05, ge=0.01, le=1.0)
    
    @validator('max_daily_drawdown')
    def drawdown_greater_than_risk(cls, v: float, values: dict) -> float:
        if 'max_risk_per_trade' in values and v < values['max_risk_per_trade']:
            raise ValueError('Daily drawdown must be > risk per trade')
        return v

settings = Settings()