"""Money parsing and formatting. All amounts are integers in minor units ("cents")."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

SYMBOLS = {
    "USD": "$", "CAD": "CA$", "AUD": "A$", "NZD": "NZ$", "SGD": "S$", "HKD": "HK$",
    "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥", "CNY": "CN¥", "KRW": "₩",
    "BRL": "R$", "MXN": "MX$", "CHF": "CHF ", "ZAR": "R", "AED": "AED ", "PHP": "₱",
    "THB": "฿", "VND": "₫", "TRY": "₺", "PLN": "zł ", "SEK": "kr ", "NOK": "kr ",
    "DKK": "kr ", "ILS": "₪", "NGN": "₦", "KES": "KSh ", "CLP": "CLP$",
}
ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP"}


def decimals(currency: str) -> int:
    return 0 if (currency or "").upper() in ZERO_DECIMAL else 2


def to_cents(amount, currency: str = "USD") -> int:
    """Convert a number (int/float/str/Decimal) in major units to integer minor units."""
    d = Decimal(str(amount))
    return int((d * (10 ** decimals(currency))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_amount(text: str, currency: str = "USD") -> int | None:
    """Parse user input like '12.50', '$1,234.56', '12,50' or '-3' into cents. None if invalid."""
    s = re.sub(r"[^\d.,\-]", "", text or "")
    if not s or not re.search(r"\d", s):
        return None
    neg = s.startswith("-")
    s = s.replace("-", "")
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = f"{head.replace(',', '')}.{tail}" if len(tail) in (1, 2) else s.replace(",", "")
    try:
        cents = to_cents(Decimal(s), currency)
    except InvalidOperation:
        return None
    return -cents if neg else cents


def fmt(cents: int, currency: str = "USD") -> str:
    """Format cents for display, e.g. 1234 -> '$12.34', -500 -> '−$5.00'."""
    code = (currency or "USD").upper()
    symbol = SYMBOLS.get(code, f"{code} ")
    places = decimals(code)
    sign = "−" if cents < 0 else ""
    major, minor = divmod(abs(cents), 10 ** places) if places else (abs(cents), 0)
    body = f"{major:,}" + (f".{minor:0{places}d}" if places else "")
    return f"{sign}{symbol}{body}"
