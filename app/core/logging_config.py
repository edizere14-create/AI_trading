from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
	def format(self, record: logging.LogRecord) -> str:
		payload: dict[str, Any] = {
			"timestamp": datetime.now(timezone.utc).isoformat(),
			"level": record.levelname,
			"logger": record.name,
			"message": record.getMessage(),
		}
		if record.exc_info:
			payload["exc_info"] = self.formatException(record.exc_info)
		return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(level: str | None = None) -> None:
	raw_level = level if level is not None else os.getenv("LOG_LEVEL", "INFO")
	selected = str(raw_level).strip().upper() or "INFO"
	resolved = getattr(logging, selected, logging.INFO)
	handler = logging.StreamHandler()
	handler.setFormatter(JsonFormatter())
	logging.basicConfig(level=resolved, handlers=[handler], force=True)

