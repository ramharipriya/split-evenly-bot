"""Message texts and inline keyboards for each step. Pure functions of the split data."""

from __future__ import annotations

from html import escape
from urllib.parse import quote

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from .bill import Bill, check
from .money import fmt
from .splitter import SplitResult, settlements

Screen = tuple[str, InlineKeyboardMarkup | None]
FRACTIONS = {2: "½", 3: "⅓", 4: "¼", 5: "⅕", 6: "⅙", 8: "⅛"}


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def chunk(buttons: list[InlineKeyboardButton], size: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + size] for i in range(0, len(buttons), size)]


def join_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " & " + names[-1]


def names_of(data: dict) -> list[str]:
    return [p["name"] for p in data["people"]]


def party_label(data: dict, members: list[int]) -> str:
    return join_names([data["people"][m]["name"] for m in members])


def short(text: str, n: int = 28) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------- names ----------

NAMES_PROMPT = (
    "👥 <b>Who's splitting?</b>\n\nSend everyone's names separated by commas. "
    "Use <b>me</b> for yourself, e.g.\n<code>me, Sam, Priya, Alex</code>"
)


def names_screen(data: dict) -> Screen:
    lines = [
        f"{i}. {escape(p['name'])}{' (you)' if p.get('me') else ''}"
        for i, p in enumerate(data["people"], 1)
    ]
    text = "👥 <b>Who's splitting?</b>\n\n" + "\n".join(lines)
    return text, kb(
        [btn("➕ Add", "nm:add"), btn("✏️ Edit", "nm:edit")],
        [btn("✅ Confirm", "nm:ok")],
    )


def names_edit_screen(data: dict) -> Screen:
    rows = [
        [btn(f"✏️ {short(p['name'])}", f"nm:ren:{i}"), btn("🗑", f"nm:del:{i}")]
        for i, p in enumerate(data["people"])
    ]
    return "Tap a name to rename it, or 🗑 to remove it.", kb(*rows, [btn("« Done", "nm:back")])


# ---------- pairing ----------

def pairing_intro() -> Screen:
    return (
        "💑 <b>Any couples or people paying together?</b>\nThey'll get one combined total.",
        kb([btn("💑 Pair people", "pr:start"), btn("Skip", "pr:skip")]),
    )


def pairing_screen(data: dict) -> Screen:
    paired = {m for party in data["parties"] for m in party}
    sel = set(data.get("pair_sel", []))
    toggles = [
        btn(("✓ " if i in sel else "") + short(p["name"], 20), f"pr:t:{i}")
        for i, p in enumerate(data["people"])
        if i not in paired
    ]
    unlink = [[btn(f"✂️ Unlink {short(party_label(data, g), 30)}", f"pr:u:{k}")] for k, g in enumerate(data["parties"])]
    text = "Tap 2 or more names, then <b>Link</b> to make them one party."
    if data["parties"]:
        text += "\n\n" + "\n".join(f"💑 {escape(party_label(data, g))}" for g in data["parties"])
    return text, kb(*chunk(toggles, 3), *unlink, [btn("🔗 Link selected", "pr:link"), btn("✅ Done", "pr:done")])


# ---------- bill review ----------

BILL_PROMPT = "📸 Now send a <b>photo or PDF of the bill</b>."


def bill_lines(bill: Bill) -> str:
    cur = bill.currency
    lines = [f"🧾 <b>{escape(bill.merchant)}</b>", ""]
    lines += [f"{i}. {escape(it.label)} — {fmt(it.price, cur)}" for i, it in enumerate(bill.items, 1)]
    if bill.charges:
        lines.append("")
        lines += [f"{escape(c.label)} — {fmt(c.amount, cur)}" for c in bill.charges]
    total = bill.total if bill.total is not None else bill.computed_total
    lines += ["", f"<b>Total — {fmt(total, cur)}</b>"]
    return "\n".join(lines)


def tip_question(bill: Bill) -> Screen:
    total = bill.total if bill.total is not None else bill.computed_total
    return (
        f"🧾 Got <b>{escape(bill.merchant)}</b> ({fmt(total, bill.currency)}).\n\n"
        "💁 <b>Was a tip included on this receipt?</b>",
        kb(
            [btn("✅ Yes, it's included", "tp:yes")],
            [btn("➕ No, add a tip", "tp:add")],
            [btn("No tip", "tp:none")],
        ),
    )


def tip_prompt(bill: Bill) -> Screen:
    return (
        f"How much tip? The subtotal before tax is <b>{fmt(bill.items_sum, bill.currency)}</b>.\n\n"
        "Type a percentage (e.g. <code>18%</code>) or an amount (e.g. <code>12.50</code>).",
        kb([btn("« Back", "tp:back")]),
    )


def review_screen(bill: Bill) -> Screen:
    issues = check(bill)
    text = bill_lines(bill)
    if issues:
        text += "\n\n⚠️ <b>Something looks off:</b>\n" + "\n".join(f"• {escape(i)}" for i in issues)
        text += "\n\nFix an item, or confirm to keep the bill's total."
    else:
        text += "\n\n✅ Everything adds up. Does this look right?"
    return text, kb(
        [btn("✏️ Fix an item", "rv:fix"), btn("➕ Add item", "rv:add")],
        [btn("✅ Confirm anyway" if issues else "✅ Confirm", "rv:ok")],
        [btn("📷 Use a different photo", "rv:photo")],
    )


def fix_pick_screen(bill: Bill) -> Screen:
    cur = bill.currency
    rows = [[btn(f"{i}. {short(it.label)} {fmt(it.price, cur)}", f"rv:e:i:{i - 1}")] for i, it in enumerate(bill.items, 1)]
    rows += [[btn(f"{short(c.label)} {fmt(c.amount, cur)}", f"rv:e:c:{k}")] for k, c in enumerate(bill.charges)]
    total = bill.total if bill.total is not None else bill.computed_total
    rows.append([btn(f"Total {fmt(total, cur)}", "rv:e:t"), btn("➕ Add tax/tip/fee", "rv:e:nc")])
    return "What needs fixing?", kb(*rows, [btn("« Back", "rv:back")])


def fix_prompt(bill: Bill, target: str) -> Screen:
    cur = bill.currency
    kind, _, idx = target.partition(":")
    if kind == "item":
        it = bill.items[int(idx)]
        text = (
            f"Send the new name and price for <b>{escape(it.label)}</b> ({fmt(it.price, cur)}), "
            f"e.g. <code>{escape(short(it.name, 20))} 12.50</code>, or just a price.\n"
            "Send <code>delete</code> to remove it."
        )
    elif kind == "charge":
        c = bill.charges[int(idx)]
        text = (
            f"Send the new amount for <b>{escape(c.label)}</b> ({fmt(c.amount, cur)}).\n"
            "Send <code>delete</code> to remove it."
        )
    elif kind == "total":
        text = "Send the bill's total, e.g. <code>84.20</code>."
    elif kind == "newcharge":
        text = (
            "Send the charge name and amount, e.g. <code>Tip 12</code> or <code>Service fee 4.50</code>. "
            "For a discount use a minus sign, e.g. <code>Coupon -5</code>."
        )
    else:
        text = "Send the item name and price, e.g. <code>Garlic bread 6.50</code>."
    return text, kb([btn("« Cancel", "rv:back")])


# ---------- split mode & item assignment ----------

def mode_screen(bill: Bill) -> Screen:
    total = bill.total if bill.total is not None else bill.computed_total
    return (
        f"How do you want to split <b>{escape(bill.merchant)}</b> ({fmt(total, bill.currency)})?",
        kb([btn("⚖️ Split everything evenly", "md:even")], [btn("🧾 Assign item by item", "md:items")]),
    )


def assign_screen(data: dict, bill: Bill) -> Screen:
    i = data["cur"]
    item = bill.items[i]
    chosen = set(data["assign"][i])
    n = len(data["people"])
    text = f"Item {i + 1} of {len(bill.items)}\n<b>{escape(item.label)}</b> — {fmt(item.price, bill.currency)}\n\n<b>Who had this?</b>"
    if chosen:
        names = [data["people"][p]["name"] for p in sorted(chosen)]
        each = f" ({fmt(item.price // len(chosen), bill.currency)} each)" if len(chosen) > 1 else ""
        text += f"\n{escape(join_names(names))}{each}"
    people = [
        btn(("✓ " if p in chosen else "") + short(person["name"], 20), f"as:t:{p}")
        for p, person in enumerate(data["people"])
    ]
    parties = [
        btn(("✓ " if set(g) <= chosen else "") + "💑 " + short(party_label(data, g), 30), f"as:g:{k}")
        for k, g in enumerate(data["parties"])
    ]
    everyone = btn(("✓ " if len(chosen) == n else "") + "👥 Everyone", "as:all")
    if item.quantity > 1:
        text += f"\n\n<i>Different people had different ones? Split this into {item.quantity} separate items.</i>"
        units = [btn(f"✂️ Split into {item.quantity}", "as:units")]
    else:
        units = []
    if data.get("from_summary"):
        nav = [btn("✅ Back to summary", "as:sum")]
    else:
        nav = [btn("◀ Back", "as:prev")] if i > 0 else []
        nav.append(btn("Next ▶" if i < len(bill.items) - 1 else "Review ▶", "as:next"))
    return text, kb(*chunk(people, 3), *chunk(parties, 1), [everyone], units, nav)


def assign_summary(data: dict, bill: Bill) -> Screen:
    lines = ["<b>Here's who had what</b> (tap an item to change it):", ""]
    rows = []
    for i, it in enumerate(bill.items):
        who = data["assign"][i]
        names = escape(join_names([data["people"][p]["name"] for p in who])) if who else "⚠️ <i>nobody yet</i>"
        lines.append(f"{i + 1}. {escape(it.label)} {fmt(it.price, bill.currency)} — {names}")
        rows.append([btn(f"{'' if who else '⚠️ '}{i + 1}. {short(it.label)}", f"as:open:{i}")])
    return "\n".join(lines), kb(*rows, [btn("✅ Done", "as:done")])


# ---------- charges & payer ----------

def charges_label(bill: Bill) -> str:
    kinds = {c.kind for c in bill.charges}
    words = [w for k, w in (("tax", "tax"), ("tip", "tip"), ("fee", "fees"), ("discount", "discounts"),
                            ("adjustment", "other charges")) if k in kinds]
    return join_names(words) if words else "charges"


def charges_screen(bill: Bill) -> Screen:
    return (
        f"How should <b>{charges_label(bill)}</b> be split?",
        kb([btn("By what each person had", "ch:prop")], [btn("Evenly", "ch:even")]),
    )


def payer_screen(data: dict) -> Screen:
    buttons = [btn(short(p["name"], 24), f"py:{i}") for i, p in enumerate(data["people"])]
    return "💳 <b>Who paid?</b>", kb(*chunk(buttons, 2))


# ---------- final summary ----------

def _item_part(bill: Bill, result: SplitResult, data: dict, idx: int, cents: int) -> str:
    k = len(data["assign"][idx])
    frac = f" ({FRACTIONS.get(k, f'1/{k}')})" if k > 1 else ""
    return f"{escape(bill.items[idx].label)}{frac} {fmt(cents, bill.currency)}"


def _person_detail(bill: Bill, result: SplitResult, data: dict, p: int) -> str:
    share = result.people[p]
    parts = [_item_part(bill, result, data, idx, cents) for idx, cents in share.items]
    if share.charges_total:
        parts.append(f"{charges_label(bill)} {fmt(share.charges_total, bill.currency)}")
    return ", ".join(parts) if parts else "nothing"


def summary_text(data: dict, bill: Bill, result: SplitResult) -> str:
    cur = bill.currency
    payer = data["payer"]
    names = names_of(data)
    lines = [f"🧾 <b>{escape(bill.merchant)}</b> — <b>{fmt(result.total, cur)}</b>"]
    if result.mode == "even":
        lines.append(f"Paid by {escape(names[payer])} · Everything split evenly")
    elif result.charge_method:
        how = "by what each person had" if result.charge_method == "proportional" else "evenly"
        lines.append(f"Paid by {escape(names[payer])} · {charges_label(bill).capitalize()} split {how}")
    else:
        lines.append(f"Paid by {escape(names[payer])}")

    for party in result.parties:
        lines.append("")
        if len(party.members) == 1:
            p = party.members[0]
            lines.append(f"👤 <b>{escape(names[p])} — {fmt(party.total, cur)}</b>")
            if result.mode == "items":
                lines.append(f"    {_person_detail(bill, result, data, p)}")
        else:
            lines.append(f"💑 <b>{escape(party_label(data, party.members))} — {fmt(party.total, cur)}</b>")
            if result.mode == "items":
                for p in party.members:
                    lines.append(f"    {escape(names[p])}: {_person_detail(bill, result, data, p)}")
            lines.append("    <i>" + " · ".join(
                f"{escape(names[p])} {fmt(result.people[p].total, cur)}" for p in party.members) + "</i>")

    owed = settlements(result, payer)
    lines += ["", "💸 <b>Who owes whom</b>"]
    if owed:
        lines += [f"{escape(party_label(data, party.members))} → {escape(names[payer])}: {fmt(amount, cur)}"
                  for party, amount in owed]
    else:
        lines.append("Nobody owes anything.")
    return "\n".join(lines)


def share_text(data: dict, bill: Bill, result: SplitResult) -> str:
    cur = bill.currency
    names = names_of(data)
    lines = [f"🧾 {bill.merchant} — {fmt(result.total, cur)}"]
    for party in result.parties:
        line = f"{party_label(data, party.members)}: {fmt(party.total, cur)}"
        if len(party.members) > 1:
            line += " (" + ", ".join(f"{names[p]} {fmt(result.people[p].total, cur)}" for p in party.members) + ")"
        lines.append(line)
    lines.append(f"Paid by {names[data['payer']]}.")
    return "\n".join(lines)


def personal_messages(data: dict, bill: Bill, result: SplitResult) -> list[tuple[str, str]]:
    """(party label, ready-to-send message) for everyone who owes the payer."""
    payer = data["payer"]
    payer_name = "me" if data["people"][payer].get("me") else data["people"][payer]["name"]
    out = []
    for party, amount in settlements(result, payer):
        label = party_label(data, party.members)
        msg = f"Hey {label}, your share of {bill.merchant} is {fmt(amount, bill.currency)}."
        if amount > 0:
            msg += f" Please send it to {payer_name} 🙏"
        out.append((label, msg))
    return out


def final_keyboard(data: dict, bill: Bill, result: SplitResult, bot_username: str) -> InlineKeyboardMarkup:
    text = share_text(data, bill, result)[:1500] + f"\nSplit with @{bot_username}"
    url = f"https://t.me/share/url?url={quote(f'https://t.me/{bot_username}')}&text={quote(text)}"
    return kb(
        [InlineKeyboardButton(text="📤 Share", url=url), btn("📋 Copy messages", "fn:copy")],
        [btn("🆕 New split", "fn:new")],
    )


def copy_screen(messages: list[tuple[str, str]]) -> Screen:
    if not messages:
        return "Nobody owes anything, so there's nothing to send. 🎉", None
    text = "Tap a button to copy that message, then paste it to them:\n\n" + "\n\n".join(
        f"<b>{escape(label)}</b>\n{escape(msg)}" for label, msg in messages
    )
    rows = [[InlineKeyboardButton(text=f"📋 {short(label, 30)}", copy_text=CopyTextButton(text=msg[:256]))]
            for label, msg in messages]
    return text, kb(*rows)
