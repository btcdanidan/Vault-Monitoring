"""Tests for notification service (Telegram, email)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import notifications as notifications_module


@pytest.mark.asyncio
async def test_notify_telegram_new_signup_skips_when_not_configured() -> None:
    """When Telegram token/chat_id are empty, we skip and do not call the API."""
    with (
        patch.object(
            notifications_module,
            "get_settings",
            return_value=type(
                "Settings",
                (),
                {
                    "telegram_bot_token": "",
                    "telegram_admin_chat_id": "",
                    "domain": "localhost",
                },
            )(),
        ),
        patch("app.services.notifications.httpx.AsyncClient") as mock_client_class,
    ):
        await notifications_module.notify_telegram_new_signup("user@example.com")
        mock_client_class.assert_not_called()


@pytest.mark.asyncio
async def test_notify_telegram_new_signup_sends_message_when_configured() -> None:
    """When Telegram is configured, POST is called with expected payload."""
    mock_post = AsyncMock(return_value=type("Response", (), {"status_code": 200})())
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value.post = mock_post
    mock_ctx.__aexit__.return_value = None

    with (
        patch.object(
            notifications_module,
            "get_settings",
            return_value=type(
                "Settings",
                (),
                {
                    "telegram_bot_token": "fake-token",
                    "telegram_admin_chat_id": "123",
                    "domain": "app.example.com",
                },
            )(),
        ),
        patch(
            "app.services.notifications.httpx.AsyncClient",
            return_value=mock_ctx,
        ),
    ):
        await notifications_module.notify_telegram_new_signup("newuser@example.com")

    assert mock_post.await_count == 1
    call_kw = mock_post.await_args[1]
    assert "newuser@example.com" in call_kw["json"]["text"]
    assert call_kw["json"]["chat_id"] == "123"


def test_send_email_sync_skips_when_smtp_not_configured() -> None:
    """When smtp_host is empty, _send_email_sync returns without opening SMTP."""
    with patch.object(
        notifications_module,
        "get_settings",
        return_value=type(
            "Settings",
            (),
            {
                "smtp_host": "",
                "smtp_port": 587,
                "smtp_from_email": "",
            },
        )(),
    ):
        # Should not raise; no SMTP connection
        notifications_module._send_email_sync(
            "user@example.com",
            "Subject",
            "Body",
        )


def test_send_email_sync_uses_smtp_when_configured() -> None:
    """When smtp_host is set, SMTP is used (we mock it)."""
    with (
        patch.object(
            notifications_module,
            "get_settings",
            return_value=type(
                "Settings",
                (),
                {
                    "smtp_host": "smtp.example.com",
                    "smtp_port": 587,
                    "smtp_user": "",
                    "smtp_password": "",
                    "smtp_from_email": "noreply@example.com",
                },
            )(),
        ),
        patch("app.services.notifications.smtplib.SMTP") as mock_smtp,
    ):
        mock_smtp.return_value.__enter__.return_value.sendmail = lambda *a, **k: None
        notifications_module._send_email_sync(
            "user@example.com",
            "Test",
            "Body",
        )
        mock_smtp.assert_called_once_with("smtp.example.com", 587)
