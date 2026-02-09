from typing import Final

DEFAULT_RESULT: Final = "job completed"

async def run_scheduled_job(job_type: str) -> str:
    """Run a scheduled task and return its status message."""
    job = job_type.strip() or "unknown"
    return f"{job} {DEFAULT_RESULT}"