"""Bill model (as read from a receipt) and deterministic consistency checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .money import fmt

CHARGE_KINDS = ("tax", "tip", "fee", "discount", "adjustment")


@dataclass
class BillItem:
    name: str
    price: int  # cents, line total
    quantity: int = 1

    @property
    def label(self) -> str:
        return f"{self.name} ×{self.quantity}" if self.quantity > 1 else self.name


@dataclass
class BillCharge:
    label: str
    kind: str  # one of CHARGE_KINDS
    amount: int  # cents, signed: discounts are negative


@dataclass
class Bill:
    merchant: str
    currency: str
    items: list[BillItem] = field(default_factory=list)
    charges: list[BillCharge] = field(default_factory=list)
    total: int | None = None  # as printed on the bill
    subtotal: int | None = None  # as printed on the bill

    @property
    def items_sum(self) -> int:
        return sum(i.price for i in self.items)

    @property
    def charges_sum(self) -> int:
        return sum(c.amount for c in self.charges)

    @property
    def computed_total(self) -> int:
        return self.items_sum + self.charges_sum

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Bill":
        return cls(
            merchant=d.get("merchant") or "",
            currency=d.get("currency") or "USD",
            items=[BillItem(**i) for i in d.get("items", [])],
            charges=[BillCharge(**c) for c in d.get("charges", [])],
            total=d.get("total"),
            subtotal=d.get("subtotal"),
        )


def normalize(bill: Bill) -> Bill:
    """Turn a total-only bill (e.g. a utility bill) into a single splittable item."""
    if not bill.items and bill.total is not None:
        bill.items = [BillItem("Bill total", bill.total - bill.charges_sum)]
    return bill


def check(bill: Bill) -> list[str]:
    """Return human-readable problems; empty list means everything adds up."""
    cur = bill.currency
    issues: list[str] = []
    if not bill.items:
        issues.append("No items found on the bill.")
    for i, item in enumerate(bill.items, 1):
        if item.price < 0:
            issues.append(f"Item {i} ({item.label}) has a negative price.")
    if bill.subtotal is not None and bill.subtotal != bill.items_sum:
        issues.append(
            f"Items add up to {fmt(bill.items_sum, cur)}, "
            f"but the bill's subtotal says {fmt(bill.subtotal, cur)}."
        )
    if bill.total is None:
        issues.append(f"No total found on the bill; it would be {fmt(bill.computed_total, cur)}.")
    elif bill.total != bill.computed_total:
        diff = bill.total - bill.computed_total
        issues.append(
            f"Items and charges add up to {fmt(bill.computed_total, cur)}, but the bill total is "
            f"{fmt(bill.total, cur)} ({'missing' if diff > 0 else 'extra'} {fmt(abs(diff), cur)})."
        )
    return issues


def split_units(bill: Bill, idx: int) -> int:
    """Replace a quantity-N line with N single items (e.g. 2 salads -> 2 lines).

    Leftover cents go to the first units so the prices still add up. Returns N.
    """
    item = bill.items[idx]
    n = max(item.quantity, 1)
    base, extra = divmod(item.price, n)
    units = [BillItem(item.name, base + (1 if k < extra else 0)) for k in range(n)]
    bill.items[idx : idx + 1] = units
    return n


def has_tip(bill: Bill) -> bool:
    return any(c.kind == "tip" for c in bill.charges)


def tip_from_percent(bill: Bill, percent: Decimal) -> int:
    """Tip on the pre-tax item subtotal, rounded half-up to whole cents."""
    return int((Decimal(bill.items_sum) * percent / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def add_tip(bill: Bill, cents: int, label: str = "Tip") -> Bill:
    """Add a tip the receipt didn't include; the printed total grows by the same amount."""
    bill.charges.append(BillCharge(label, "tip", cents))
    if bill.total is not None:
        bill.total += cents
    return bill


def accept_as_is(bill: Bill) -> Bill:
    """User confirmed despite a mismatch: record the gap so shares add up to the printed total."""
    if bill.total is None:
        bill.total = bill.computed_total
    diff = bill.total - bill.computed_total
    if diff:
        bill.charges.append(BillCharge("Unexplained difference", "adjustment", diff))
    bill.subtotal = None
    return bill
