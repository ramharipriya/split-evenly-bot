"""Tests for the pieces around the math: bill parsing, input parsing, rendering, storage."""

import asyncio

import pytest
from aiogram.fsm.storage.base import StorageKey

from splitbot.bill import Bill, BillCharge, BillItem, split_units
from splitbot.handlers import Split, add_people, compute, parse_name_price, parse_names, parse_tip
from splitbot.reader import UnreadableBill, to_bill
from splitbot.storage import SQLiteStorage
from splitbot import views


def claude_json(**kw):
    base = dict(readable=True, problem="", merchant="Luigi's", currency="USD", items=[],
                subtotal=None, charges=[], total=None)
    base.update(kw)
    return base


def test_to_bill_converts_to_cents_and_signs_discounts():
    bill = to_bill(claude_json(
        items=[{"name": "Pizza", "quantity": 2, "price": 36.0}, {"name": "Wine", "quantity": 1, "price": 24.5}],
        charges=[{"label": "Tax", "kind": "tax", "amount": 5.3}, {"label": "Promo", "kind": "discount", "amount": 5}],
        subtotal=60.5, total=60.8,
    ))
    assert [(i.label, i.price, i.quantity) for i in bill.items] == [("Pizza ×2", 3600, 2), ("Wine", 2450, 1)]
    assert [c.amount for c in bill.charges] == [530, -500]
    assert bill.total == 6080 and bill.subtotal == 6050


def test_to_bill_unreadable():
    with pytest.raises(UnreadableBill, match="blurry"):
        to_bill(claude_json(readable=False, problem="Too blurry"))
    with pytest.raises(UnreadableBill):
        to_bill(claude_json())  # readable but nothing on it: never invent items


def test_to_bill_total_only():
    bill = to_bill(claude_json(merchant="City Water", total=84.2))
    assert [(i.name, i.price) for i in bill.items] == [("Bill total", 8420)]


def test_parse_name_price():
    assert parse_name_price("Pizza 18.50", "USD") == ("Pizza", 1850)
    assert parse_name_price("Garlic bread - $6.50", "USD") == ("Garlic bread", 650)
    assert parse_name_price("12", "USD") == (None, 1200)
    assert parse_name_price("Coupon -5", "USD") == ("Coupon", -500)
    assert parse_name_price("7up 2.50", "USD") == ("7up", 250)
    assert parse_name_price("Pizza", "USD") == ("Pizza", None)


def test_parse_tip():
    bill = Bill("X", "USD", [BillItem("A", 4000)], [BillCharge("Tax", "tax", 350)], total=4350)
    assert parse_tip("18%", bill) == (720, "Tip (18%)")
    assert parse_tip("12.5 %", bill) == (500, "Tip (12.5%)")
    assert parse_tip("$7.25", bill) == (725, "Tip")
    assert parse_tip("abc", bill) is None
    assert parse_tip("0%", bill) is None
    assert parse_tip("-5", bill) is None


def test_parse_names_and_me():
    people = add_people([], parse_names("me, Sam,  Priya ,sam, Me", "Hari"))
    assert people == [
        {"name": "Hari", "me": True},
        {"name": "Sam", "me": False},
        {"name": "Priya", "me": False},
    ]


def sample_data():
    bill = Bill("Luigi's", "USD",
                [BillItem("Nachos", 1200), BillItem("Burger", 1500), BillItem("Salad", 900)],
                [BillCharge("Tip", "tip", 360)], total=3960)
    return {
        "people": [{"name": "Hari", "me": True}, {"name": "Sam", "me": False}, {"name": "Priya", "me": False}],
        "parties": [[1, 2]],
        "bill": bill.to_dict(),
        "mode": "items",
        "assign": [[0, 1, 2], [1], [0]],
        "charge_method": "proportional",
        "payer": 0,
    }


def test_summary_and_messages():
    data = sample_data()
    bill = Bill.from_dict(data["bill"])
    result = compute(data)
    text = views.summary_text(data, bill, result)
    assert "💑 <b>Sam &amp; Priya — " in text
    assert "Nachos (⅓)" in text
    assert "split by what each person had" in text
    msgs = views.personal_messages(data, bill, result)
    assert len(msgs) == 1
    label, msg = msgs[0]
    party_total = result.party_of(1).total
    assert label == "Sam & Priya"
    assert msg.startswith(f"Hey Sam & Priya, your share of Luigi's is ${party_total / 100:.2f}")
    assert "send it to me" in msg
    assert sum(p.total for p in result.parties) == 3960


def test_couple_shares_one_of_two_salads():
    # Haripriya & Vivek are a couple and share one cucumber salad; Harshitha has the other.
    bill = to_bill(claude_json(items=[{"name": "Cucumber salad", "quantity": 2, "price": 20}], total=20))
    data = {
        "people": [{"name": "Haripriya", "me": True}, {"name": "Vivek", "me": False},
                   {"name": "Harshitha", "me": False}],
        "parties": [[0, 1]],
        "mode": "items",
        "payer": 0,
    }
    split_units(bill, 0)
    data["bill"] = bill.to_dict()
    data["assign"] = [[0, 1], [2]]
    result = compute(data)
    assert [p.total for p in result.people] == [500, 500, 1000]
    assert [(p.members, p.total) for p in result.parties] == [([0, 1], 1000), ([2], 1000)]


def test_split_button_only_for_multi_quantity_items():
    data = sample_data()
    data.update(cur=0, from_summary=False)
    bill = Bill.from_dict(data["bill"])
    buttons = lambda: [b.callback_data for row in views.assign_screen(data, bill)[1].inline_keyboard for b in row]
    assert "as:units" not in buttons()
    bill.items[0].quantity = 2
    assert "as:units" in buttons()


def test_screens_render():
    data = sample_data()
    bill = Bill.from_dict(data["bill"])
    data.update(cur=0, from_summary=False)
    for screen in (views.tip_question(bill), views.tip_prompt(bill), views.names_screen(data), views.pairing_screen(data), views.review_screen(bill),
                   views.fix_pick_screen(bill), views.assign_screen(data, bill),
                   views.assign_summary(data, bill), views.charges_screen(bill), views.payer_screen(data)):
        text, markup = screen
        assert text
        for row in markup.inline_keyboard:
            for b in row:
                assert b.callback_data is None or len(b.callback_data.encode()) <= 64


def test_sqlite_storage_survives_reopen(tmp_path):
    path = tmp_path / "state.db"
    key = StorageKey(bot_id=1, chat_id=42, user_id=42)

    async def run():
        s = SQLiteStorage(path)
        await s.set_state(key, Split.review)
        await s.set_data(key, {"people": [{"name": "Sam"}]})
        await s.close()
        s2 = SQLiteStorage(path)
        assert await s2.get_state(key) == Split.review.state
        assert await s2.get_data(key) == {"people": [{"name": "Sam"}]}
        await s2.set_state(key, None)
        assert await s2.get_state(key) is None
        await s2.close()

    asyncio.run(run())
