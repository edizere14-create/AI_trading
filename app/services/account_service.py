from __future__ import annotations

from typing import Any, Dict


async def get_account(user_id: int, db: Any) -> Dict[str, Any]:
    """Get user account (placeholder)."""
    return {
        "user_id": user_id,
        "base_currency": "USD",
        "max_risk_per_trade": 0.02,
    }