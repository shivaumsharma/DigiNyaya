"""SMS provider abstraction.

Swap ConsoleSmsProvider for a real Twilio/MSG91/WhatsApp Business API
implementation later -- nothing in the auth router needs to change, it only
ever calls get_sms_provider().send_otp(...).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("diginyaya.auth.sms")


class SmsProvider(ABC):
    @abstractmethod
    def send_otp(self, phone: str, code: str) -> None: ...


class ConsoleSmsProvider(SmsProvider):
    """Dev stub: logs the OTP instead of sending a real SMS."""

    def send_otp(self, phone: str, code: str) -> None:
        logger.info("OTP for %s: %s", phone, code)
        print(f"[DEV SMS STUB] OTP for {phone}: {code}")


def get_sms_provider() -> SmsProvider:
    return ConsoleSmsProvider()
