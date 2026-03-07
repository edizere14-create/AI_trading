import os

# Override DB URL before any app imports touch SQLAlchemy
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("KRAKEN_API_KEY", "test_key")
os.environ.setdefault("KRAKEN_API_SECRET", "test_secret")
os.environ.setdefault("TRADING_PAPER_MODE", "true")
os.environ.setdefault("KRAKEN_SANDBOX", "true")
