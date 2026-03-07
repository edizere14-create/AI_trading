"""
Alert manager — sends critical trading events to configured channels.
Channels: Telegram, email, dashboard state.
All channels opt-in via env vars.
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    level: str        # "info" | "warning" | "critical"
    title: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AlertManager:
    """Singleton alert manager. Call AlertManager.instance() anywhere."""

    _instance: "AlertManager | None" = None

    def __init__(self) -> None:
        self._history: deque[Alert] = deque(maxlen=200)

        # Telegram
        self._tg_token  = os.getenv("ALERT_TELEGRAM_BOT_TOKEN", "").strip()
        self._tg_chat   = os.getenv("ALERT_TELEGRAM_CHAT_ID",   "").strip()
        self._tg_levels = set(
            os.getenv("ALERT_TELEGRAM_LEVELS", "warning,critical").lower().split(",")
        )

        # Email
        self._email_host   = os.getenv("ALERT_EMAIL_HOST",     "").strip()
        self._email_port   = int(os.getenv("ALERT_EMAIL_PORT", "587"))
        self._email_user   = os.getenv("ALERT_EMAIL_USER",     "").strip()
        self._email_pass   = os.getenv("ALERT_EMAIL_PASS",     "").strip()
        self._email_to     = os.getenv("ALERT_EMAIL_TO",       "").strip()
        self._email_levels = set(
            os.getenv("ALERT_EMAIL_LEVELS", "critical").lower().split(",")
        )

        # Rate limiting — max 1 alert per title per N seconds
        self._last_sent: dict[str, float] = {}
        self._rate_limit_sec = int(os.getenv("ALERT_RATE_LIMIT_SEC", "300"))

    @classmethod
    def instance(cls) -> "AlertManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _is_rate_limited(self, title: str) -> bool:
        now = time.monotonic()
        last = self._last_sent.get(title, 0.0)
        if now - last < self._rate_limit_sec:
            return True
        self._last_sent[title] = now
        return False

    def send(
        self,
        level: str,
        title: str,
        message: str,
        data: dict | None = None,
    ) -> None:
        alert = Alert(level=level, title=title, message=message, data=data or {})
        self._history.append(alert)
        logger.log(
            logging.CRITICAL if level == "critical" else
            logging.WARNING  if level == "warning"  else logging.INFO,
            "[ALERT][%s] %s — %s", level.upper(), title, message,
        )

        if self._is_rate_limited(title):
            return

        if self._tg_token and self._tg_chat and level in self._tg_levels:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self._send_telegram(alert))
                else:
                    loop.run_until_complete(self._send_telegram(alert))
            except Exception:
                pass

        if self._email_host and self._email_to and level in self._email_levels:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self._send_email(alert))
                else:
                    loop.run_until_complete(self._send_email(alert))
            except Exception:
                pass

    async def _send_telegram(self, alert: Alert) -> None:
        emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(alert.level, "📢")
        text = (
            f"{emoji} *{alert.title}*\n"
            f"{alert.message}\n"
            f"`{alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}`"
        )
        if alert.data:
            for k, v in list(alert.data.items())[:5]:
                text += f"\n• {k}: `{v}`"

        url = f"https://api.telegram.org/bot{self._tg_token}/sendMessage"
        try:
            await asyncio.to_thread(
                requests.post,
                url,
                json={"chat_id": self._tg_chat, "text": text, "parse_mode": "Markdown"},
                timeout=5,
            )
        except Exception as exc:
            logger.warning("[ALERT] Telegram send failed: %s", exc)

    async def _send_email(self, alert: Alert) -> None:
        subject = f"[{alert.level.upper()}] AI Trader — {alert.title}"
        body    = f"{alert.message}\n\nTimestamp: {alert.timestamp.isoformat()}"
        if alert.data:
            body += "\n\nDetails:\n" + "\n".join(f"  {k}: {v}" for k, v in alert.data.items())

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = self._email_user
        msg["To"]      = self._email_to

        try:
            def _send() -> None:
                with smtplib.SMTP(self._email_host, self._email_port) as s:
                    s.starttls()
                    s.login(self._email_user, self._email_pass)
                    s.send_message(msg)

            await asyncio.to_thread(_send)
        except Exception as exc:
            logger.warning("[ALERT] Email send failed: %s", exc)

    def get_recent(self, n: int = 20) -> list[dict]:
        return [
            {
                "level":     a.level,
                "title":     a.title,
                "message":   a.message,
                "timestamp": a.timestamp.isoformat(),
                "data":      a.data,
            }
            for a in list(self._history)[-n:]
        ]
