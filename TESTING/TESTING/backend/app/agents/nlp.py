"""Lightweight keyword / signal extraction shared by the agents.

This is deliberately dependency-free (no external LLM call) so the demo is
fully deterministic and offline. The signal maps mirror the tags used in the
precedent corpus so the Research Agent can do meaningful relevance matching.
"""

from __future__ import annotations

import re

# Maps a domain signal -> trigger phrases that may appear in free text.
SIGNAL_LEXICON: dict[str, list[str]] = {
    "non_delivery": ["not deliver", "never deliver", "non-delivery", "did not arrive", "didn't arrive", "not received", "never received", "undelivered"],
    "defective_product": ["defective", "not working", "stopped working", "faulty", "broken", "malfunction", "damaged", "defect"],
    "refund": ["refund", "money back", "return my money", "reimburse"],
    "replacement": ["replace", "replacement", "exchange"],
    "counterfeit": ["fake", "counterfeit", "duplicate", "not genuine", "first copy"],
    "misrepresentation": ["misled", "misleading", "false", "misrepresent", "not as described", "different from", "refurbished"],
    "warranty": ["warranty", "guarantee", "guaranteed"],
    "ecommerce": ["online", "website", "app", "ecommerce", "e-commerce", "marketplace", "ordered online"],
    "service_deficiency": ["service", "support", "customer care", "no response", "ignored", "deficiency"],
    "repair_delay": ["repair", "service centre", "service center", "delay", "months", "waiting"],
    "banking": ["bank", "account", "debit", "atm", "upi", "transaction"],
    "unauthorized_transaction": ["unauthorized", "unauthorised", "fraudulent", "without my consent", "didn't authorise"],
    "wrongful_billing": ["overcharged", "wrongful bill", "billed", "extra charge", "charged twice", "double charge"],
    "subscription": ["subscription", "auto-renew", "auto renew", "recurring", "renewed"],
    "insurance": ["insurance", "policy", "claim repudiat", "premium"],
    "food_delivery": ["food", "restaurant", "contaminated", "stale", "spoiled"],
    "travel": ["flight", "ticket", "airline", "cancelled flight", "booking", "hotel"],
    "real_estate": ["flat", "apartment", "builder", "possession", "property"],
    "electronics": ["laptop", "phone", "mobile", "tv", "television", "electronic", "gadget", "appliance"],
    "automobile": ["car", "vehicle", "bike", "automobile", "engine"],
    "furniture": ["furniture", "sofa", "table", "chair", "bed"],
}

_AMOUNT_RE = re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_DATE_RE = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{2,4})\b",
    re.IGNORECASE,
)


def extract_signals(text: str) -> list[str]:
    """Return the domain signals present in a block of free text."""
    lowered = text.lower()
    found: list[str] = []
    for signal, triggers in SIGNAL_LEXICON.items():
        if any(trigger in lowered for trigger in triggers):
            found.append(signal)
    return found


def extract_amounts(text: str) -> list[float]:
    out: list[float] = []
    for raw in _AMOUNT_RE.findall(text):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def extract_dates(text: str) -> list[str]:
    return _DATE_RE.findall(text)


def inr(amount: float) -> str:
    """Format a number in the Indian numbering system with a rupee symbol."""
    amount = round(float(amount), 2)
    whole = int(amount)
    frac = amount - whole
    s = str(whole)
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        s = ",".join(groups) + "," + last3
    out = f"Rs. {s}"
    if frac:
        out += f".{int(round(frac * 100)):02d}"
    return out
