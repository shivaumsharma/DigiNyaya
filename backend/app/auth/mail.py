"""Email provider abstraction.

get_mail_provider() auto-selects: ResendMailProvider when RESEND_API_KEY is
set, ConsoleMailProvider (dev stub) otherwise -- same auto-detect shape as
app.llm.factory's provider selection. Nothing in the auth router needs to
change either way.
"""

from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod

import requests

logger = logging.getLogger("diginyaya.auth.mail")

_RESEND_API_URL = "https://api.resend.com/emails"
_RESEND_TIMEOUT = 10


class MailProvider(ABC):
    @abstractmethod
    def send_verification_email(self, to: str, link: str) -> None: ...

    @abstractmethod
    def send_password_reset_email(self, to: str, link: str) -> None: ...


class ConsoleMailProvider(MailProvider):
    """Dev stub: logs the link instead of sending a real email."""

    def send_verification_email(self, to: str, link: str) -> None:
        logger.info("Verification email for %s: %s", to, link)
        print(f"[DEV MAIL STUB] Verify email for {to}: {link}")

    def send_password_reset_email(self, to: str, link: str) -> None:
        logger.info("Password reset email for %s: %s", to, link)
        print(f"[DEV MAIL STUB] Reset password for {to}: {link}")


class ResendMailProvider(MailProvider):
    """Sends real email via Resend's REST API
    (https://resend.com/docs/api-reference/emails/send-email).

    Never raises -- matches this codebase's provider-fallback philosophy
    (see app.language.translator/detector's "never raises" contract): both
    call sites (password_reset_request, _send_verification_email in
    app.auth.router) fire-and-forget this with no return value, and
    password_reset_request specifically returns an enumeration-safe generic
    response regardless of outcome -- a transient Resend outage should mean
    the email doesn't arrive this one time, not a 500.
    """

    def __init__(self, api_key: str, from_address: str) -> None:
        self._api_key = api_key
        self._from = from_address
        self._session = requests.Session()

    def _send(self, to: str, subject: str, html: str) -> None:
        try:
            response = self._session.post(
                _RESEND_API_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"from": self._from, "to": [to], "subject": subject, "html": html},
                timeout=_RESEND_TIMEOUT,
            )
            response.raise_for_status()
            logger.info("email sent via resend", extra={"event": "resend_email_sent", "to": to, "subject": subject})
        except requests.RequestException as exc:
            logger.error(
                "resend send failed",
                extra={"event": "resend_send_failed", "to": to, "subject": subject, "error": str(exc)},
            )

    def send_verification_email(self, to: str, link: str) -> None:
        self._send(
            to,
            "Verify your DigiNyaya account",
            f"<p>Welcome to DigiNyaya.</p>"
            f'<p><a href="{link}">Click here to verify your email address</a>.</p>'
            f"<p>This link expires in 24 hours. If you didn't create a DigiNyaya account, you can ignore this email.</p>",
        )

    def send_password_reset_email(self, to: str, link: str) -> None:
        self._send(
            to,
            "Reset your DigiNyaya password",
            f"<p>Someone requested a password reset for this DigiNyaya account.</p>"
            f'<p><a href="{link}">Click here to reset your password</a>.</p>'
            f"<p>If this wasn't you, you can safely ignore this email -- your password won't change.</p>",
        )


_provider: MailProvider | None = None
_provider_lock = threading.Lock()


def get_mail_provider() -> MailProvider:
    """Process-wide provider singleton -- same lazy-init pattern as
    app.language.translator.get_translator(). Re-reads env vars on first
    call only; a changed RESEND_API_KEY requires a process restart to take
    effect, same as every other env-var-configured provider in this app.
    """
    global _provider
    if _provider is None:
        with _provider_lock:
            if _provider is None:
                api_key = os.getenv("RESEND_API_KEY")
                if api_key:
                    from_address = os.getenv("DIGINYAYA_MAIL_FROM", "onboarding@resend.dev")
                    _provider = ResendMailProvider(api_key, from_address)
                else:
                    _provider = ConsoleMailProvider()
    return _provider
