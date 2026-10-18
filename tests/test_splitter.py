import random

import pytest

from splitbot.splitter import Charge, Item, SplitError, settlements, split_bill

NAMES = ["Alex", "Sam", "Priya", "Jo"]


def totals(result):
    return [p.total for p in result.people]


def test_single_owner_items_no_charges():
    r = split_bill(NAMES[:2], [Item("Pasta", 1800, (0,)), Item("Pizza", 1500, (1,))])
    assert totals(r) == [1800, 1500]
    assert r.total == 3300
    assert r.charge_method is None


def test_shared_item_split_evenly_between_its_people():
    r = split_bill(NAMES[:3], [Item("Nachos", 1200, (0, 1, 2)), Item("Steak", 3000, (1,))])
    assert totals(r) == [400, 3400, 400]
    assert r.people[1].items == [(0, 400), (1, 3000)]


def test_shared_item_rounding_hands_out_leftover_cents():
    r = split_bill(NAMES[:3], [Item("Fries", 1000, (0, 1, 2))])
    assert sorted(totals(r)) == [333, 333, 334]
    assert sum(totals(r)) == 1000


def test_rounding_cents_rotate_across_items():
    # Three items of $10 split three ways: each person should get exactly one extra cent.
    items = [Item(f"Dish {i}", 1000, (0, 1, 2)) for i in range(3)]
    r = split_bill(NAMES[:3], items)
    assert totals(r) == [1000, 1000, 1000]


def test_proportional_charges():
    items = [Item("A", 3000, (0,)), Item("B", 1000, (1,))]
    r = split_bill(NAMES[:2], items, [Charge("Tax", 400), Charge("Tip", 800)])
    assert [p.charges_total for p in r.people] == [900, 300]
    assert totals(r) == [3900, 1300]
    assert r.charge_method == "proportional"


def test_even_charges_count_individuals_not_parties():
    items = [Item("A", 3000, (0,)), Item("B", 1000, (1,)), Item("C", 2000, (2,))]
    r = split_bill(NAMES[:3], items, [Charge("Tip", 900)], parties=[[0, 1]], charge_method="even")
    assert [p.charges_total for p in r.people] == [300, 300, 300]
    assert [p.total for p in r.parties] == [3000 + 1000 + 600, 2000 + 300]


def test_discount_proportional():
    items = [Item("A", 3000, (0,)), Item("B", 1000, (1,))]
    r = split_bill(NAMES[:2], items, [Charge("Tax", 400), Charge("Coupon", -800)])
    assert [p.charges_total for p in r.people] == [-300, -100]
    assert totals(r) == [2700, 900]
    assert r.total == 3600


def test_discount_even():
    items = [Item("A", 3000, (0,)), Item("B", 1000, (1,))]
    r = split_bill(NAMES[:2], items, [Charge("Coupon", -1000)], charge_method="even")
    assert totals(r) == [2500, 500]


def test_discount_with_rounding_sums_exactly():
    items = [Item("A", 1001, (0,)), Item("B", 1003, (1,)), Item("C", 997, (2,))]
    r = split_bill(NAMES[:3], items, [Charge("Promo", -500), Charge("Tax", 263)])
    assert sum(totals(r)) == r.total == 1001 + 1003 + 997 - 500 + 263


def test_proportional_charge_rounding():
    items = [Item("A", 1000, (0,)), Item("B", 1000, (1,)), Item("C", 1000, (2,))]
    r = split_bill(NAMES[:3], items, [Charge("Tax", 100)])
    assert sorted(p.charges_total for p in r.people) == [33, 33, 34]
    assert sum(totals(r)) == 3100


def test_party_totals_sum_members_computed_individually():
    items = [Item("A", 1500, (0,)), Item("B", 2500, (1,)), Item("C", 1000, (2,))]
    r = split_bill(NAMES[:3], items, [Charge("Tax", 500)], parties=[[0, 1]])
    assert totals(r) == [1650, 2750, 1100]
    assert [(p.members, p.total) for p in r.parties] == [([0, 1], 4400), ([2], 1100)]


def test_party_sharing_an_item_with_others():
    # Sam & Priya are a party; they share nachos with Alex, plus each has their own dish.
    items = [
        Item("Nachos", 1200, (0, 1, 2)),
        Item("Burger", 1500, (1,)),
        Item("Salad", 900, (2,)),
        Item("Soup", 600, (0,)),
    ]
    r = split_bill(NAMES[:3], items, [Charge("Tip", 420)], parties=[[1, 2]])
    # items: Alex 400+600=1000, Sam 400+1500=1900, Priya 400+900=1300 (subtotal 4200)
    assert [p.items_total for p in r.people] == [1000, 1900, 1300]
    assert [p.charges_total for p in r.people] == [100, 190, 130]
    assert [(p.members, p.total) for p in r.parties] == [([0], 1100), ([1, 2], 2090 + 1430)]


def test_party_shared_item_counts_each_member():
    # An item shared by a whole 2-person party plus one other person is split 3 ways.
    r = split_bill(NAMES[:3], [Item("Pitcher", 900, (0, 1, 2))], parties=[[0, 1]])
    assert [p.total for p in r.parties] == [600, 300]


def test_multiple_parties():
    items = [Item("Feast", 4000, (0, 1, 2, 3))]
    r = split_bill(NAMES, items, parties=[[0, 3], [1, 2]])
    assert [(p.members, p.total) for p in r.parties] == [([0, 3], 2000), ([1, 2], 2000)]


def test_split_everything_evenly():
    items = [Item("A", 5000, ()), Item("B", 100, ())]  # assignments ignored
    r = split_bill(NAMES[:3], items, [Charge("Tax", 400), Charge("Disc", -500)], mode="even")
    assert sorted(totals(r)) == [1666, 1667, 1667]
    assert sum(totals(r)) == r.total == 5000
    assert r.charge_method is None


def test_split_everything_evenly_with_party_counts_individuals():
    r = split_bill(NAMES[:3], [Item("Total", 9000)], parties=[[0, 1]], mode="even")
    assert [p.total for p in r.parties] == [6000, 3000]


def test_single_total_bill():
    # Utility bill: only a total, represented as one item.
    r = split_bill(NAMES[:3], [Item("Bill total", 10000, (0, 1, 2))])
    assert sorted(totals(r)) == [3333, 3333, 3334]
    r_even = split_bill(NAMES[:3], [Item("Bill total", 10000)], mode="even")
    assert sum(totals(r_even)) == 10000


def test_proportional_with_zero_subtotal_falls_back_to_even():
    r = split_bill(NAMES[:2], [Item("Free", 0, (0,))], [Charge("Fee", 200)])
    assert totals(r) == [100, 100]


def test_unassigned_item_rejected():
    with pytest.raises(SplitError):
        split_bill(NAMES[:2], [Item("A", 100, ())])


def test_person_in_two_parties_rejected():
    with pytest.raises(SplitError):
        split_bill(NAMES[:3], [Item("A", 100, (0,))], parties=[[0, 1], [1, 2]])


def test_settlements_exclude_payers_party():
    items = [Item("A", 1000, (0,)), Item("B", 2000, (1,)), Item("C", 3000, (2,))]
    r = split_bill(NAMES[:3], items, parties=[[1, 2]])
    owed = settlements(r, payer=2)
    assert [(p.members, amt) for p, amt in owed] == [([0], 1000)]


@pytest.mark.parametrize("seed", range(200))
def test_random_bills_always_sum_exactly(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 7)
    names = [f"P{i}" for i in range(n)]
    items = [
        Item(f"I{k}", rng.randint(0, 10000), tuple(rng.sample(range(n), rng.randint(1, n))))
        for k in range(rng.randint(1, 12))
    ]
    charges = [Charge(f"C{k}", rng.randint(-2000, 3000)) for k in range(rng.randint(0, 4))]
    people = list(range(n))
    rng.shuffle(people)
    parties = [people[i : i + 2] for i in range(0, n - 1, 3)]
    for mode in ("items", "even"):
        for method in ("proportional", "even"):
            r = split_bill(names, items, charges, parties, mode=mode, charge_method=method)
            expected = sum(i.price for i in items) + sum(c.amount for c in charges)
            assert sum(p.total for p in r.people) == expected
            assert sum(p.total for p in r.parties) == expected
            if mode == "items":
                for idx, it in enumerate(items):
                    got = [c for p in r.people for (i, c) in p.items if i == idx]
                    assert sum(got) == it.price
                    assert max(got) - min(got) <= 1
