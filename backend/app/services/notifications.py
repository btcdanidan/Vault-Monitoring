"""Notification service: Telegram (admin alerts) and email (approval/rejection)."""

import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


async def notify_telegram_new_signup(email: str) -> None:
    """
    Send a Telegram message to admin chat: new signup request.
    Fire-and-forget; logs and ignores errors so auth flow is not blocked.
    """
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
        logger.warning("Telegram not configured; skipping new signup notification")
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    # Use https if domain looks like production
    scheme = "https" if not settings.domain.startswith("localhost") else "http"
    approve_url = f"{scheme}://{settings.domain}/admin/accounts"
    text = f"New signup request: {email}. Approve at {approve_url}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json={"chat_id": settings.telegram_admin_chat_id, "text": text},
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Telegram send failed",
                    status=resp.status_code,
                    body=resp.text,
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("Telegram notification error", error=str(e))


def _send_email_sync(
    to_email: str,
    subject: str,
    body_text: str,
) -> None:
    """Sync helper to send one email via SMTP. Used from asyncio.to_thread."""
    settings = get_settings()
    if not settings.smtp_host:
        logger.warning("SMTP not configured; skipping email", to=to_email)
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_email or "noreply@localhost"
    msg["To"] = to_email
    msg.attach(MIMEText(body_text, "plain"))
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            if settings.smtp_user and settings.smtp_password:
                smtp.starttls()
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.sendmail(msg["From"], [to_email], msg.as_string())
    except Exception as e:  # noqa: BLE001
        logger.warning("Email send failed", to=to_email, error=str(e))


async def send_approval_email(to_email: str) -> None:
    """Send approval email in background. Does not block."""
    settings = get_settings()
    scheme = "https" if not settings.domain.startswith("localhost") else "http"
    signin_url = f"{scheme}://{settings.domain}/login"
    subject = "Your account has been approved"
    body = f"Your account has been approved. You can now sign in at {signin_url}"
    asyncio.create_task(
        asyncio.to_thread(_send_email_sync, to_email, subject, body)
    )


async def send_rejection_email(to_email: str) -> None:
    """Send rejection email in background. Does not block."""
    subject = "Account request declined"
    body = "Your account request has been declined."
    asyncio.create_task(
        asyncio.to_thread(_send_email_sync, to_email, subject, body)
    )
