#!/usr/bin/env python3
"""
25 Martini Telegram Order Bot (@mtniassistantbot)
Integrated with Notion Databases:
- 01_ORDERS_AND_FINANCIAL_LEDGER (Orders, Landed Math, Fulfillment)
- 03_CARGO_BATCHES_LOG (Air Cargo Shipments, HAWB Tracking)
Includes background HTTP health server on port 7860 for Hugging Face Spaces.
"""

import asyncio
import functools
import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Tuple, List, Dict, Any

from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from notion_manager import notion_mgr

# ---------------------------------------------------------------------------
# Config & Whitelist
# ---------------------------------------------------------------------------
DEFAULT_ADMIN_IDS = [1753678802, 8995849513, 5065850640, 6019728260]
env_admin_ids = os.getenv("ADMIN_IDS")
if env_admin_ids:
    try:
        ADMIN_IDS = [int(i.strip()) for i in env_admin_ids.split(",") if i.strip()]
    except ValueError:
        ADMIN_IDS = DEFAULT_ADMIN_IDS
else:
    ADMIN_IDS = DEFAULT_ADMIN_IDS

BOT_CHANNEL_ID = os.getenv("BOT_CHANNEL_ID", "-1004342732438")
BOT_CHANNEL_TOPIC_ID = int(os.getenv("BOT_CHANNEL_TOPIC_ID", "621"))
ORDER_TRACK_CHANNEL_ID = os.getenv("ORDER_TRACK_CHANNEL_ID", "-1004342732438")
ORDER_TRACK_TOPIC_ID = os.getenv("ORDER_TRACK_TOPIC_ID", "34")
BATCH_CHANNEL_ID = os.getenv("BATCH_CHANNEL_ID", "-1004342732438")
BATCH_TOPIC_ID = os.getenv("BATCH_TOPIC_ID", "36")
PORT = int(os.getenv("PORT", "7860"))

# Dynamic Operational Pricing Configuration
PRICING_CONFIG_FILE = Path(__file__).resolve().parent / "pricing_config.json"
DEFAULT_PRICING_CONFIG = {
    "sgd_to_mmk_rate": 3400.0,
    "cargo_rate_per_kg": 16.0,
    "service_markup_pct": 15.0,
    "yangon_lastmile_mmk": 5000.0
}

def load_pricing_config() -> dict:
    """Load operational pricing configuration from JSON file or fallbacks."""
    config = dict(DEFAULT_PRICING_CONFIG)
    legacy_fx = Path(__file__).resolve().parent / "fx_config.json"
    if legacy_fx.exists():
        try:
            with open(legacy_fx, "r") as f:
                data = json.load(f)
                if "sgd_to_mmk_rate" in data:
                    config["sgd_to_mmk_rate"] = float(data["sgd_to_mmk_rate"])
        except Exception:
            pass

    if PRICING_CONFIG_FILE.exists():
        try:
            with open(PRICING_CONFIG_FILE, "r") as f:
                data = json.load(f)
                config.update(data)
        except Exception:
            pass
    return config

def save_pricing_config_key(key: str, value: float) -> dict:
    """Save an operational pricing parameter persistently."""
    config = load_pricing_config()
    config[key] = value
    config["updated_at"] = str(datetime.now())
    try:
        with open(PRICING_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save pricing config: {e}")
    return config

def get_current_fx_rate() -> float:
    return float(load_pricing_config().get("sgd_to_mmk_rate", 3400.0))

def set_current_fx_rate(rate: float) -> bool:
    save_pricing_config_key("sgd_to_mmk_rate", rate)
    return True

def get_cargo_rate() -> float:
    return float(load_pricing_config().get("cargo_rate_per_kg", 16.0))

def set_cargo_rate(rate: float) -> bool:
    save_pricing_config_key("cargo_rate_per_kg", rate)
    return True

def get_service_markup() -> float:
    return float(load_pricing_config().get("service_markup_pct", 15.0))

def set_service_markup(pct: float) -> bool:
    save_pricing_config_key("service_markup_pct", pct)
    return True

def get_lastmile_fee() -> float:
    return float(load_pricing_config().get("yangon_lastmile_mmk", 5000.0))

def set_lastmile_fee(fee: float) -> bool:
    save_pricing_config_key("yangon_lastmile_mmk", fee)
    return True

# ---------------------------------------------------------------------------
# Conversation State Constants
# ---------------------------------------------------------------------------
NAME, CONTACT, ITEMS, PRICE, DEPOSIT, AREA, CONFIRMATION = range(7)
BATCH_SHIP_DATE, BATCH_HAWB, BATCH_ORDERS = range(7, 10)

ALLOWED_ORDER_STATUSES = [
    "Inquiry",
    "Deposit Received",
    "Bought at Novela",
    "Shipped",
    "Delivered",
    "Cancelled",
]

MENU_REGEX = r"(?i)^(➕\s*New Order|New Order|📋\s*Pending Orders|Pending Orders|📋\s*Active Orders|Active Orders|📊\s*Summary|Summary|✈️\s*Cargo Batches|Cargo Batches|📦\s*Batch Status|Batch Status|Batches)$"

def get_main_menu_keyboard():
    keyboard = [
        ["📋 Pending Orders", "📊 Summary"],
        ["➕ New Order", "📦 Batch Status"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup([["❌ Cancel Order Intake"]], resize_keyboard=True)

# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------
def is_admin(update: Update) -> bool:
    """Check if the user is in the admin whitelist."""
    if not update or not update.effective_user:
        return False
    return update.effective_user.id in ADMIN_IDS


def is_allowed_topic(update: Update) -> bool:
    """Check if the update originates from the allowed Bot Channel topic or an authorized private DM."""
    if not update or not update.effective_chat:
        return False

    # Allow 1-on-1 private chat with an admin
    if update.effective_chat.type == "private":
        return True

    chat_id = str(update.effective_chat.id)
    target_chat = str(BOT_CHANNEL_ID)

    msg = update.effective_message
    thread_id = getattr(msg, "message_thread_id", None) if msg else None

    if chat_id == target_chat and thread_id == BOT_CHANNEL_TOPIC_ID:
        return True

    return False


def require_bot_channel_and_admin(func):
    """Decorator ensuring handler only runs for admins inside the designated Bot Channel topic or private DM."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not is_admin(update):
            user_id = update.effective_user.id if update.effective_user else "Unknown"
            if update.effective_message:
                await update.effective_message.reply_text(
                    f"⛔ **Access Denied**\n\nYour Telegram User ID is `{user_id}`.\nPlease ask Wynn to add your ID to `ADMIN_IDS`.",
                    parse_mode="Markdown"
                )
            return

        if not is_allowed_topic(update):
            if update.effective_message:
                await update.effective_message.reply_text(
                    "⚠️ **Bot Channel Only**\n\n"
                    "To keep typing boxes and keyboards clean across other discussion channels, bot commands and menus are restricted to the **Bot Channel** topic.\n\n"
                    "👉 Please run this command inside the **Bot Channel** topic.",
                    reply_markup=ReplyKeyboardRemove(),
                    parse_mode="Markdown"
                )
            return

        return await func(update, context, *args, **kwargs)
    return wrapper


def clean_order_id(val: Any) -> str:
    """Strip '#' and whitespace from order ID."""
    return str(val).strip().lstrip("#").strip()


def normalize_id(val: Any) -> str:
    """Normalize ID for comparison (removes '#', leading zeroes)."""
    cleaned = str(val).strip().lstrip("#").lstrip("0")
    return cleaned if cleaned else "0"


def clean_float(value: Any) -> float:
    """Safely parse float from arbitrary string or number."""
    if value is None:
        return 0.0
    val_str = str(value).strip().replace(",", "")
    if val_str == "":
        return 0.0
    sanitized = re.sub(r"[^\d.]", "", val_str)
    parts = sanitized.split(".")
    if len(parts) > 2:
        sanitized = parts[0] + "." + "".join(parts[1:])
    try:
        return float(sanitized)
    except ValueError:
        return 0.0


def escape_markdown(text: Any) -> str:
    """Escape markdown special characters."""
    if not text:
        return ""
    text_str = str(text)
    for ch in ("_", "*", "`", "[", "]"):
        text_str = text_str.replace(ch, f"\\{ch}")
    return text_str


def status_badge(status: str) -> str:
    """Return status with representative emoji badge."""
    lower = status.strip().lower()
    if "delivered" in lower or "completed" in lower:
        return f"🟢 {status}"
    elif "shipped" in lower or "transit" in lower or "cargo" in lower:
        return f"✈️ {status}"
    elif "bought" in lower:
        return f"🛍️ {status}"
    elif "deposit" in lower:
        return f"💳 {status}"
    elif "inquiry" in lower:
        return f"💬 {status}"
    elif "cancel" in lower:
        return f"🔴 {status}"
    return f"🟡 {status}"


# ---------------------------------------------------------------------------
# Broadcast Helper
# ---------------------------------------------------------------------------
async def broadcast_to_channel(
    context: ContextTypes.DEFAULT_TYPE, channel_id: str, message_text: str, topic_id: Optional[str] = None
) -> None:
    """Send broadcast message to a Telegram channel or forum topic."""
    if not channel_id or channel_id.strip() == "":
        return
    try:
        kwargs: Dict[str, Any] = {
            "chat_id": int(channel_id),
            "text": message_text,
            "parse_mode": "Markdown",
        }
        if topic_id is not None and str(topic_id).strip() != "":
            kwargs["message_thread_id"] = int(topic_id)
        await context.bot.send_message(**kwargs)
    except Exception as exc:
        logger.error(f"Broadcast Markdown sending failed to {channel_id} (topic={topic_id}): {exc}")
        try:
            await context.bot.send_message(
                chat_id=int(channel_id),
                text=message_text,
                message_thread_id=(int(topic_id) if topic_id is not None and str(topic_id).strip() != "" else None),
            )
        except Exception as exc2:
            logger.error(f"Broadcast plain-text retry failed to {channel_id}: {exc2}")


# ---------------------------------------------------------------------------
# Order Intake Conversation (/neworder)
# ---------------------------------------------------------------------------
async def start_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /neworder conversation."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Access Denied. You are not authorized to use this bot.")
        return ConversationHandler.END

    if not is_allowed_topic(update):
        if update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ /neworder can only be started inside the designated **Bot Channel** topic.",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "🍸 **25 Martini — New Order Intake**\n\n👤 What is the Customer's Name?\n(Type /cancel or tap Cancel below to exit)",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    return NAME


async def receive_customer_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Please enter a valid customer name:")
        return NAME
    context.user_data["customer_name"] = text
    await update.message.reply_text("📞 What is the Customer's Contact / Phone / Viber / Telegram?")
    return CONTACT


async def receive_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Please enter a valid contact:")
        return CONTACT
    context.user_data["contact"] = text
    await update.message.reply_text("🛍️ What are the order items? (e.g. SK-II Treatment Essence 230ml)")
    return ITEMS


async def receive_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Please enter the item details:")
        return ITEMS
    context.user_data["items"] = text
    await update.message.reply_text("💰 What is the total price in MMK? (e.g. 664700, or 195 if SGD):")
    return PRICE


async def receive_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    raw_val = clean_float(text)
    if raw_val <= 0:
        await update.message.reply_text("⚠️ Please enter a valid positive number for price:")
        return PRICE

    # Convert SGD to MMK if small number entered
    current_fx = get_current_fx_rate()
    if raw_val < 2000:
        price_mmk = raw_val * current_fx
        sgd_note = f" ({raw_val:g} SGD @ {current_fx:,.0f} FX)"
    else:
        price_mmk = raw_val
        sgd_note = ""

    context.user_data["price"] = price_mmk
    context.user_data["sgd_note"] = sgd_note

    keyboard = [
        [
            InlineKeyboardButton("✅ 50% Deposit Paid", callback_data="deposit_paid_50"),
            InlineKeyboardButton("⏳ Unpaid (Inquiry)", callback_data="deposit_unpaid"),
        ]
    ]
    await update.message.reply_text(
        f"💳 Price set to **{price_mmk:,.0f} MMK**{sgd_note}.\n"
        f"Did the customer pay the 50% deposit?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return DEPOSIT


async def receive_deposit_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "deposit_paid_50":
        deposit_paid = True
        label = "✅ 50% Deposit Received"
    else:
        deposit_paid = False
        label = "⏳ Pending Deposit (Inquiry)"

    context.user_data["deposit_paid"] = deposit_paid
    context.user_data["deposit_label"] = label

    await query.edit_message_text(f"Deposit status: **{label}**", parse_mode="Markdown")
    await query.message.reply_text("📍 What is the Yangon delivery township / address? (e.g. Kamayut, Yangon)")
    return AREA


async def receive_deposit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    deposit_paid = any(w in text for w in ("yes", "paid", "50", "half"))
    label = "✅ 50% Deposit Received" if deposit_paid else "⏳ Pending Deposit (Inquiry)"

    context.user_data["deposit_paid"] = deposit_paid
    context.user_data["deposit_label"] = label

    await update.message.reply_text(f"Deposit status: **{label}**\n\n📍 What is the Yangon delivery township / address?")
    return AREA


async def receive_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Please enter a valid area / address:")
        return AREA
    context.user_data["area"] = text

    data = context.user_data
    price = data.get("price", 0.0)

    summary_text = (
        "📋 **ORDER CONFIRMATION REVIEW**\n\n"
        f"👤 **Customer:** {escape_markdown(data.get('customer_name', ''))}\n"
        f"📞 **Contact:** {escape_markdown(data.get('contact', ''))}\n"
        f"🛍️ **Items:** {escape_markdown(data.get('items', ''))}\n"
        f"💰 **Total Price:** {price:,.0f} MMK{data.get('sgd_note', '')}\n"
        f"💳 **Deposit Status:** {data.get('deposit_label', '')}\n"
        f"📍 **Delivery Area:** {escape_markdown(data.get('area', ''))}\n\n"
        "Save this order directly to Notion?"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm & Save to Notion", callback_data="confirm_order"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_order_entry"),
        ]
    ]
    await update.message.reply_text(
        summary_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )
    return CONFIRMATION


async def handle_order_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_order_entry":
        await query.edit_message_text("❌ Order intake cancelled.")
        context.user_data.clear()
        return ConversationHandler.END

    if query.data == "confirm_order":
        data = context.user_data
        if not data.get("customer_name"):
            await query.edit_message_text("⚠️ Order session expired. Please start over with /neworder.")
            return ConversationHandler.END

        price = data.get("price", 0.0)
        deposit_paid = data.get("deposit_paid", False)

        def save_to_notion():
            return notion_mgr.create_order(
                name=data.get("customer_name", "N/A"),
                contact=data.get("contact", ""),
                items=data.get("items", "N/A"),
                price=price,
                deposit_paid=deposit_paid,
                area=data.get("area", ""),
                notes="Logged via @mtniassistantbot on Telegram"
            )

        try:
            order_res = await asyncio.to_thread(save_to_notion)
            order_id = order_res.get("order_id", "#25M-???")
            stage = order_res.get("fulfillment_stage", "Inquiry")
            notion_url = order_res.get("url", "")

            success_msg = (
                f"🎉 **Order {order_id} Logged to Notion!**\n\n"
                f"👤 **Customer:** {escape_markdown(data.get('customer_name', ''))}\n"
                f"🛍️ **Product:** {escape_markdown(data.get('items', ''))}\n"
                f"💰 **Price:** {price:,.0f} MMK\n"
                f"🚦 **Stage:** {stage}\n\n"
                f"🔗 [View Card in Notion]({notion_url})"
            )
            await query.edit_message_text(success_msg, parse_mode="Markdown", disable_web_page_preview=True)

            broadcast_text = (
                f"🍸 *New Order Created in Notion*\n\n"
                f"Order ID: *{order_id}*\n"
                f"👤 Customer: {escape_markdown(data.get('customer_name', ''))}\n"
                f"📞 Contact: {escape_markdown(data.get('contact', ''))}\n"
                f"🛍️ Items: {escape_markdown(data.get('items', ''))}\n"
                f"💰 Total: {price:,.0f} MMK\n"
                f"💳 Deposit: {data.get('deposit_label', '')}\n"
                f"📍 Area: {escape_markdown(data.get('area', ''))}\n"
                f"🚦 Stage: {stage}\n"
                f"🔗 [Notion Card]({notion_url})"
            )
            await broadcast_to_channel(context, ORDER_TRACK_CHANNEL_ID, broadcast_text, ORDER_TRACK_TOPIC_ID)

        except Exception as exc:
            logger.error(f"Failed to log order to Notion: {exc}")
            await query.edit_message_text(f"❌ Failed to save order to Notion: {exc}")

        context.user_data.clear()
        return ConversationHandler.END


async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    msg = update.effective_message
    if msg:
        if is_allowed_topic(update):
            await msg.reply_text("❌ Action cancelled. Returning to main menu.", reply_markup=get_main_menu_keyboard())
        else:
            await msg.reply_text("❌ Action cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Order Lookup & Admin Commands
# ---------------------------------------------------------------------------
@require_bot_channel_and_admin
async def get_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lookup order details with action buttons."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Access Denied. You are not authorized to use this bot.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/order [id]`\nExample: `/order 001` or `/order #25M-001`", parse_mode="Markdown")
        return

    query_id = clean_order_id(args[0])
    order = await asyncio.to_thread(notion_mgr.get_order_details, query_id)

    if not order:
        await update.message.reply_text(f"❌ Order #{query_id} not found in Notion.")
        return

    clean_id = clean_order_id(order["order_id"])
    keyboard = [
        [
            InlineKeyboardButton("✏️ Update Stage", callback_data=f"card_status_{clean_id}"),
            InlineKeyboardButton("💳 Toggle Deposit", callback_data=f"card_deposit_{clean_id}"),
        ],
        [
            InlineKeyboardButton("❌ Cancel Order", callback_data=f"card_cancel_{clean_id}"),
            InlineKeyboardButton("🔗 Open in Notion", url=order.get("url", "https://notion.so")),
        ],
    ]

    card_text = (
        f"🍸 **ORDER {order['order_id']}**\n\n"
        f"👤 **Customer:** {escape_markdown(order['customer'])}\n"
        f"📞 **Contact:** {escape_markdown(order['contact'])}\n"
        f"🛍️ **Product:** {escape_markdown(order['product'])}\n"
        f"💰 **Total Price:** {order['price']:,.0f} MMK\n"
        f"💳 **Payment Status:** {order['payment_status']}\n"
        f"🚦 **Fulfillment Stage:** {order['stage']}\n"
        f"📍 **Delivery Address:** {escape_markdown(order['address'])}\n"
        f"📅 **Date:** {order['order_date']}"
    )

    await update.message.reply_text(
        card_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )


@require_bot_channel_and_admin
async def pending_orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all active orders from Notion."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Access Denied. You are not authorized to use this bot.")
        return

    pending = await asyncio.to_thread(notion_mgr.get_pending_orders)
    if not pending:
        await update.message.reply_text("✅ No pending orders in Notion right now — all caught up!")
        return

    lines = [f"🍸 **ACTIVE ORDERS IN NOTION ({len(pending)})**\n"]
    for ord in pending:
        lines.append(f"• **{ord['order_id']}** — {escape_markdown(ord['customer'])}")
        lines.append(f"  {escape_markdown(ord['product'])} | {ord['price']:,.0f} MMK")
        lines.append(f"  Stage: {ord['stage']} | Payment: {ord['payment_status']}")
        lines.append("")

    lines.append("👉 Use `/order [number]` for card actions, or `/updatestatus` to advance stage.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@require_bot_channel_and_admin
async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Aggregate business metrics from Notion."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Access Denied. You are not authorized to use this bot.")
        return

    summary = await asyncio.to_thread(notion_mgr.get_summary_metrics)
    total_orders = summary.get("total_orders", 0)
    active_count = summary.get("active_count", 0)
    total_sales = summary.get("total_sales_mmk", 0.0)
    stage_counts = summary.get("stage_counts", {})

    lines = [
        "📊 **25 MARTINI — BUSINESS METRICS**",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📦 **Total Orders Logged:** {total_orders}",
        f"⚡ **Active Orders in Pipeline:** {active_count}",
        f"💰 **Gross Pipeline Volume:** {total_sales:,.0f} MMK",
        "",
        "🚦 **Pipeline Breakdown:**",
    ]
    for stage, count in stage_counts.items():
        lines.append(f"• {stage}: **{count}**")

    lines.extend([
        "",
        f"🔗 [Open 25 Martini Notion Hub](https://app.notion.com/p/25-Martini-Sales-Hunt-3dbf3e9bb31c81bfb958e8bc4728af09)"
    ])
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


@require_bot_channel_and_admin
async def update_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Advance order stage: /updatestatus [id] [stage]."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Access Denied.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: `/updatestatus [id] [stage]`\nStages: `inquiry`, `deposit`, `bought`, `shipped`, `delivered`, `cancelled`",
            parse_mode="Markdown"
        )
        return

    order_id = clean_order_id(args[0])
    target_stage = " ".join(args[1:])

    res = await asyncio.to_thread(notion_mgr.update_order_stage, order_id, target_stage)
    if not res:
        await update.message.reply_text(f"❌ Order #{order_id} not found in Notion.")
        return

    await update.message.reply_text(
        f"✅ Order **{res['order_id']}** updated in Notion!\nStage: {res['stage']}",
        parse_mode="Markdown"
    )
    broadcast_text = f"📢 *Order Stage Update* — Order *{res['order_id']}* is now *{res['stage']}*"
    await broadcast_to_channel(context, ORDER_TRACK_CHANNEL_ID, broadcast_text, ORDER_TRACK_TOPIC_ID)


@require_bot_channel_and_admin
async def cancel_order_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel order: /cancelorder [id] [reason]."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Access Denied.")
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Usage: `/cancelorder [id] [reason]`", parse_mode="Markdown")
        return

    order_id = clean_order_id(args[0])
    reason = " ".join(args[1:]) if len(args) > 1 else "Cancelled by founder"

    res = await asyncio.to_thread(notion_mgr.cancel_order, order_id, reason)
    if not res:
        await update.message.reply_text(f"❌ Order #{order_id} not found in Notion.")
        return

    await update.message.reply_text(f"🚫 Order **{res['order_id']}** cancelled in Notion.\nReason: {reason}")
    broadcast_text = f"🚫 *Order Cancelled* — Order *{res['order_id']}* | Reason: {escape_markdown(reason)}"
    await broadcast_to_channel(context, ORDER_TRACK_CHANNEL_ID, broadcast_text, ORDER_TRACK_TOPIC_ID)


@require_bot_channel_and_admin
async def editdeposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle deposit: /editdeposit [id] [yes/no]."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Access Denied.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/editdeposit [id] yes` (or `no`)", parse_mode="Markdown")
        return

    order_id = clean_order_id(args[0])
    is_paid = True
    if len(args) > 1 and args[1].lower() in ("no", "0", "unpaid"):
        is_paid = False

    res = await asyncio.to_thread(notion_mgr.update_payment_status, order_id, is_paid)
    if not res:
        await update.message.reply_text(f"❌ Order #{order_id} not found in Notion.")
        return

    await update.message.reply_text(f"💳 Order **{res['order_id']}** deposit updated: **{res['payment_status']}**", parse_mode="Markdown")
    broadcast_text = f"💳 *Deposit Updated* — Order *{res['order_id']}* is now *{res['payment_status']}*"
    await broadcast_to_channel(context, ORDER_TRACK_CHANNEL_ID, broadcast_text, ORDER_TRACK_TOPIC_ID)


# ---------------------------------------------------------------------------
# Cargo Batches Commands (/batches, /newbatch, /batchstatus)
# ---------------------------------------------------------------------------
@require_bot_channel_and_admin
async def batches_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all cargo batches from Notion."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Access Denied.")
        return

    batches = await asyncio.to_thread(notion_mgr.list_batches)
    if not batches:
        await update.message.reply_text("✈️ No cargo batches yet — create one with `/newbatch`.", parse_mode="Markdown")
        return

    lines = [f"✈️ **CARGO BATCHES IN NOTION ({len(batches)})**\n"]
    for b in batches:
        lines.append(f"• **{b['batch_id']}** — {b['status']}")
        lines.append(f"  📅 Date: {b['ship_date']} | 🏷️ HAWB: {b['hawb']}")
        lines.append(f"  📦 Orders: {b['orders']} ({b['total_items']} items)")
        lines.append("")

    lines.append("👉 Use `/batchstatus [id]` to update status.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def start_newbatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start batch intake."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Access Denied.")
        return ConversationHandler.END

    if not is_allowed_topic(update):
        if update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ /newbatch can only be started inside the designated **Bot Channel** topic.",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "✈️ **New Cargo Batch Intake**\n\n📅 What is the Ship Date? (e.g. 2026-10-27 or Oct 27):"
    )
    return BATCH_SHIP_DATE


async def handle_ship_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Please enter a ship date:")
        return BATCH_SHIP_DATE
    context.user_data["ship_date"] = text
    await update.message.reply_text("🏷️ What is the Air Cargo HAWB / Tracking Number? (e.g. RYL-9921-SG)")
    return BATCH_HAWB


async def handle_hawb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Please enter a tracking number:")
        return BATCH_HAWB
    context.user_data["hawb"] = text

    pending = await asyncio.to_thread(notion_mgr.get_pending_orders)
    pending_ids = [p["order_id"] for p in pending]
    if not pending_ids:
        pending_ids_str = "(No active orders right now)"
    else:
        pending_ids_str = ", ".join(pending_ids)

    await update.message.reply_text(
        f"📦 Current active orders in Notion:\n`{pending_ids_str}`\n\n"
        f"Reply with order numbers to include in this batch (comma-separated, e.g. #25M-001, #25M-002):",
        parse_mode="Markdown"
    )
    return BATCH_ORDERS


async def handle_order_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_input = update.message.text.strip()
    order_ids = [oid.strip() for oid in raw_input.split(",") if oid.strip()]
    if not order_ids:
        await update.message.reply_text("Please enter at least one order number:")
        return BATCH_ORDERS

    ship_date = context.user_data.get("ship_date", "")
    hawb = context.user_data.get("hawb", "")
    joined_ids = ", ".join(order_ids)

    def create_batch_record():
        b = notion_mgr.create_batch(
            ship_date=ship_date,
            hawb=hawb,
            order_numbers=joined_ids,
            total_items=len(order_ids),
            status="🛫 Departed SG"
        )
        # Advance included orders to Shipped stage in Notion
        for oid in order_ids:
            try:
                notion_mgr.update_order_stage(oid, "shipped")
            except Exception:
                pass
        return b

    batch = await asyncio.to_thread(create_batch_record)

    await update.message.reply_text(
        f"✈️ **Batch {batch['batch_id']} Created in Notion!**\n\n"
        f"📅 **Date:** {ship_date}\n"
        f"🏷️ **HAWB:** {hawb}\n"
        f"📦 **Orders:** {joined_ids}\n"
        f"🚦 **Status:** {batch['status']}",
        parse_mode="Markdown"
    )

    batch_broadcast = (
        f"✈️ *New Air Cargo Batch Dispatched*\n\n"
        f"Batch: *{batch['batch_id']}*\n"
        f"📅 Ship Date: {ship_date}\n"
        f"🏷️ HAWB: {hawb}\n"
        f"📦 Included Orders: {joined_ids}\n"
        f"🚦 Status: {batch['status']}"
    )
    await broadcast_to_channel(context, BATCH_CHANNEL_ID, batch_broadcast, BATCH_TOPIC_ID)
    context.user_data.clear()
    return ConversationHandler.END


async def batchstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lookup or update batch status."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Access Denied.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/batchstatus [id] [status]`\nExample: `/batchstatus B-001 in transit`", parse_mode="Markdown")
        return

    batch_id = args[0]
    if len(args) > 1:
        new_status = " ".join(args[1:])
        res = await asyncio.to_thread(notion_mgr.update_batch_status, batch_id, new_status)
        if not res:
            await update.message.reply_text(f"❌ Batch {batch_id} not found in Notion.")
            return
        await update.message.reply_text(f"✅ Batch **{res['batch_id']}** status updated: **{res['status']}**", parse_mode="Markdown")
        broadcast_text = f"✈️ *Batch Update* — Batch *{res['batch_id']}* is now *{res['status']}*"
        await broadcast_to_channel(context, BATCH_CHANNEL_ID, broadcast_text, BATCH_TOPIC_ID)
        return

    # Show single batch
    batches = await asyncio.to_thread(notion_mgr.list_batches)
    target = None
    for b in batches:
        if clean_order_id(b["batch_id"]).lower() == clean_order_id(batch_id).lower():
            target = b
            break

    if not target:
        await update.message.reply_text(f"❌ Batch {batch_id} not found in Notion.")
        return

    keyboard = [
        [
            InlineKeyboardButton("✈️ In Transit", callback_data=f"bstatus_{clean_order_id(batch_id)}_In Transit"),
            InlineKeyboardButton("🛬 Arrived YGN", callback_data=f"bstatus_{clean_order_id(batch_id)}_Arrived Myanmar"),
        ],
        [
            InlineKeyboardButton("🚚 Out for Delivery", callback_data=f"bstatus_{clean_order_id(batch_id)}_Out for Delivery"),
            InlineKeyboardButton("✅ Completed", callback_data=f"bstatus_{clean_order_id(batch_id)}_Completed"),
        ]
    ]
    await update.message.reply_text(
        f"✈️ **Batch {target['batch_id']}**\n\n"
        f"📅 Date: {target['ship_date']}\n"
        f"🏷️ HAWB: {target['hawb']}\n"
        f"📦 Orders: {target['orders']}\n"
        f"🚦 Status: {target['status']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# ---------------------------------------------------------------------------
# Inline Card Callback Handlers
# ---------------------------------------------------------------------------
async def handle_card_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("card_status_"):
        order_id = clean_order_id(data.replace("card_status_", ""))
        keyboard = [
            [
                InlineKeyboardButton("🛍️ Bought at Novela", callback_data=f"setstage_{order_id}_bought"),
                InlineKeyboardButton("✈️ In Air Cargo", callback_data=f"setstage_{order_id}_shipped"),
            ],
            [
                InlineKeyboardButton("✅ Delivered & Paid", callback_data=f"setstage_{order_id}_delivered"),
                InlineKeyboardButton("❌ Cancelled", callback_data=f"setstage_{order_id}_cancelled"),
            ]
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("setstage_"):
        parts = data.split("_")
        order_id = parts[1]
        target_stage = parts[2]
        res = await asyncio.to_thread(notion_mgr.update_order_stage, order_id, target_stage)
        if res:
            await query.edit_message_text(f"✅ Order **{res['order_id']}** stage updated: **{res['stage']}**", parse_mode="Markdown")
            broadcast_text = f"📢 *Order Stage Update* — Order *{res['order_id']}* is now *{res['stage']}*"
            await broadcast_to_channel(context, ORDER_TRACK_CHANNEL_ID, broadcast_text, ORDER_TRACK_TOPIC_ID)
        else:
            await query.edit_message_text(f"❌ Failed to update order #{order_id}")
        return

    if data.startswith("card_deposit_"):
        order_id = clean_order_id(data.replace("card_deposit_", ""))
        # Check current details
        ord_details = await asyncio.to_thread(notion_mgr.get_order_details, order_id)
        is_paid = not (ord_details and "Paid" in ord_details.get("payment_status", ""))
        res = await asyncio.to_thread(notion_mgr.update_payment_status, order_id, is_paid)
        if res:
            await query.edit_message_text(f"💳 Order **{res['order_id']}** deposit updated: **{res['payment_status']}**", parse_mode="Markdown")
            broadcast_text = f"💳 *Deposit Updated* — Order *{res['order_id']}* is now *{res['payment_status']}*"
            await broadcast_to_channel(context, ORDER_TRACK_CHANNEL_ID, broadcast_text, ORDER_TRACK_TOPIC_ID)
        else:
            await query.edit_message_text(f"❌ Order #{order_id} not found.")
        return

    if data.startswith("card_cancel_"):
        order_id = clean_order_id(data.replace("card_cancel_", ""))
        res = await asyncio.to_thread(notion_mgr.cancel_order, order_id, "Cancelled via inline card")
        if res:
            await query.edit_message_text(f"🚫 Order **{res['order_id']}** has been cancelled in Notion.")
            broadcast_text = f"🚫 *Order Cancelled* — Order *{res['order_id']}* marked cancelled"
            await broadcast_to_channel(context, ORDER_TRACK_CHANNEL_ID, broadcast_text, ORDER_TRACK_TOPIC_ID)
        else:
            await query.edit_message_text(f"❌ Order #{order_id} not found.")
        return

    if data.startswith("bstatus_"):
        parts = data.split("_", 2)
        batch_id = parts[1]
        new_status = parts[2]
        res = await asyncio.to_thread(notion_mgr.update_batch_status, batch_id, new_status)
        if res:
            await query.edit_message_text(f"✈️ Batch **{res['batch_id']}** status updated: **{res['status']}**", parse_mode="Markdown")
            broadcast_text = f"✈️ *Batch Update* — Batch *{res['batch_id']}* is now *{res['status']}*"
            await broadcast_to_channel(context, BATCH_CHANNEL_ID, broadcast_text, BATCH_TOPIC_ID)
        else:
            await query.edit_message_text(f"❌ Batch #{batch_id} not found.")
        return

    if data.startswith("prompt_set"):
        param = data.replace("prompt_set", "")
        examples = {
            "fx": "`/setfx 3450` (MMK per SGD)",
            "cargo": "`/setcargo 18` (S$ per kg)",
            "markup": "`/setmarkup 20` (percentage %)",
            "lastmile": "`/setlastmile 6000` (MMK dispatch buffer)"
        }
        ex = examples.get(param, "")
        await query.message.reply_text(
            f"👉 **To update this parameter, send the command:**\n\n{ex}",
            parse_mode="Markdown"
        )
        return


# ---------------------------------------------------------------------------
# Menu & Identity Commands
# ---------------------------------------------------------------------------
@require_bot_channel_and_admin
async def start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show main keyboard and welcome message."""
    keyboard = [
        ["📋 Pending Orders", "📊 Summary"],
        ["➕ New Order", "📦 Batch Status"],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    welcome_text = (
        "🍸 **25 Martini — Operations Assistant**\n\n"
        "Welcome! All orders and cargo batches are synced live with our Notion Hub.\n\n"
        "• `/neworder` — Take a customer order (7-step guide)\n"
        "• `/pending` — View active orders in pipeline\n"
        "• `/order [id]` — Look up an order card with action buttons\n"
        "• `/summary` — View business revenue & pipeline metrics\n"
        "• `/batches` — View air cargo shipments & tracking\n"
        "• `/newbatch` — Create new cargo batch\n"
        "• `/fx` — View and adjust live operational pricing\n"
        "• `/setfx [rate]` — Update live SGD/MMK exchange rate\n"
        "• `/setcargo [sgd]` — Update air cargo rate per kg (SGD)\n"
        "• `/setmarkup [pct]` — Update service markup %\n"
        "• `/setlastmile [mmk]` — Update Yangon last-mile buffer (MMK)\n"
        "• `/clean` — Remove keyboard buttons from this chat\n\n"
        "🔗 [Open 25 Martini Notion Hub](https://app.notion.com/p/25-Martini-Sales-Hunt-3dbf3e9bb31c81bfb958e8bc4728af09)"
    )
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown", disable_web_page_preview=True)


def get_pricing_keyboard():
    """Inline keyboard for one-tap guidance on updating operational parameters."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Set FX Rate", callback_data="prompt_setfx"),
            InlineKeyboardButton("✈️ Set Cargo Rate", callback_data="prompt_setcargo"),
        ],
        [
            InlineKeyboardButton("🏷️ Set Markup %", callback_data="prompt_setmarkup"),
            InlineKeyboardButton("🚚 Set Last-Mile", callback_data="prompt_setlastmile"),
        ]
    ])


@require_bot_channel_and_admin
async def fx_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current operational exchange rate and logistics parameters."""
    cfg = load_pricing_config()
    fx = cfg.get("sgd_to_mmk_rate", 3400.0)
    cargo = cfg.get("cargo_rate_per_kg", 16.0)
    markup = cfg.get("service_markup_pct", 15.0)
    lastmile = cfg.get("yangon_lastmile_mmk", 5000.0)

    msg = (
        "💱 **25 Martini — Operational Pricing Parameters**\n\n"
        f"• **Active FX Rate:** 1 SGD = **{fx:,.0f} MMK**\n"
        f"• **Air Cargo Rate:** **S${cargo:,.2f} / kg**\n"
        f"• **Service Fee:** **{markup:g}%** Landed Markup\n"
        f"• **Yangon Last-Mile:** **{lastmile:,.0f} MMK**\n\n"
        "💡 *Tap a button below or use direct commands:*\n"
        "• `/setfx <rate>` (e.g. `/setfx 3450`)\n"
        "• `/setcargo <sgd>` (e.g. `/setcargo 18`)\n"
        "• `/setmarkup <pct>` (e.g. `/setmarkup 20`)\n"
        "• `/setlastmile <mmk>` (e.g. `/setlastmile 6000`)\n\n"
        "All new order calculations in the bot will immediately use these values."
    )
    await update.message.reply_text(msg, reply_markup=get_pricing_keyboard(), parse_mode="Markdown")


@require_bot_channel_and_admin
async def setfx_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update the operational SGD to MMK exchange rate."""
    args = context.args
    if not args:
        current = get_current_fx_rate()
        await update.message.reply_text(
            f"ℹ️ **Current Active FX Rate:** 1 SGD = **{current:,.0f} MMK**\n\n"
            "To set a new rate, provide the MMK amount:\n"
            "👉 `/setfx 3450`",
            parse_mode="Markdown"
        )
        return

    try:
        new_rate = float(args[0].replace(",", "").strip())
        if new_rate < 1000 or new_rate > 10000:
            await update.message.reply_text("⚠️ FX rate must be between 1,000 and 10,000 MMK per SGD.", parse_mode="Markdown")
            return

        old_rate = get_current_fx_rate()
        set_current_fx_rate(new_rate)

        await update.message.reply_text(
            f"✅ **Operational Exchange Rate Updated!**\n\n"
            f"• **Previous Rate:** 1 SGD = {old_rate:,.0f} MMK\n"
            f"• **New Rate:** 1 SGD = **{new_rate:,.0f} MMK**\n\n"
            f"✨ All future order intakes will calculate at this rate.",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Please enter a valid rate, e.g. `/setfx 3450`", parse_mode="Markdown")


@require_bot_channel_and_admin
async def setcargo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update the operational air cargo rate per kg (in SGD)."""
    args = context.args
    if not args:
        current = get_cargo_rate()
        await update.message.reply_text(
            f"ℹ️ **Current Air Cargo Rate:** **S${current:,.2f} / kg**\n\n"
            "To set a new cargo rate (in SGD), send:\n"
            "👉 `/setcargo 18` (or `/setcargo 16.50`)",
            parse_mode="Markdown"
        )
        return

    try:
        new_rate = float(args[0].replace("$", "").replace(",", "").strip())
        if new_rate < 1 or new_rate > 100:
            await update.message.reply_text("⚠️ Cargo rate must be between S$1.00 and S$100.00 per kg.", parse_mode="Markdown")
            return

        old_rate = get_cargo_rate()
        set_cargo_rate(new_rate)

        await update.message.reply_text(
            f"✅ **Air Cargo Rate Updated!**\n\n"
            f"• **Previous Rate:** S${old_rate:,.2f} / kg\n"
            f"• **New Rate:** **S${new_rate:,.2f} / kg**\n\n"
            f"✈️ Air freight calculations for batch orders will use this rate.",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Please enter a valid rate, e.g. `/setcargo 18`", parse_mode="Markdown")


@require_bot_channel_and_admin
async def setmarkup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update the service markup percentage."""
    args = context.args
    if not args:
        current = get_service_markup()
        await update.message.reply_text(
            f"ℹ️ **Current Service Markup:** **{current:g}%**\n\n"
            "To set a new markup percentage, send:\n"
            "👉 `/setmarkup 20` (or `/setmarkup 12.5`)",
            parse_mode="Markdown"
        )
        return

    try:
        new_pct = float(args[0].replace("%", "").strip())
        if new_pct < 0 or new_pct > 100:
            await update.message.reply_text("⚠️ Markup must be between 0% and 100%.", parse_mode="Markdown")
            return

        old_pct = get_service_markup()
        set_service_markup(new_pct)

        await update.message.reply_text(
            f"✅ **Service Markup Updated!**\n\n"
            f"• **Previous Markup:** {old_pct:g}%\n"
            f"• **New Markup:** **{new_pct:g}%**\n\n"
            f"🏷️ Profit margin multiplier updated across pricing calculations.",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Invalid percentage. Please enter a valid number, e.g. `/setmarkup 20`", parse_mode="Markdown")


@require_bot_channel_and_admin
async def setlastmile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update the Yangon local last-mile dispatch buffer in MMK."""
    args = context.args
    if not args:
        current = get_lastmile_fee()
        await update.message.reply_text(
            f"ℹ️ **Current Yangon Last-Mile Buffer:** **{current:,.0f} MMK**\n\n"
            "To set a new last-mile buffer, send:\n"
            "👉 `/setlastmile 6000`",
            parse_mode="Markdown"
        )
        return

    try:
        new_fee = float(args[0].replace(",", "").strip())
        if new_fee < 0 or new_fee > 50000:
            await update.message.reply_text("⚠️ Last-mile fee must be between 0 and 50,000 MMK.", parse_mode="Markdown")
            return

        old_fee = get_lastmile_fee()
        set_lastmile_fee(new_fee)

        await update.message.reply_text(
            f"✅ **Yangon Last-Mile Buffer Updated!**\n\n"
            f"• **Previous Buffer:** {old_fee:,.0f} MMK\n"
            f"• **New Buffer:** **{new_fee:,.0f} MMK**\n\n"
            f"🚚 Local delivery dispatch buffer updated.",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Please enter a valid amount, e.g. `/setlastmile 6000`", parse_mode="Markdown")


async def clean_keyboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Purge stuck custom keyboards from any chat or topic."""
    if not is_admin(update):
        return
    await update.effective_message.reply_text(
        "🧹 Custom keyboard removed from this chat/topic. Your typing box is clear.",
        reply_markup=ReplyKeyboardRemove()
    )


async def get_my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else "Unknown"
    await update.message.reply_text(f"🆔 Your Telegram User ID is: `{user_id}`", parse_mode="Markdown")


async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    if not is_allowed_topic(update):
        if update.effective_message:
            await update.effective_message.reply_text(
                "🧹 Custom keyboard removed. Please use the designated **Bot Channel** topic for bot operations.",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
        return

    raw_text = update.message.text.strip() if update.message and update.message.text else ""
    text = raw_text.lower()
    context.user_data.clear()

    if "new order" in text or "➕" in text:
        return await start_order(update, context)
    elif "pending" in text or "active" in text or ("order" in text and "new" not in text):
        return await pending_orders_command(update, context)
    elif "summary" in text or "📊" in text:
        return await summary_command(update, context)
    elif "batch" in text or "cargo" in text or "📦" in text or "✈️" in text:
        return await batches_list_command(update, context)
    elif "cancel" in text or "❌" in text:
        return await cancel_conv(update, context)


# ---------------------------------------------------------------------------
# Background HTTP Health Server (Hugging Face Spaces keep-alive)
# ---------------------------------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        response = {
            "status": "ok",
            "bot": "mtniassistantbot",
            "system": "25 Martini Operations",
            "database": "Notion (01_ORDERS_AND_FINANCIAL_LEDGER & 03_CARGO_BATCHES_LOG)",
            "timestamp": datetime.now().isoformat()
        }
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Quiet HTTP logs


def run_health_server(port: int = 7860):
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logger.info(f"Health server listening on http://0.0.0.0:{port}")
        server.serve_forever()
    except Exception as e:
        logger.warning(f"Could not bind health server to port {port}: {e}")


# ---------------------------------------------------------------------------
# Main Runner
# ---------------------------------------------------------------------------
async def setup_bot_commands(application):
    """Register native Telegram commands so they appear in UI autocomplete / slash menu."""
    commands = [
        BotCommand("neworder", "Take a customer order (7-step intake)"),
        BotCommand("pending", "View active orders in pipeline"),
        BotCommand("summary", "View business revenue & metrics"),
        BotCommand("batches", "View air cargo shipments & tracking"),
        BotCommand("newbatch", "Create new air cargo batch"),
        BotCommand("order", "Look up order card by ID"),
        BotCommand("fx", "View & adjust live operational pricing"),
        BotCommand("setfx", "Set SGD to MMK exchange rate"),
        BotCommand("setcargo", "Set air cargo rate per kg (SGD)"),
        BotCommand("setmarkup", "Set service markup percentage"),
        BotCommand("setlastmile", "Set Yangon last-mile buffer (MMK)"),
        BotCommand("clean", "Remove stuck custom keyboard"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("✅ Native bot commands registered successfully with Telegram")
    except Exception as e:
        logger.error(f"Failed to set bot commands: {e}")


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or "PLACEHOLDER" in token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not configured in .env")

    # Start HTTP Health Server in daemon thread
    health_thread = threading.Thread(target=run_health_server, args=(PORT,), daemon=True)
    health_thread.start()

    app = ApplicationBuilder().token(token).post_init(setup_bot_commands).build()

    # Order Creation Conversation
    order_conv = ConversationHandler(
        entry_points=[
            CommandHandler("neworder", start_order),
            MessageHandler(filters.Regex("^➕ New Order$"), start_order),
        ],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_customer_name)],
            CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_contact)],
            ITEMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_items)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price)],
            DEPOSIT: [
                CallbackQueryHandler(receive_deposit_inline, pattern="^deposit_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_deposit_text),
            ],
            AREA: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_area)],
            CONFIRMATION: [
                CallbackQueryHandler(handle_order_confirmation, pattern="^(confirm_order|cancel_order_entry)$")
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conv),
            MessageHandler(filters.Regex(r"(?i)^(❌ Cancel|❌ Cancel Order Intake|/cancel)$"), cancel_conv),
            MessageHandler(filters.Regex(MENU_REGEX), handle_menu_text),
        ],
        allow_reentry=True,
        per_message=False,
    )

    # Batch Creation Conversation
    batch_conv = ConversationHandler(
        entry_points=[CommandHandler("newbatch", start_newbatch)],
        states={
            BATCH_SHIP_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ship_date)],
            BATCH_HAWB: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_hawb)],
            BATCH_ORDERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_order_selection)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conv),
            MessageHandler(filters.Regex(r"(?i)^(❌ Cancel|❌ Cancel Order Intake|/cancel)$"), cancel_conv),
            MessageHandler(filters.Regex(MENU_REGEX), handle_menu_text),
        ],
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(order_conv)
    app.add_handler(batch_conv)

    # Command Handlers with aliases
    app.add_handler(CommandHandler(["start", "menu"], start_menu))
    app.add_handler(CommandHandler("id", get_my_id))
    app.add_handler(CommandHandler("order", get_order))
    app.add_handler(CommandHandler(["pending", "orders", "active"], pending_orders_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler(["batches", "batchstatus"], batches_list_command))
    app.add_handler(CommandHandler("updatestatus", update_status_command))
    app.add_handler(CommandHandler("cancelorder", cancel_order_command))
    app.add_handler(CommandHandler("editdeposit", editdeposit_command))
    app.add_handler(CommandHandler(["fx", "pricing", "rate", "rates"], fx_command))
    app.add_handler(CommandHandler("setfx", setfx_command))
    app.add_handler(CommandHandler(["setcargo", "cargo"], setcargo_command))
    app.add_handler(CommandHandler(["setmarkup", "markup", "margin"], setmarkup_command))
    app.add_handler(CommandHandler(["setlastmile", "lastmile"], setlastmile_command))
    app.add_handler(CommandHandler(["clean", "cleankeyboard", "clear"], clean_keyboard_command))

    # Menu text buttons matching MENU_REGEX
    app.add_handler(MessageHandler(filters.Regex(MENU_REGEX), handle_menu_text))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(handle_card_callbacks, pattern="^(card_|setstage_|bstatus_|prompt_set)"))

    logger.info("🍸 25 Martini Telegram Bot (@mtniassistantbot) starting polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
