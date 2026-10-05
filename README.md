# BigBasket Telegram Bot — Sequential Railway Demo

A Telegram interface around the supplied `BigBasketClient` with a step-by-step conversation flow.

## Sequential flow

```text
/login
  ↓
/search
  ↓ (bot asks what to search; user replies with text)
/addresses
  ↓ (user selects an address button)
/cart
  ↓
/checkout
  ↓
/now  OR  /schedule
  ↓
/upi OR /cod
```

Commands intentionally do **not** take arguments. For example, use `/search` and then reply `milk`.

## What was added

- `/search` prompts for the search term instead of `/search milk`.
- Address selection uses buttons.
- `/cart` is required before `/checkout`.
- `/checkout` uses the selected address and creates the existing BigBasket checkout/potential-order state.
- `/now` retrieves delivery slots and attempts to assign the first usable slot.
- `/schedule` asks for a date and time, then attempts to assign a matching slot returned by BigBasket.
- `/upi` creates a standard UPI QR for the payable checkout amount using the Railway `UPI_ID` variable.
- `/cod` is wired to a `place_order()` method, but the supplied client has no verified final-order/payment endpoint, so it safely refuses to claim an order was placed until that endpoint is implemented.

## Railway variables

Required:

```text
BOT_TOKEN=your_telegram_bot_token
```

For the demo UPI QR:

```text
UPI_ID=your_demo_upi_id@bank
UPI_NAME=BigBasket Demo
```

**Important:** the UPI QR pays the configured `UPI_ID`. It does not by itself confirm a BigBasket payment or finalize a BigBasket order. A verified BigBasket payment/order endpoint is still required for real order placement.

## Railway

1. Upload these files to the GitHub repository root.
2. Deploy that repository in Railway.
3. Add the variables above in Railway → Variables.
4. Railway starts the bot with `python bot.py`.

## Notes

- Each Telegram user gets a separate in-memory `BigBasketClient` instance.
- Sessions disappear when Railway restarts/redeploys, so users can `/login` again.
- The client calls BigBasket endpoints directly; those endpoints can change or rate-limit automated traffic.
- Do not commit real tokens, OTPs, cookies, or credentials.
