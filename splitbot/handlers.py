"""Telegram conversation flow. All split state lives in the FSM (persisted to SQLite)."""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup, Message

from . import views
from .bill import (
    Bill, BillCharge, BillItem, accept_as_is, add_tip, check, has_tip, split_units, tip_from_percent,
)
from .money import parse_amount
from .reader import BillFile, BillReader, ReaderError, UnreadableBill
from .splitter import Charge, Item, SplitResult, split_bill

log = logging.getLogger(__name__)

MAX_PEOPLE = 30
ME_WORDS = {"me", "myself", "i"}


class Split(StatesGroup):
    names = State()
    names_add = State()
    names_rename = State()
    pairing = State()
    bill = State()
    review = State()
    fix_value = State()
    tip = State()
    tip_value = State()
    mode = State()
    assign = State()
    charges = State()
    payer = State()
    done = State()


NAME_STATES = (Split.names, Split.names_add, Split.names_rename)
REVIEW_STATES = (Split.review, Split.fix_value)
TIP_STATES = (Split.tip, Split.tip_value)

private = Router(name="private")
private.message.filter(F.chat.type == "private")
private.callback_query.filter(F.message.chat.type == "private")

groups = Router(name="groups")

GROUP_REPLY = (
    "👋 Thanks for adding me! I work in private chat only: one person splits the bill with me, "
    "then shares the results. Message me directly at @{username}. Leaving this group now."
)


# ---------- helpers ----------

async def show(target: Message | CallbackQuery, screen: views.Screen) -> None:
    """Edit the callback's message in place, or send a new message."""
    text, markup = screen
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
            target = target.message
    await send_long(target, text, markup)


async def send_long(message: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Send text, splitting on blank lines to stay under Telegram's 4096-char limit."""
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if cur and len(cur) + len(block) + 2 > 4000:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    parts.append(cur)
    for i, part in enumerate(parts):
        await message.answer(part, reply_markup=markup if i == len(parts) - 1 else None)


def get_bill(data: dict) -> Bill:
    return Bill.from_dict(data["bill"])


def compute(data: dict) -> SplitResult:
    bill = get_bill(data)
    mode = data.get("mode", "items")
    items = [
        Item(it.label, it.price, tuple(data["assign"][i]) if mode == "items" else ())
        for i, it in enumerate(bill.items)
    ]
    return split_bill(
        views.names_of(data),
        items,
        [Charge(c.label, c.amount) for c in bill.charges],
        data["parties"],
        mode=mode,
        charge_method=data.get("charge_method", "proportional"),
    )


def parse_names(text: str, me_name: str) -> list[dict]:
    out = []
    for raw in re.split(r"[,\n;]+", text):
        name = " ".join(raw.split())[:32]
        if not name:
            continue
        if name.lower() in ME_WORDS:
            out.append({"name": me_name, "me": True})
        else:
            out.append({"name": name, "me": False})
    return out


def add_people(people: list[dict], new: list[dict]) -> list[dict]:
    seen = {p["name"].casefold() for p in people}
    has_me = any(p.get("me") for p in people)
    for p in new:
        if p["me"] and has_me:
            continue
        if p["name"].casefold() in seen:
            if not p["me"]:
                continue
            p["name"] = f"{p['name']} (me)"
        if len(people) >= MAX_PEOPLE:
            break
        people.append(p)
        seen.add(p["name"].casefold())
        has_me = has_me or p["me"]
    return people


def me_name(message: Message | CallbackQuery) -> str:
    user = message.from_user
    return (user.first_name or "Me")[:32] if user else "Me"


# A minus sign only counts when attached to the amount ("Coupon -5", not "Bread - 6.50").
AMOUNT_TOKEN = re.compile(r"-?(?:[A-Za-z]{3}|[^\w\s\-]{1,3})?\s*\d[\d.,]*")


def parse_name_price(text: str, currency: str) -> tuple[str | None, int | None]:
    """'Pizza 18.50' -> ('Pizza', 1850); '18.50' -> (None, 1850); garbage -> (None, None)."""
    text = text.strip().replace("−", "-")
    if AMOUNT_TOKEN.fullmatch(text):
        return None, parse_amount(text, currency)
    m = re.fullmatch(r"(.+?)\s+(" + AMOUNT_TOKEN.pattern + r")", text)
    if not m:
        return text or None, None
    name = m.group(1).strip(" -:–—=")
    return (name[:60] or None), parse_amount(m.group(2).replace(" ", ""), currency)


# ---------- groups ----------

@groups.my_chat_member(F.chat.type.in_({"group", "supergroup"}))
async def added_to_group(event: ChatMemberUpdated, bot: Bot) -> None:
    if event.new_chat_member.status in ("member", "administrator"):
        me = await bot.get_me()
        try:
            await bot.send_message(event.chat.id, GROUP_REPLY.format(username=me.username))
        except TelegramBadRequest:
            pass
        await bot.leave_chat(event.chat.id)


@groups.message(F.chat.type.in_({"group", "supergroup"}))
async def group_message(message: Message, bot: Bot) -> None:
    me = await bot.get_me()
    await message.answer(GROUP_REPLY.format(username=me.username))
    await bot.leave_chat(message.chat.id)


# ---------- commands ----------

@private.message(CommandStart())
@private.message(Command("help"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 I split bills between friends: restaurants, groceries, utilities, rent, trips, shopping.\n\n"
        "Send /split to start (or just send a photo of the bill).\n"
        "/cancel cancels the current split."
    )


@private.message(Command("split"))
async def cmd_split(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current and current != Split.done.state:
        await message.answer(
            "You already have a split in progress. Continue it or start over?",
            reply_markup=views.kb([views.btn("▶️ Continue", "sp:cont"), views.btn("🔄 Start over", "sp:new")]),
        )
        return
    await start_split(message, state)


@private.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    if current and current != Split.done.state:
        await message.answer("❌ Split cancelled. Send /split to start a new one.")
    else:
        await message.answer("Nothing to cancel. Send /split to start a new split.")


@private.callback_query(F.data == "sp:cont")
async def cb_continue(cb: CallbackQuery, state: FSMContext, bot: Bot, reader: BillReader) -> None:
    await cb.answer()
    if not await state.get_state():
        await start_split(cb.message, state)
        return
    await resume(cb.message, state, bot, reader)


@private.callback_query(F.data == "sp:new")
async def cb_restart(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await start_split(cb.message, state)


async def start_split(message: Message, state: FSMContext, pending_file: dict | None = None) -> None:
    await state.set_state(Split.names)
    await state.set_data({"people": [], "parties": [], "pending_file": pending_file})
    prefix = "Got the bill! 👍 First, let's see who's splitting.\n\n" if pending_file else ""
    await message.answer(prefix + views.NAMES_PROMPT)


async def resume(message: Message, state: FSMContext, bot: Bot, reader: BillReader) -> None:
    """Re-send the screen for wherever the user left off."""
    current = await state.get_state()
    data = await state.get_data()
    if current in {s.state for s in NAME_STATES}:
        await state.set_state(Split.names)
        if data["people"]:
            await send_long(message, *views.names_screen(data))
        else:
            await message.answer(views.NAMES_PROMPT)
    elif current == Split.pairing.state:
        await send_long(message, *views.pairing_intro())
    elif current == Split.bill.state:
        if data.get("pending_file"):
            await process_bill(message, state, bot, reader)
        else:
            await message.answer(views.BILL_PROMPT)
    elif current in {s.state for s in TIP_STATES}:
        await state.set_state(Split.tip)
        await send_long(message, *views.tip_question(get_bill(data)))
    elif current in {s.state for s in REVIEW_STATES}:
        await state.set_state(Split.review)
        await send_long(message, *views.review_screen(get_bill(data)))
    elif current == Split.mode.state:
        await send_long(message, *views.mode_screen(get_bill(data)))
    elif current == Split.assign.state:
        bill = get_bill(data)
        screen = views.assign_summary(data, bill) if data.get("screen") == "summary" else views.assign_screen(data, bill)
        await send_long(message, *screen)
    elif current == Split.charges.state:
        await send_long(message, *views.charges_screen(get_bill(data)))
    elif current == Split.payer.state:
        await send_long(message, *views.payer_screen(data))
    else:
        await start_split(message, state)


# ---------- step 1: names ----------

@private.message(Split.names, F.text, ~F.text.startswith("/"))
@private.message(Split.names_add, F.text, ~F.text.startswith("/"))
async def on_names(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    new = parse_names(message.text, me_name(message))
    if not new:
        await message.answer("I didn't catch any names. " + views.NAMES_PROMPT)
        return
    data["people"] = add_people(data["people"], new)
    await state.set_data(data)
    await state.set_state(Split.names)
    await send_long(message, *views.names_screen(data))


@private.message(Split.names_rename, F.text, ~F.text.startswith("/"))
async def on_rename(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    name = " ".join(message.text.split())[:32]
    idx = data.get("edit_idx", -1)
    others = {p["name"].casefold() for i, p in enumerate(data["people"]) if i != idx}
    if not name or name.casefold() in others:
        await message.answer("That name is empty or already taken. Send a different one.")
        return
    if 0 <= idx < len(data["people"]):
        data["people"][idx]["name"] = name
    await state.set_data(data)
    await state.set_state(Split.names)
    await send_long(message, *views.names_screen(data))


@private.callback_query(F.data.startswith("nm:"), Split.names)
@private.callback_query(F.data.startswith("nm:"), Split.names_add)
@private.callback_query(F.data.startswith("nm:"), Split.names_rename)
async def cb_names(cb: CallbackQuery, state: FSMContext, bot: Bot, reader: BillReader) -> None:
    data = await state.get_data()
    _, action, *rest = cb.data.split(":")
    if action == "add":
        await state.set_state(Split.names_add)
        await cb.answer()
        await show(cb, ("Send more names, separated by commas.", views.kb([views.btn("« Back", "nm:back")])))
    elif action == "edit":
        await state.set_state(Split.names)
        await cb.answer()
        await show(cb, views.names_edit_screen(data))
    elif action == "del":
        idx = int(rest[0])
        if 0 <= idx < len(data["people"]):
            data["people"].pop(idx)
            await state.set_data(data)
        await cb.answer()
        await show(cb, views.names_edit_screen(data) if data["people"] else (views.NAMES_PROMPT, None))
    elif action == "ren":
        idx = int(rest[0])
        if not 0 <= idx < len(data["people"]):
            await cb.answer()
            return
        data["edit_idx"] = idx
        await state.set_data(data)
        await state.set_state(Split.names_rename)
        await cb.answer()
        await show(cb, (f"Send the new name for <b>{escape(data['people'][idx]['name'])}</b>.",
                        views.kb([views.btn("« Back", "nm:back")])))
    elif action == "back":
        await state.set_state(Split.names)
        await cb.answer()
        await show(cb, views.names_screen(data) if data["people"] else (views.NAMES_PROMPT, None))
    elif action == "ok":
        if len(data["people"]) < 2:
            await cb.answer("Add at least two people to split with.", show_alert=True)
            return
        await cb.answer()
        if len(data["people"]) < 3:
            await show(cb, (views.names_screen(data)[0], None))
            await go_to_bill(cb.message, state, bot, reader)
            return
        await state.set_state(Split.pairing)
        await show(cb, views.pairing_intro())


# ---------- step 2: couples / parties ----------

@private.callback_query(F.data.startswith("pr:"), Split.pairing)
async def cb_pairing(cb: CallbackQuery, state: FSMContext, bot: Bot, reader: BillReader) -> None:
    data = await state.get_data()
    _, action, *rest = cb.data.split(":")
    sel = set(data.get("pair_sel", []))
    if action in ("skip", "done"):
        await cb.answer()
        summary = "\n".join(f"💑 {escape(views.party_label(data, g))}" for g in data["parties"])
        await show(cb, (summary or "No couples or groups — everyone pays for themselves.", None))
        await go_to_bill(cb.message, state, bot, reader)
        return
    if action == "t":
        sel ^= {int(rest[0])}
    elif action == "link":
        if len(sel) < 2:
            await cb.answer("Pick at least two people to link.", show_alert=True)
            return
        data["parties"].append(sorted(sel))
        sel = set()
    elif action == "u":
        k = int(rest[0])
        if 0 <= k < len(data["parties"]):
            data["parties"].pop(k)
    data["pair_sel"] = sorted(sel)
    await state.set_data(data)
    await cb.answer()
    await show(cb, views.pairing_screen(data))


async def go_to_bill(message: Message, state: FSMContext, bot: Bot, reader: BillReader) -> None:
    await state.set_state(Split.bill)
    data = await state.get_data()
    if data.get("pending_file"):
        await process_bill(message, state, bot, reader)
    else:
        await message.answer(views.BILL_PROMPT)


# ---------- step 3: reading the bill ----------

def file_info(message: Message) -> dict | None:
    if message.photo:
        return {"file_id": message.photo[-1].file_id, "mime": "image/jpeg"}
    doc = message.document
    if doc and doc.mime_type and (doc.mime_type.startswith("image/") or doc.mime_type == "application/pdf"):
        return {"file_id": doc.file_id, "mime": doc.mime_type}
    return None


@private.message(F.photo | F.document)
async def on_file(message: Message, state: FSMContext, bot: Bot, reader: BillReader) -> None:
    info = file_info(message)
    if not info:
        await message.answer("Please send the bill as a photo, image or PDF.")
        return
    current = await state.get_state()
    if current in (None, Split.done.state):
        await start_split(message, state, pending_file=info)
    elif current in {s.state for s in NAME_STATES} | {Split.pairing.state}:
        await state.update_data(pending_file=info)
        await message.answer("Got the bill 👍 I'll read it as soon as we know who's splitting.")
    elif current in {Split.bill.state} | {s.state for s in REVIEW_STATES + TIP_STATES}:
        await state.update_data(pending_file=info)
        await state.set_state(Split.bill)
        await process_bill(message, state, bot, reader)
    else:
        await message.answer(
            "You're already splitting a bill. Send /split to continue it, or /cancel to start over."
        )


async def process_bill(message: Message, state: FSMContext, bot: Bot, reader: BillReader) -> None:
    data = await state.get_data()
    info = data["pending_file"]
    status = await message.answer("🔍 Reading your bill…")
    try:
        buf = await bot.download(info["file_id"])
        bill = await reader.read(BillFile(buf.read(), info["mime"]))
    except UnreadableBill as e:
        await state.update_data(pending_file=None)
        await status.edit_text(f"😕 {escape(str(e))}\n\nCould you send a clearer photo or PDF of the bill?")
        return
    except (ReaderError, TelegramBadRequest) as e:
        log.warning("Bill reading failed: %s", e)
        await status.edit_text("Sorry, something went wrong reading the bill. Please send it again.")
        await state.update_data(pending_file=None)
        return
    data = await state.get_data()
    data["pending_file"] = None
    data["bill"] = bill.to_dict()
    await state.set_data(data)
    if has_tip(bill):
        await state.set_state(Split.review)
        text, markup = views.review_screen(bill)
    else:
        await state.set_state(Split.tip)
        text, markup = views.tip_question(bill)
    if len(text) <= 4000:
        await status.edit_text(text, reply_markup=markup)
    else:
        await status.delete()
        await send_long(message, text, markup)


@private.message(Split.bill)
async def bill_expected(message: Message) -> None:
    await message.answer(views.BILL_PROMPT)


# ---------- step 3b: tip not on the receipt ----------

def parse_tip(text: str, bill: Bill) -> tuple[int, str] | None:
    """'18%' -> (cents, 'Tip (18%)') on the pre-tax subtotal; '12.50' -> (1250, 'Tip')."""
    text = text.strip()
    if text.endswith("%"):
        try:
            pct = Decimal(text[:-1].strip().replace(",", "."))
        except InvalidOperation:
            return None
        if not 0 < pct <= 100:
            return None
        return tip_from_percent(bill, pct), f"Tip ({pct.normalize():f}%)"
    cents = parse_amount(text, bill.currency)
    if cents is None or cents <= 0:
        return None
    return cents, "Tip"


async def finish_tip(target: Message | CallbackQuery, state: FSMContext, bill: Bill) -> None:
    await state.update_data(bill=bill.to_dict())
    await state.set_state(Split.review)
    await show(target, views.review_screen(bill))


@private.callback_query(F.data.startswith("tp:"), Split.tip)
@private.callback_query(F.data.startswith("tp:"), Split.tip_value)
async def cb_tip(cb: CallbackQuery, state: FSMContext) -> None:
    bill = get_bill(await state.get_data())
    action = cb.data.split(":")[1]
    await cb.answer()
    if action == "add":
        await state.set_state(Split.tip_value)
        await show(cb, views.tip_prompt(bill))
    elif action == "back":
        await state.set_state(Split.tip)
        await show(cb, views.tip_question(bill))
    else:  # "yes" (already in the total) or "none"
        await finish_tip(cb, state, bill)


@private.message(Split.tip_value, F.text, ~F.text.startswith("/"))
async def on_tip_value(message: Message, state: FSMContext) -> None:
    bill = get_bill(await state.get_data())
    parsed = parse_tip(message.text, bill)
    if not parsed:
        await message.answer("Sorry, I couldn't read that. Send a percentage like <code>18%</code> "
                             "or an amount like <code>12.50</code>.")
        return
    await finish_tip(message, state, add_tip(bill, *parsed))


# ---------- step 4: review ----------

@private.callback_query(F.data.startswith("rv:"), Split.review)
@private.callback_query(F.data.startswith("rv:"), Split.fix_value)
async def cb_review(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    bill = get_bill(data)
    parts = cb.data.split(":")
    action = parts[1]
    if action != "ok":
        await cb.answer()
    if action == "fix":
        await state.set_state(Split.review)
        await show(cb, views.fix_pick_screen(bill))
    elif action == "back":
        await state.set_state(Split.review)
        await show(cb, views.review_screen(bill))
    elif action in ("add", "e"):
        if action == "add":
            target = "add"
        elif parts[2] == "i":
            target = f"item:{parts[3]}"
        elif parts[2] == "c":
            target = f"charge:{parts[3]}"
        elif parts[2] == "t":
            target = "total"
        else:
            target = "newcharge"
        data["fix_target"] = target
        await state.set_data(data)
        await state.set_state(Split.fix_value)
        await show(cb, views.fix_prompt(bill, target))
    elif action == "photo":
        await state.set_state(Split.bill)
        await show(cb, (views.BILL_PROMPT, None))
    elif action == "ok":
        if not bill.items:
            await cb.answer("The bill needs at least one item.", show_alert=True)
            return
        await cb.answer()
        if check(bill):
            bill = accept_as_is(bill)
        data["bill"] = bill.to_dict()
        await state.set_data(data)
        await state.set_state(Split.mode)
        await show(cb, views.mode_screen(bill))


@private.message(Split.fix_value, F.text, ~F.text.startswith("/"))
async def on_fix_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    bill = get_bill(data)
    kind, _, idx = data.get("fix_target", "add").partition(":")
    text = message.text.strip()
    hint = "Sorry, I couldn't read that. Try e.g. <code>Pizza 18.50</code>."

    if text.lower() in ("delete", "remove") and kind in ("item", "charge"):
        (bill.items if kind == "item" else bill.charges).pop(int(idx))
    elif kind == "total":
        cents = parse_amount(text, bill.currency)
        if cents is None:
            await message.answer(hint)
            return
        bill.total = cents
    else:
        name, cents = parse_name_price(text, bill.currency)
        if cents is None or (kind in ("add", "newcharge") and not name):
            await message.answer(hint)
            return
        if kind == "item":
            item = bill.items[int(idx)]
            item.name, item.price = name or item.name, cents
        elif kind == "charge":
            charge = bill.charges[int(idx)]
            charge.label = name or charge.label
            charge.amount = -abs(cents) if charge.kind == "discount" else cents
        elif kind == "add":
            bill.items.append(BillItem(name, cents))
        else:
            low = name.lower()
            ckind = "discount" if cents < 0 else "tax" if "tax" in low else "tip" if "tip" in low else "fee"
            bill.charges.append(BillCharge(name, ckind, cents))

    data["bill"] = bill.to_dict()
    data.pop("fix_target", None)
    await state.set_data(data)
    await state.set_state(Split.review)
    await send_long(message, *views.review_screen(bill))


# ---------- step 5: split mode & assignment ----------

@private.callback_query(F.data.startswith("md:"), Split.mode)
async def cb_mode(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    bill = get_bill(data)
    await cb.answer()
    if cb.data == "md:even":
        data["mode"] = "even"
        await state.set_data(data)
        await state.set_state(Split.payer)
        await show(cb, views.payer_screen(data))
        return
    data.update(mode="items", assign=[[] for _ in bill.items], cur=0, from_summary=False, screen="item")
    await state.set_data(data)
    await state.set_state(Split.assign)
    await show(cb, views.assign_screen(data, bill))


@private.callback_query(F.data.startswith("as:"), Split.assign)
async def cb_assign(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    bill = get_bill(data)
    _, action, *rest = cb.data.split(":")
    n = len(data["people"])
    cur = data["cur"]
    chosen = set(data["assign"][cur])

    if action == "t":
        chosen ^= {int(rest[0])}
    elif action == "g":
        group = set(data["parties"][int(rest[0])])
        chosen = chosen - group if group <= chosen else chosen | group
    elif action == "all":
        chosen = set() if len(chosen) == n else set(range(n))
    elif action == "prev":
        data["cur"] = max(0, cur - 1)
    elif action == "next":
        if cur < len(bill.items) - 1:
            data["cur"] = cur + 1
        else:
            data["screen"] = "summary"
    elif action == "sum":
        data["screen"] = "summary"
    elif action == "units":
        # One line, several units (e.g. 2 salads): make each its own item to assign separately.
        units = split_units(bill, cur)
        data["bill"] = bill.to_dict()
        data["assign"][cur : cur + 1] = [[] for _ in range(units)]
        data["from_summary"] = False
        chosen = set()
    elif action == "open":
        data.update(cur=int(rest[0]), from_summary=True, screen="item")
    elif action == "done":
        missing = [it.label for it, who in zip(bill.items, data["assign"]) if not who]
        if missing:
            await cb.answer("Every item needs at least one person. Still unassigned: "
                            + views.short(", ".join(missing), 150), show_alert=True)
            return
        await cb.answer()
        if bill.charges:
            await state.set_state(Split.charges)
            await show(cb, views.charges_screen(bill))
        else:
            await state.set_state(Split.payer)
            await show(cb, views.payer_screen(data))
        return

    data["assign"][cur] = sorted(chosen)
    await state.set_data(data)
    await cb.answer()
    if data.get("screen") == "summary":
        data["from_summary"] = True
        await state.set_data(data)
        await show(cb, views.assign_summary(data, bill))
    else:
        await show(cb, views.assign_screen(data, bill))


# ---------- steps 6-8: charges, payer, summary ----------

@private.callback_query(F.data.startswith("ch:"), Split.charges)
async def cb_charges(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    data["charge_method"] = "even" if cb.data == "ch:even" else "proportional"
    await state.set_data(data)
    await state.set_state(Split.payer)
    await cb.answer()
    await show(cb, views.payer_screen(data))


@private.callback_query(F.data.startswith("py:"), Split.payer)
async def cb_payer(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    data["payer"] = int(cb.data.split(":")[1])
    await state.set_data(data)
    await state.set_state(Split.done)
    await cb.answer()
    await show(cb, (f"💳 Paid by <b>{escape(data['people'][data['payer']]['name'])}</b>", None))
    await send_summary(cb.message, data, bot)


async def send_summary(message: Message, data: dict, bot: Bot) -> None:
    bill = get_bill(data)
    result = compute(data)
    me = await bot.get_me()
    await send_long(
        message,
        views.summary_text(data, bill, result),
        views.final_keyboard(data, bill, result, me.username or "SplitEvenlyBot"),
    )


@private.callback_query(F.data.startswith("fn:"), Split.done)
async def cb_final(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    if cb.data == "fn:new":
        await start_split(cb.message, state)
        return
    data = await state.get_data()
    bill = get_bill(data)
    await send_long(cb.message, *views.copy_screen(views.personal_messages(data, bill, compute(data))))


# ---------- fallbacks ----------

@private.callback_query()
async def cb_stale(cb: CallbackQuery) -> None:
    await cb.answer("That button is from an earlier step. Send /split to continue.", show_alert=True)


@private.message()
async def on_other(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current in (None, Split.done.state):
        await message.answer("Send /split to split a bill, or just send me a photo of it. 📸")
    else:
        await message.answer("Please use the buttons above. Send /split to see the current step, or /cancel.")
