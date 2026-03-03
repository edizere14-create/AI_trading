from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def log_risk_event(
    db: AsyncSession,
    user_id: int,
    event_type: str,
    metadata: dict[str, Any],
) -> None:
    """
    Lightweight audit logger placeholder.

    This logs structured events and keeps the async signature stable
    for future persistence to an audit table or event bus.
    """
    _ = db
    logger.info(
        "risk_audit user_id=%s event=%s metadata=%s",
        user_id,
        event_type,
        metadata,
    )
