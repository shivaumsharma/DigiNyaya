"""Email provider abstraction.

Swap ConsoleMailProvider for a real SES/SendGrid/Postmark implementation
later -- nothing in the auth router needs to change.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("diginyaya.auth.mail")


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


def get_mail_provider() -> MailProvider:
    return ConsoleMailProvider()
