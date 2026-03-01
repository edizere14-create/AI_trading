from .settings import settings

# Define constants if needed
STRATEGIES = ["grid_trading", "dca", "arbitrage", "ml_signals"]
RISK_PROFILES = ["conservative", "moderate", "aggressive"]
INSTRUMENTS = ["BTC/USD", "ETH/USD"]
MARKET_HOURS = {"NYSE": "09:30-16:00"}
MODELS = ["random_forest", "lstm"]

__all__ = ["settings", "STRATEGIES", "RISK_PROFILES", "INSTRUMENTS", "MARKET_HOURS", "MODELS"]