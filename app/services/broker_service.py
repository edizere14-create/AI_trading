from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.kraken import KrakenBroker

async def get_broker_for_user(db: AsyncSession, user_id: int) -> KrakenBroker:
    """Return broker instance for user."""
    _ = db
    _ = user_id
    return KrakenBroker(
        api_key="YOUR_API_KEY",
        api_secret="YOUR_API_SECRET",
    )