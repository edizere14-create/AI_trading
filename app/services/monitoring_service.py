from typing import Final

DEFAULT_METRICS: Final = {
    "uptime": "99.9%",
    "requests_per_min": 42,
    "ai_latency_ms": 120,
}

async def get_system_metrics() -> dict:
    """Return health metrics (placeholder)."""
    return dict(DEFAULT_METRICS)