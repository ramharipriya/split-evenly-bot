"""Reads a bill image/PDF with Claude. Claude only extracts; it never does split math."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass

import anthropic

from .bill import Bill, BillCharge, BillItem, normalize
from .money import to_cents

log = logging.getLogger(__name__)

IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024

SYSTEM = """You read photos and PDFs of bills and receipts (restaurants, groceries, utilities, \
rent, trips, shopping) and transcribe them into structured data.

Rules:
- Transcribe only what is printed. Never invent, guess or "fill in" items, prices or charges.
- items: every purchased line item, with its line total (quantity already multiplied in). \
Keep the item name as printed, tidied into readable words. If a discount is printed directly \
under one specific item, subtract it from that item's price.
- charges: tax, tip/gratuity, service charges, delivery/booking/other fees (kind "fee"), and \
bill-level discounts, coupons or promotions (kind "discount"). Amounts are always positive \
numbers; the kind says whether they add or subtract.
- Do not list subtotal, total, amount paid, change, or payment lines as items or charges.
- subtotal / total: as printed, or null if not printed. If a tip line is blank or handwritten \
illegibly, leave it out.
- Bills with no itemization (e.g. a utility bill) have an empty items list and just a total.
- currency: ISO 4217 code inferred from symbols, language or location (default USD).
- If the image is not a bill, or is too blurry, cropped or dark to read the amounts reliably, \
set readable to false and explain briefly in problem. Otherwise problem is an empty string."""

MONEY = {"type": "number"}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["readable", "problem", "merchant", "currency", "items", "subtotal", "charges", "total"],
    "properties": {
        "readable": {"type": "boolean"},
        "problem": {"type": "string"},
        "merchant": {"type": "string"},
        "currency": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "quantity", "price"],
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "integer"},
                    "price": MONEY,
                },
            },
        },
        "subtotal": {"anyOf": [MONEY, {"type": "null"}]},
        "charges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "kind", "amount"],
                "properties": {
                    "label": {"type": "string"},
                    "kind": {"type": "string", "enum": ["tax", "tip", "fee", "discount"]},
                    "amount": MONEY,
                },
            },
        },
        "total": {"anyOf": [MONEY, {"type": "null"}]},
    },
}


class UnreadableBill(Exception):
    """The bill could not be read; the message is safe to show the user."""


class ReaderError(Exception):
    """Claude API failed; try again later."""


@dataclass
class BillFile:
    data: bytes
    mime: str  # image/* or application/pdf


class BillReader:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def read(self, file: BillFile) -> Bill:
        b64 = base64.standard_b64encode(file.data).decode()
        if file.mime == "application/pdf":
            media = {"type": "document", "source": {"type": "base64", "media_type": file.mime, "data": b64}}
        elif file.mime in IMAGE_TYPES:
            if len(file.data) > MAX_IMAGE_BYTES:
                raise UnreadableBill("That image is too large. Please send it as a photo (not a file).")
            media = {"type": "image", "source": {"type": "base64", "media_type": file.mime, "data": b64}}
        else:
            raise UnreadableBill("Please send the bill as a photo, image or PDF.")

        try:
            response = await self._client.beta.messages.create(
                model=self._model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
                system=SYSTEM,
                messages=[{"role": "user", "content": [media, {"type": "text", "text": "Read this bill."}]}],
            )
        except anthropic.BadRequestError as e:
            log.warning("Claude rejected the bill: %s", e)
            raise UnreadableBill("I couldn't open that file.") from e
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            log.exception("Claude API error")
            raise ReaderError(str(e)) from e

        if response.stop_reason == "refusal":
            raise UnreadableBill("I couldn't read that as a bill.")
        if response.stop_reason == "max_tokens":
            raise ReaderError("response was cut off")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ReaderError("invalid JSON from Claude") from e
        return to_bill(data)


def to_bill(data: dict) -> Bill:
    """Convert Claude's JSON into a Bill in integer cents, or raise UnreadableBill."""
    if not data.get("readable"):
        raise UnreadableBill(data.get("problem") or "The bill isn't readable.")
    cur = (data.get("currency") or "USD").strip().upper()[:3] or "USD"
    items = [
        BillItem(it["name"], to_cents(it["price"], cur), max(int(it.get("quantity") or 1), 1))
        for it in data.get("items", [])
    ]
    charges = []
    for c in data.get("charges", []):
        cents = abs(to_cents(c["amount"], cur))
        if cents:
            charges.append(BillCharge(c["label"], c["kind"], -cents if c["kind"] == "discount" else cents))
    total = to_cents(data["total"], cur) if data.get("total") is not None else None
    subtotal = to_cents(data["subtotal"], cur) if data.get("subtotal") is not None else None
    if not items and total is None:
        raise UnreadableBill(data.get("problem") or "I couldn't find any items or a total.")
    return normalize(Bill(data.get("merchant") or "the bill", cur, items, charges, total, subtotal))
