from decimal import Decimal

from splitbot.bill import (
    Bill, BillCharge, BillItem, accept_as_is, add_tip, check, has_tip, normalize, split_units, tip_from_percent,
)
from splitbot.money import fmt, parse_amount, to_cents


def make_bill(**kw):
    defaults = dict(
        merchant="Luigi's",
        currency="USD",
        items=[BillItem("Pizza", 1800), BillItem("Wine", 2400)],
        charges=[BillCharge("Tax", "tax", 380), BillCharge("Promo", "discount", -500)],
        total=4080,
        subtotal=4200,
    )
    defaults.update(kw)
    return Bill(**defaults)


def test_consistent_bill_has_no_issues():
    assert check(make_bill()) == []


def test_total_mismatch_reported():
    issues = check(make_bill(total=4100))
    assert len(issues) == 1 and "$41.00" in issues[0] and "missing $0.20" in issues[0]


def test_subtotal_mismatch_reported():
    assert any("subtotal" in i for i in check(make_bill(subtotal=4000)))


def test_accept_as_is_adds_adjustment():
    bill = accept_as_is(make_bill(total=4100))
    assert bill.charges[-1].amount == 20
    assert bill.computed_total == 4100
    assert check(bill) == []


def test_single_total_bill_becomes_one_item():
    bill = normalize(Bill("City Power", "USD", items=[], charges=[], total=12345))
    assert [(i.name, i.price) for i in bill.items] == [("Bill total", 12345)]
    assert check(bill) == []


def test_single_total_bill_with_listed_tax():
    bill = normalize(Bill("Water", "USD", charges=[BillCharge("Tax", "tax", 500)], total=5500))
    assert bill.items[0].price == 5000
    assert check(bill) == []


def test_tip_from_percent_uses_pretax_subtotal():
    bill = make_bill()  # items 4200, tax 380, promo -500
    assert tip_from_percent(bill, Decimal("18")) == 756
    assert tip_from_percent(bill, Decimal("12.5")) == 525
    assert tip_from_percent(Bill("X", "USD", [BillItem("A", 1003)]), Decimal("15")) == 150  # 150.45


def test_add_tip_keeps_bill_consistent():
    bill = add_tip(make_bill(), 756, "Tip (18%)")
    assert has_tip(bill)
    assert bill.total == 4080 + 756
    assert check(bill) == []


def test_add_tip_without_printed_total():
    bill = add_tip(make_bill(total=None), 500)
    assert bill.total is None
    assert bill.computed_total == 4080 + 500


def test_split_units_divides_line_into_single_items():
    bill = make_bill(items=[BillItem("Bread", 500), BillItem("Cucumber salad", 2001, 2)])
    assert bill.items[1].label == "Cucumber salad ×2"
    assert split_units(bill, 1) == 2
    assert [(i.label, i.price, i.quantity) for i in bill.items] == [
        ("Bread", 500, 1), ("Cucumber salad", 1001, 1), ("Cucumber salad", 1000, 1),
    ]


def test_old_saved_items_without_quantity_still_load():
    bill = Bill.from_dict({"merchant": "X", "currency": "USD", "items": [{"name": "A", "price": 100}]})
    assert bill.items[0].quantity == 1


def test_roundtrip_dict():
    bill = make_bill()
    assert Bill.from_dict(bill.to_dict()) == bill


def test_money_helpers():
    assert to_cents(12.34) == 1234
    assert to_cents("0.1") == 10
    assert to_cents(1500, "JPY") == 1500
    assert parse_amount("$1,234.56") == 123456
    assert parse_amount("12,50") == 1250
    assert parse_amount("-3") == -300
    assert parse_amount("abc") is None
    assert fmt(123456) == "$1,234.56"
    assert fmt(-500, "EUR") == "−€5.00"
    assert fmt(1500, "JPY") == "¥1,500"
