"""Deterministic bill-splitting math. Integer cents only; no I/O, no Claude.

People are referred to by index. Each person's amount is calculated first; parties
(couples, people paying together) are then just the sum of their members.

Rounding: every split starts from exact fractional shares. Whole cents are handed
out with the largest-remainder method, and ties go to whoever has received the
fewest extra cents so far, so leftover cents rotate fairly across the bill.
The per-person totals always sum exactly to the bill total.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal, Sequence

Mode = Literal["items", "even"]
ChargeMethod = Literal["proportional", "even"]


@dataclass(frozen=True)
class Item:
    name: str
    price: int  # cents
    people: tuple[int, ...] = ()


@dataclass(frozen=True)
class Charge:
    label: str
    amount: int  # cents, signed: tax/tip/fees positive, discounts negative


@dataclass
class PersonShare:
    index: int
    name: str
    items: list[tuple[int, int]] = field(default_factory=list)  # (item index, cents)
    items_total: int = 0
    charges_total: int = 0

    @property
    def total(self) -> int:
        return self.items_total + self.charges_total


@dataclass
class PartyShare:
    members: list[int]
    total: int


@dataclass
class SplitResult:
    people: list[PersonShare]
    parties: list[PartyShare]
    total: int
    mode: Mode
    charge_method: ChargeMethod | None  # None when not applicable

    def party_of(self, person: int) -> PartyShare:
        return next(p for p in self.parties if person in p.members)


class SplitError(ValueError):
    pass


def _round_shares(exact: Sequence[Fraction], tally: list[int]) -> list[int]:
    """Round exact shares (whose sum is a whole number) to ints with the same sum.

    Largest fractional remainder gets the leftover cents first; ties go to the person
    with the fewest extra cents so far (`tally`, updated in place), then by index.
    """
    target = sum(exact)
    assert target.denominator == 1, "exact shares must sum to whole cents"
    floors = [math.floor(x) for x in exact]
    leftover = int(target) - sum(floors)
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i] - floors[i]), tally[i], i))
    for i in order[:leftover]:
        floors[i] += 1
        tally[i] += 1
    return floors


def build_parties(n_people: int, parties: Sequence[Sequence[int]]) -> list[list[int]]:
    """Return every party (explicit groups plus singletons), ordered by first member."""
    seen: set[int] = set()
    groups: list[list[int]] = []
    for group in parties:
        members = sorted(set(group))
        if len(members) < 2:
            continue
        for m in members:
            if not 0 <= m < n_people:
                raise SplitError(f"party member {m} is not a person")
            if m in seen:
                raise SplitError(f"person {m} is in more than one party")
            seen.add(m)
        groups.append(members)
    groups += [[i] for i in range(n_people) if i not in seen]
    return sorted(groups, key=lambda g: g[0])


def split_bill(
    names: Sequence[str],
    items: Sequence[Item],
    charges: Sequence[Charge] = (),
    parties: Sequence[Sequence[int]] = (),
    mode: Mode = "items",
    charge_method: ChargeMethod = "proportional",
) -> SplitResult:
    n = len(names)
    if n == 0:
        raise SplitError("need at least one person")
    groups = build_parties(n, parties)
    subtotal = sum(it.price for it in items)
    charges_sum = sum(c.amount for c in charges)
    total = subtotal + charges_sum
    people = [PersonShare(i, name) for i, name in enumerate(names)]
    tally = [0] * n

    if mode == "even":
        # Split the whole bill (items + charges) equally between individuals.
        for p, cents in zip(people, _round_shares([Fraction(total, n)] * n, tally)):
            p.items_total = cents
        method = None
    else:
        exact_items = [Fraction(0)] * n
        for idx, it in enumerate(items):
            who = sorted(set(it.people))
            if not who:
                raise SplitError(f"item {it.name!r} has nobody assigned")
            if any(not 0 <= w < n for w in who):
                raise SplitError(f"item {it.name!r} is assigned to an unknown person")
            share = Fraction(it.price, len(who))
            sub_tally = [tally[w] for w in who]
            for w, cents in zip(who, _round_shares([share] * len(who), sub_tally)):
                people[w].items.append((idx, cents))
                people[w].items_total += cents
                exact_items[w] += share
            for w, t in zip(who, sub_tally):
                tally[w] = t

        method = charge_method if charges else None
        if charges_sum or charges:
            if charge_method == "proportional" and subtotal != 0:
                exact_charges = [Fraction(charges_sum) * x / subtotal for x in exact_items]
            else:
                exact_charges = [Fraction(charges_sum, n)] * n  # counts individuals, not parties
            for p, cents in zip(people, _round_shares(exact_charges, tally)):
                p.charges_total = cents

    party_shares = [PartyShare(g, sum(people[m].total for m in g)) for g in groups]
    result = SplitResult(people, party_shares, total, mode, method)
    assert sum(p.total for p in people) == total
    assert sum(p.total for p in party_shares) == total
    return result


def settlements(result: SplitResult, payer: int) -> list[tuple[PartyShare, int]]:
    """Who owes the payer what: one entry per party other than the payer's own."""
    payer_party = result.party_of(payer)
    return [(p, p.total) for p in result.parties if p is not payer_party and p.total != 0]
