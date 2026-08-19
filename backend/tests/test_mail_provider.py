"""Unit tests for app.auth.mail -- real Resend integration added
2026-08-08 once the user set RESEND_API_KEY in Render (the provider was a
console-log-only stub before this).

get_mail_provider() is a lazy module-level singleton (same pattern as
app.language.translator.get_translator()), so every test here resets
mail_module._provider to None first -- otherwise whichever provider won
the FIRST call in this pytest process would stick for every later test
regardless of env vars, since the cache is never invalidated.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.auth import mail as mail_module
from app.auth.mail import ConsoleMailProvider, ResendMailProvider, get_mail_provider


@pytest.fixture(autouse=True)
def _reset_singleton():
    mail_module._provider = None
    yield
    mail_module._provider = None


class TestProviderSelection:
    def test_no_api_key_selects_console_provider(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        assert isinstance(get_mail_provider(), ConsoleMailProvider)

    def test_api_key_present_selects_resend_provider(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
        assert isinstance(get_mail_provider(), ResendMailProvider)

    def test_result_is_cached_across_calls(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        first = get_mail_provider()
        second = get_mail_provider()
        assert first is second

    def test_from_address_defaults_to_resend_dev_sender(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
        monkeypatch.delenv("DIGINYAYA_MAIL_FROM", raising=False)
        provider = get_mail_provider()
        assert provider._from == "onboarding@resend.dev"

    def test_from_address_honors_env_var(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
        monkeypatch.setenv("DIGINYAYA_MAIL_FROM", "noreply@example.com")
        provider = get_mail_provider()
        assert provider._from == "noreply@example.com"


class TestResendMailProvider:
    def test_verification_email_posts_to_resend_with_bearer_auth(self):
        provider = ResendMailProvider(api_key="re_test_key", from_address="noreply@example.com")
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        with patch.object(provider._session, "post", return_value=mock_response) as mock_post:
            provider.send_verification_email("citizen@example.com", "https://app.example.com/verify-email?token=abc")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.resend.com/emails"
        assert kwargs["headers"]["Authorization"] == "Bearer re_test_key"
        assert kwargs["json"]["from"] == "noreply@example.com"
        assert kwargs["json"]["to"] == ["citizen@example.com"]
        assert "verify-email?token=abc" in kwargs["json"]["html"]

    def test_password_reset_email_posts_to_resend(self):
        provider = ResendMailProvider(api_key="re_test_key", from_address="noreply@example.com")
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        with patch.object(provider._session, "post", return_value=mock_response) as mock_post:
            provider.send_password_reset_email("citizen@example.com", "https://app.example.com/reset-password?token=xyz")

        kwargs = mock_post.call_args.kwargs
        assert kwargs["json"]["to"] == ["citizen@example.com"]
        assert "reset-password?token=xyz" in kwargs["json"]["html"]

    def test_network_failure_does_not_raise(self):
        # Both call sites in app.auth.router treat this as fire-and-forget;
        # password_reset_request specifically returns an enumeration-safe
        # generic response regardless of outcome -- a Resend outage must
        # degrade to "email didn't arrive", never a 500.
        provider = ResendMailProvider(api_key="re_test_key", from_address="noreply@example.com")
        with patch.object(provider._session, "post", side_effect=requests.ConnectionError("down")):
            provider.send_verification_email("citizen@example.com", "https://app.example.com/verify-email?token=abc")
            provider.send_password_reset_email("citizen@example.com", "https://app.example.com/reset-password?token=xyz")

    def test_http_error_status_does_not_raise(self):
        provider = ResendMailProvider(api_key="re_bad_key", from_address="noreply@example.com")
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")
        with patch.object(provider._session, "post", return_value=mock_response):
            provider.send_verification_email("citizen@example.com", "https://app.example.com/verify-email?token=abc")


class TestConsoleMailProvider:
    def test_never_raises_and_never_hits_the_network(self):
        provider = ConsoleMailProvider()
        provider.send_verification_email("citizen@example.com", "https://app.example.com/verify-email?token=abc")
        provider.send_password_reset_email("citizen@example.com", "https://app.example.com/reset-password?token=xyz")
