import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bigbasket_client import BigBasketClient

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bigbasket_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")

PHONE, OTP = range(2)


@dataclass
class UserSession:
    client: BigBasketClient = field(default_factory=lambda: BigBasketClient(silent=True))
    phone: Optional[str] = None
    pending_ref_id: Optional[str] = None
    products: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    addresses: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    selected_address_id: Optional[str] = None
    input_mode: Optional[str] = None
    schedule_date: Optional[str] = None
    schedule_time: Optional[str] = None
    delivery_mode: Optional[str] = None
    payment_method: Optional[str] = None
    stage: str = "login"

    @property
    def logged_in(self) -> bool:
        return bool(self.client.bb_token)


SESSIONS: Dict[int, UserSession] = {}


def session_for(update: Update) -> UserSession:
    user_id = update.effective_user.id
    if user_id not in SESSIONS:
        SESSIONS[user_id] = UserSession()
    return SESSIONS[user_id]


def clean_phone(value: str) -> Optional[str]:
    digits = re.sub(r"\D", "", value)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) != 10 or digits[0] not in "6789":
        return None
    return digits


def money(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"₹{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def short_text(value: Any, limit: int = 70) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def address_label(address: Dict[str, Any]) -> str:
    parts = []
    for key in ("label", "address_type", "name", "first_name"):
        if address.get(key):
            parts.append(str(address[key]))
            break
    for key in ("address", "address_line", "line1", "line_1", "house_no", "locality", "area", "city", "pincode"):
        value = address.get(key)
        if value:
            parts.append(str(value))
    return short_text(", ".join(parts) or str(address), 180)


def require_login(update: Update) -> Optional[UserSession]:
    s = session_for(update)
    if not s.logged_in:
        return None
    return s


def stage_number(stage: str) -> int:
    return {
        "login": 0,
        "search": 1,
        "address": 2,
        "cart": 3,
        "checkout": 4,
        "delivery": 5,
        "payment": 6,
        "done": 7,
    }.get(stage, 0)


def can_do(s: UserSession, required: str) -> bool:
    return stage_number(s.stage) >= stage_number(required)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 *BigBasket Telegram Bot*\n\n"
        "This bot uses a step-by-step flow. Commands do not take arguments.\n\n"
        "1. /login\n"
        "2. /search → then send what you want\n"
        "3. /addresses → select an address\n"
        "4. /cart\n"
        "5. /checkout\n"
        "6. /now or /schedule\n"
        "7. /upi or /cod\n\n"
        "Use /help for details.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Step-by-step commands*\n\n"
        "🔐 /login — asks for phone, then OTP\n"
        "🔎 /search — bot asks what to search for\n"
        "📍 /addresses — choose delivery address with a button\n"
        "🛒 /cart — refresh/show checkout cart status\n"
        "🧾 /checkout — creates the checkout preview\n"
        "⚡ /now — choose the first available delivery slot\n"
        "📅 /schedule — bot asks for date and time\n"
        "💳 /upi — uses BigBasket's returned UPI payment data to generate a QR\n"
        "💵 /cod — attempts final COD placement only if a verified order endpoint exists\n"
        "❌ /cancel — cancel the current text prompt\n"
        "🚪 /logout — clear your in-memory session\n\n"
        "No command needs text after it. For example, use /search, then reply with `milk`.",
        parse_mode="Markdown",
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = session_for(update)
    state = "logged in" if s.logged_in else "not logged in"
    await update.message.reply_text(f"✅ Bot is online. Your BigBasket session is {state}. Current step: {s.stage}.")


async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("📱 Send your 10-digit BigBasket mobile number.")
    return PHONE


async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = clean_phone(update.message.text or "")
    if not phone:
        await update.message.reply_text("❌ Please send a valid 10-digit Indian mobile number.")
        return PHONE

    s = session_for(update)
    await update.message.reply_text("⏳ Preparing session and requesting OTP…")
    try:
        ok = await asyncio.to_thread(_prepare_and_request_otp, s, phone)
    except Exception:
        logger.exception("OTP request failed")
        ok = False

    if not ok:
        reason = s.client.last_otp_error or "BigBasket did not accept the OTP request."
        await update.message.reply_text(f"❌ OTP request failed.\n{short_text(reason, 300)}")
        return ConversationHandler.END

    s.phone = phone
    s.pending_ref_id = s.client.ref_id
    await update.message.reply_text("✅ OTP sent. Send the OTP here to complete login.\n\nUse /cancel to stop.")
    return OTP


def _prepare_and_request_otp(s: UserSession, phone: str) -> bool:
    s.client.register_device()
    s.client.load_ui_data()
    s.client.update_device_info()
    return s.client.request_otp(phone)


async def login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    otp = re.sub(r"\D", "", update.message.text or "")
    if not (4 <= len(otp) <= 8):
        await update.message.reply_text("❌ Send the OTP digits you received.")
        return OTP

    s = session_for(update)
    if not s.phone or not s.client.ref_id:
        await update.message.reply_text("Your login session expired. Please use /login again.")
        return ConversationHandler.END

    await update.message.reply_text("⏳ Verifying OTP…")
    try:
        ok = await asyncio.to_thread(s.client.verify_otp, s.phone, otp)
    except Exception:
        logger.exception("OTP verification failed")
        ok = False

    if not ok:
        await update.message.reply_text("❌ OTP verification failed. Please use /login again.")
        return ConversationHandler.END

    await asyncio.to_thread(s.client.complete_post_login_flow)
    s.stage = "search"
    s.input_mode = None
    await update.message.reply_text(
        "✅ *Login successful!*\n\n"
        "Next step: type /search. I will ask you what product to search for.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    s = session_for(update)
    s.input_mode = None
    await update.message.reply_text("🛑 Current prompt cancelled. You can continue with the current step.")
    return ConversationHandler.END


async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    SESSIONS.pop(update.effective_user.id, None)
    await update.message.reply_text("✅ Your bot session has been cleared from memory.")


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = require_login(update)
    if not s:
        await update.message.reply_text("🔐 Please /login first.")
        return
    s.input_mode = "search"
    await update.message.reply_text("🔎 What product do you want to search for?\n\nExample: `milk`")


async def do_search(update: Update, s: UserSession, term: str):
    await update.message.reply_text(f"🔎 Searching for *{short_text(term)}*…", parse_mode="Markdown")
    try:
        ok = await asyncio.to_thread(s.client.search_product, term)
    except Exception:
        logger.exception("Product search failed")
        ok = False
    if not ok or not s.client.product_list:
        await update.message.reply_text("❌ No products found. Type /search to try again.")
        s.input_mode = None
        return

    s.products.clear()
    lines = [f"🔎 *Results for:* {short_text(term)}", ""]
    keyboard = []
    for product in s.client.product_list[:10]:
        data = s.client._extract_product_data(product)
        if not data or not data.get("id"):
            continue
        pid = str(data["id"])
        s.products[pid] = data
        name = short_text(data.get("desc") or data.get("brand") or pid, 48)
        price = money(data.get("sp"))
        pack = data.get("pack_desc") or data.get("w") or ""
        lines.append(f"*{pid}* — {name}\n{price}  {short_text(pack, 25)}")
        keyboard.append([InlineKeyboardButton(f"➕ Add {pid}", callback_data=f"add:{pid}")])

    if not s.products:
        await update.message.reply_text("Products were returned, but no usable product IDs were found.")
        s.input_mode = None
        return

    s.stage = "search"
    s.input_mode = None
    await update.message.reply_text(
        "\n".join(lines) + "\n\nTap a product to add it. When you are finished adding items, type /addresses.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def product_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    s = session_for(update)
    if not s.logged_in:
        await query.message.reply_text("🔐 Please /login first.")
        return
    pid = query.data.split(":", 1)[1]
    product = s.products.get(pid)
    if not product:
        await query.message.reply_text("That search result expired. Type /search and search again.")
        return
    fc_id = product.get("fc_id")
    if not fc_id:
        await query.message.reply_text("❌ This product has no usable fulfilment-center ID.")
        return
    ok = await asyncio.to_thread(s.client.add_to_cart, pid, str(fc_id), 1)
    await query.message.reply_text("✅ Added to cart. Add another item or type /addresses when finished." if ok else "❌ BigBasket rejected the add-to-cart request.")


async def addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = require_login(update)
    if not s:
        await update.message.reply_text("🔐 Please /login first.")
        return
    if not can_do(s, "search"):
        await update.message.reply_text("Please complete /login first.")
        return
    await update.message.reply_text("📍 Loading your saved addresses…")
    data = await asyncio.to_thread(s.client.get_address_list)
    if not data:
        await update.message.reply_text("❌ No saved addresses were returned.")
        return

    s.addresses.clear()
    lines = ["📍 *Choose your delivery address:*", ""]
    keyboard = []
    for address in data[:10]:
        aid = address.get("id") or address.get("address_id") or address.get("member_address_id")
        if aid is None:
            continue
        aid = str(aid)
        s.addresses[aid] = address
        lines.append(f"*{aid}* — {address_label(address)}")
        keyboard.append([InlineKeyboardButton(f"Use {aid}", callback_data=f"addr:{aid}")])

    if not s.addresses:
        await update.message.reply_text("Addresses were returned, but no address ID was found.")
        return
    s.stage = "address"
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def address_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    s = session_for(update)
    aid = query.data.split(":", 1)[1]
    try:
        ok = await asyncio.to_thread(s.client.set_current_delivery_address, int(aid))
    except (TypeError, ValueError):
        ok = False
    if not ok:
        await query.message.reply_text("❌ Could not select that address.")
        return
    s.selected_address_id = aid
    s.stage = "address"
    await query.message.reply_text("✅ Delivery address selected. Next step: type /cart")


async def cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = require_login(update)
    if not s:
        await update.message.reply_text("🔐 Please /login first.")
        return
    if not s.selected_address_id:
        await update.message.reply_text("📍 First type /addresses and select a delivery address.")
        return
    await update.message.reply_text("📦 Refreshing cart…")
    ok = await asyncio.to_thread(s.client.get_cart_summary)
    if not ok:
        await update.message.reply_text("❌ Could not read the cart.")
        return
    s.stage = "cart"
    await update.message.reply_text("✅ Cart is ready. Next step: type /checkout")


async def checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = require_login(update)
    if not s:
        await update.message.reply_text("🔐 Please /login first.")
        return
    if not s.selected_address_id:
        await update.message.reply_text("📍 Please type /addresses and select an address first.")
        return
    if not can_do(s, "cart"):
        await update.message.reply_text("🛒 Please type /cart first.")
        return

    await update.message.reply_text("🧾 Creating checkout preview…")
    ok = await asyncio.to_thread(s.client.checkout, s.selected_address_id)
    if not ok:
        await update.message.reply_text("❌ Checkout failed. Check cart, address and delivery availability.")
        return

    s.stage = "checkout"
    total = s.client.get_checkout_total()
    total_text = money(total) if total is not None else "not available from the checkout response"
    await update.message.reply_text(
        "✅ *Checkout created.*\n\n"
        f"Potential order ID: `{s.client.po_id}`\n"
        f"Payable total: *{total_text}*\n\n"
        "Now choose delivery: type /now or /schedule.",
        parse_mode="Markdown",
    )


async def get_shipment_and_assign_now(update: Update, s: UserSession):
    await update.message.reply_text("⚡ Looking for the first available delivery slot…")
    shipment = await asyncio.to_thread(s.client.get_delivery_options)
    if not shipment:
        await update.message.reply_text("❌ BigBasket did not return delivery slots.")
        return
    ok = await asyncio.to_thread(s.client.assign_first_available_slot, shipment)
    if not ok:
        await update.message.reply_text("❌ I could not identify a usable delivery slot in BigBasket's response.")
        return
    s.delivery_mode = "now"
    s.stage = "delivery"
    await update.message.reply_text("✅ Earliest available slot selected. Next step: type /upi or /cod")


async def now_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = require_login(update)
    if not s:
        await update.message.reply_text("🔐 Please /login first.")
        return
    if not s.client.po_id or not can_do(s, "checkout"):
        await update.message.reply_text("🧾 Please complete /checkout first.")
        return
    await get_shipment_and_assign_now(update, s)


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = require_login(update)
    if not s:
        await update.message.reply_text("🔐 Please /login first.")
        return
    if not s.client.po_id or not can_do(s, "checkout"):
        await update.message.reply_text("🧾 Please complete /checkout first.")
        return
    s.input_mode = "schedule_date"
    await update.message.reply_text("📅 Send the delivery date in `YYYY-MM-DD` format.", parse_mode="Markdown")


def _extract_slots(data: Any):
    found = []
    def walk(obj):
        if isinstance(obj, dict):
            if all(k in obj for k in ("slot_date", "slot_definition_id", "template_slot_id")):
                found.append(obj)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
    walk(data)
    return found


async def do_schedule_date(update: Update, s: UserSession, value: str):
    try:
        parsed = date.fromisoformat(value.strip())
        if parsed < date.today():
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid date. Send a future date as `YYYY-MM-DD`.", parse_mode="Markdown")
        return
    s.schedule_date = value.strip()
    s.input_mode = "schedule_time"
    await update.message.reply_text("⏰ Send the preferred time, for example `18:00` or `6 PM`.", parse_mode="Markdown")


async def do_schedule_time(update: Update, s: UserSession, value: str):
    s.schedule_time = value.strip()
    await update.message.reply_text("🔎 Checking BigBasket's available slots for that date/time…")
    shipment = await asyncio.to_thread(s.client.get_delivery_options)
    if not shipment:
        s.input_mode = None
        await update.message.reply_text("❌ Could not retrieve delivery slots. Type /schedule to try again.")
        return

    candidates = [x for x in _extract_slots(shipment) if str(x.get("slot_date")) == s.schedule_date]
    if not candidates:
        s.input_mode = None
        await update.message.reply_text("❌ No slot was returned for that date. Type /schedule to choose another date.")
        return

    # Prefer a slot whose human-readable label contains the requested time.
    requested = s.schedule_time.lower()
    chosen = next((x for x in candidates if requested in str(x).lower()), candidates[0])
    shipment_group_id = chosen.get("shipment_group_id")
    shipment_id = chosen.get("shipment_id")
    if shipment_group_id is None or shipment_id is None:
        # Try common parent keys by walking the full response again.
        shipment_group_id = chosen.get("group_id") or chosen.get("shipment_group")
        shipment_id = chosen.get("id") or chosen.get("shipment")

    try:
        ok = await asyncio.to_thread(
            s.client.assign_slot,
            s.client.po_id,
            int(shipment_group_id),
            int(shipment_id),
            str(chosen.get("slot_date")),
            int(chosen.get("slot_definition_id")),
            int(chosen.get("template_slot_id")),
        )
    except (TypeError, ValueError):
        ok = False

    s.input_mode = None
    if not ok:
        await update.message.reply_text("❌ The selected schedule could not be assigned. Type /schedule to try another slot.")
        return
    s.delivery_mode = "schedule"
    s.stage = "delivery"
    await update.message.reply_text(
        f"✅ Scheduled delivery slot selected for {s.schedule_date} around {s.schedule_time}.\n\nNext step: type /upi or /cod"
    )


async def payment_upi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = require_login(update)
    if not s:
        await update.message.reply_text("🔐 Please /login first.")
        return
    if not can_do(s, "delivery"):
        await update.message.reply_text("📅 Please choose /now or /schedule first.")
        return

    # IMPORTANT: never manufacture a UPI VPA here. The QR must contain the
    # actual payment instruction returned by BigBasket/their payment gateway.
    await update.message.reply_text("💳 Checking BigBasket's payment data for a UPI QR…")
    try:
        uri = s.client.extract_upi_payment_uri()
    except Exception:
        logger.exception("UPI data extraction failed")
        uri = None

    if not uri:
        await update.message.reply_text(
            "❌ BigBasket's current checkout response did not expose a UPI QR/UPI URI to this API client.\n\n"
            "I will not generate a fake or redirected QR. The next implementation step is to capture the actual UPI payment response from BigBasket's payment page/gateway and encode that exact payload."
        )
        return

    try:
        import qrcode
        import io
        qr = qrcode.make(uri)
        bio = io.BytesIO()
        bio.name = "bigbasket_upi_qr.png"
        qr.save(bio, format="PNG")
        bio.seek(0)
        s.payment_method = "upi"
        total = s.client.get_checkout_total()
        await update.message.reply_photo(
            photo=bio,
            caption=(
                f"💳 BigBasket UPI QR generated"
                + (f" for {money(total)}" if total is not None else "")
                + ".\n\nThis QR is created only from the UPI payment data returned by BigBasket."
            ),
        )
    except ImportError:
        await update.message.reply_text("❌ QR support is missing. Install the `qrcode[pil]` dependency and redeploy.")


async def payment_cod(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = require_login(update)
    if not s:
        await update.message.reply_text("🔐 Please /login first.")
        return
    if not can_do(s, "delivery"):
        await update.message.reply_text("📅 Please choose /now or /schedule first.")
        return
    s.payment_method = "cod"
    try:
        await asyncio.to_thread(s.client.place_order, "cod")
    except NotImplementedError as exc:
        await update.message.reply_text(
            "❌ COD cannot be finalized yet.\n\n"
            f"{exc}\n\n"
            "The existing client has checkout and slot assignment, but it does not contain the verified final-order endpoint/payload needed to safely place the order."
        )
        return
    except Exception:
        logger.exception("COD placement failed")
        await update.message.reply_text("❌ COD placement failed.")
        return
    s.stage = "done"
    await update.message.reply_text("✅ COD order placed.")


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = session_for(update)
    if not s.logged_in or not s.input_mode:
        return
    value = (update.message.text or "").strip()
    mode = s.input_mode
    if mode == "search":
        await do_search(update, s, value)
    elif mode == "schedule_date":
        await do_schedule_date(update, s, value)
    elif mode == "schedule_time":
        await do_schedule_time(update, s, value)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled Telegram error", exc_info=context.error)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set")

    app = Application.builder().token(BOT_TOKEN).build()

    login_conversation = ConversationHandler(
        entry_points=[CommandHandler("login", login_start)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_phone)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_otp)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(login_conversation)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("logout", logout))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("addresses", addresses))
    app.add_handler(CommandHandler("cart", cart))
    app.add_handler(CommandHandler("checkout", checkout))
    app.add_handler(CommandHandler("now", now_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("upi", payment_upi))
    app.add_handler(CommandHandler("cod", payment_cod))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(product_button, pattern=r"^add:"))
    app.add_handler(CallbackQueryHandler(address_button, pattern=r"^addr:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)

    logger.info("BigBasket Telegram bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
