# SplitEvenlyBot

Telegram bot ([@SplitEvenlyBot](https://t.me/SplitEvenlyBot)) that splits any bill between friends,
in private chat. Send a photo or PDF of the bill, say who had what, get everyone's share.

- **aiogram 3** with FSM state stored in **SQLite** (a split in progress survives restarts)
- **Claude** (vision) only *reads* the bill — `splitbot/reader.py`
- All money math is in `splitbot/splitter.py`: integer cents, deterministic, tested

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
```

Fill in `TELEGRAM_BOT_TOKEN` and `ANTHROPIC_API_KEY` in `.env`, then:

```bash
python bot.py
```

Tests:

```bash
pytest
```

## Layout

| File | What it does |
|---|---|
| `bot.py` | Entry point: polling locally, webhook if `WEBHOOK_URL` is set |
| `splitbot/handlers.py` | Conversation flow (names → couples → bill → review → assign → charges → payer → summary) |
| `splitbot/views.py` | Message texts and inline keyboards |
| `splitbot/reader.py` | Claude bill reading (structured JSON output, never invents items) |
| `splitbot/bill.py` | Bill model and "does it add up?" checks |
| `splitbot/splitter.py` | Split math: per person first, then parties; fair rounding |
| `splitbot/money.py` | Parsing/formatting amounts in integer cents |
| `splitbot/storage.py` | SQLite FSM storage |

## How the math works

- Each item is split evenly between the people who had it.
- Tax/tip/fees and discounts are split either **proportionally** to each person's items
  or **evenly** per individual (parties count as their members, not as one).
- "Split everything evenly" divides the whole total per individual.
- Shares start as exact fractions; whole cents are handed out by largest remainder, with ties
  going to whoever has received the fewest extra cents so far. Totals always equal the bill exactly.
- Party totals are the sum of their members' amounts.
- If the bill doesn't add up and the user confirms anyway, the gap becomes an
  "Unexplained difference" charge so shares still sum to the printed total.

## Deploying on Railway (later)

Set the same variables plus `WEBHOOK_URL` (your Railway public URL), optionally `WEBHOOK_SECRET`,
and mount a volume for `DB_PATH` so state survives redeploys. Start command: `python bot.py`.
