"""Operator notifications: Telegram, e-mail, or the log.

Used for things a human must act on — new review items, budget stops, licence
expiry — and a daily digest. Rate-limited per hour and de-duplicated by key so
a noisy day cannot flood the operator.
"""

from __future__ import annotations

import smtplib
import ssl
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.logging import get_logger, log_event
from app.database.models import Notification

logger = get_logger("nexus.notify")


def _telegram(settings: Settings, subject: str, body: str) -> None:
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required for telegram notifications")
    response = httpx.post(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
        json={"chat_id": settings.telegram_chat_id, "text": f"{subject}\n\n{body}"[:4000], "disable_web_page_preview": True},
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"telegram rejected the message ({response.status_code})")


def _email(settings: Settings, subject: str, body: str) -> None:
    from app.execution.email import build_mime

    if not (settings.operator_email and settings.smtp_host and settings.smtp_username and settings.smtp_password):
        raise RuntimeError("OPERATOR_EMAIL and SMTP settings are required for email notifications")
    message = build_mime(settings, to=settings.operator_email, subject=f"[NEXUS] {subject}", body=body)
    del message["List-Unsubscribe"]
    del message["List-Unsubscribe-Post"]
    if settings.smtp_security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30,
                                               context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
    with server:
        if settings.smtp_security == "starttls":
            server.starttls(context=ssl.create_default_context())
        server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)


SENDERS = {"telegram": _telegram, "email": _email}


def notify(ctx: Any, subject: str, body: str, *, dedupe_key: str | None = None) -> list[str]:
    """Send to every configured channel. Returns the channels that delivered."""
    session = ctx.session
    settings: Settings = ctx.settings
    if dedupe_key and session.scalar(
        select(Notification.id).where(Notification.dedupe_key == dedupe_key, Notification.status == "sent")
    ):
        return []
    since = ctx.now - timedelta(hours=1)
    recent = session.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.created_at >= since, Notification.status == "sent", Notification.channel != "log"
        )
    ) or 0
    delivered: list[str] = []
    for channel in settings.notify_channel_list:
        status, error = "sent", None
        if channel == "log":
            log_event(logger, 20, "operator_notification", subject=subject, body=body[:500])
        elif channel in SENDERS:
            if recent >= settings.notify_max_per_hour:
                status, error = "suppressed", "hourly notification limit reached"
            else:
                try:
                    SENDERS[channel](settings, subject, body)
                except Exception as exc:  # a failed alert must never stop the platform
                    status, error = "failed", str(exc)[:500]
        else:
            status, error = "failed", f"unknown channel {channel!r}"
        session.add(Notification(channel=channel, subject=subject[:300], body=body[:4000], status=status,
                                 error=error, dedupe_key=dedupe_key))
        if status == "sent":
            delivered.append(channel)
    session.flush()
    return delivered


def dashboard_link(settings: Settings, fragment: str = "") -> str:
    base = (settings.public_base_url or "").rstrip("/")
    return f"{base}/{fragment}" if base else "(open the dashboard)"
