"""
bot.py
------
Telegram bot entry-point.
All navigation uses edit_message_text (inline keyboard, no new messages).
"""

from __future__ import annotations

import asyncio
import html
import re
from typing import Any

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

import config
import server_manager as sm
from utils import get_vps_ip, init_db, logger, register_user, status_label

# ── Conversation states ───────────────────────────────────────────────────────
CHOOSE_TYPE, ENTER_NAME = range(2)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _main_menu_content() -> tuple[str, InlineKeyboardMarkup]:
    servers = sm.list_servers()
    running = sum(1 for s in servers if s["status"] == "running")
    text = (
        "<b>Minecraft Server Manager</b>\n\n"
        f"Servers: {len(servers)}\n"
        f"Running: {running}"
    )
    keyboard = [
        [InlineKeyboardButton("Servers", callback_data="menu_servers")],
        [InlineKeyboardButton("Create Server", callback_data="menu_create")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


def _servers_menu_content() -> tuple[str, InlineKeyboardMarkup]:
    servers = sm.list_servers()
    if servers:
        lines = "\n".join(
            f"{s['name']} — {status_label(s['status'])}" for s in servers
        )
        text = f"<b>Servers</b>\n\n{lines}"
    else:
        text = "<b>Servers</b>\n\n(no servers yet)"

    keyboard = [
        [InlineKeyboardButton(s["name"], callback_data=f"server_{s['name']}")]
        for s in servers
    ]
    keyboard.append([InlineKeyboardButton("Back", callback_data="menu_main")])
    return text, InlineKeyboardMarkup(keyboard)


def _server_menu_content(name: str) -> tuple[str, InlineKeyboardMarkup]:
    srv = sm.get_server(name)
    if not srv:
        return f"<b>Server '{html.escape(name)}' not found.</b>", InlineKeyboardMarkup(
            [[InlineKeyboardButton("Back", callback_data="menu_servers")]]
        )

    ip = get_vps_ip()
    text = (
        f"<b>Server: {html.escape(name)}</b>\n\n"
        f"Status: {status_label(srv['status'])}\n"
        f"Address: {ip}:{srv['port']}\n"
        f"Type: {srv.get('type', '?')}"
    )
    keyboard = [
        [
            InlineKeyboardButton("▶️ Start", callback_data=f"start_{name}"),
            InlineKeyboardButton("⏹️ Stop", callback_data=f"stop_{name}"),
        ],
        [InlineKeyboardButton("⚙️ Restart", callback_data=f"restart_{name}")],
        [InlineKeyboardButton("📄 Logs", callback_data=f"logs_{name}")],
        [InlineKeyboardButton("🗑️ Delete", callback_data=f"confirm_delete_{name}")],
        [InlineKeyboardButton("Back", callback_data="menu_servers")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


async def _edit(update: Update, text: str, markup: InlineKeyboardMarkup) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    register_user(user.id, user.username)
    text, markup = _main_menu_content()
    await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


# ── CallbackQuery router ──────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data: str = query.data

    # Main menu
    if data == "menu_main":
        text, markup = _main_menu_content()
        await _edit(update, text, markup)

    # Servers list
    elif data == "menu_servers":
        text, markup = _servers_menu_content()
        await _edit(update, text, markup)

    # Single server menu
    elif data.startswith("server_"):
        name = data[len("server_"):]
        text, markup = _server_menu_content(name)
        await _edit(update, text, markup)

    # Start
    elif data.startswith("start_"):
        name = data[len("start_"):]
        await query.answer()
        try:
            sm.start_server(name)
            notice = f"Server <b>{html.escape(name)}</b> started."
        except Exception as exc:
            notice = f"Error: {html.escape(str(exc))}"
        text, markup = _server_menu_content(name)
        await query.edit_message_text(
            f"{notice}\n\n{text}", reply_markup=markup, parse_mode="HTML"
        )

    # Stop
    elif data.startswith("stop_"):
        name = data[len("stop_"):]
        await query.answer()
        try:
            sm.stop_server(name)
            notice = f"Server <b>{html.escape(name)}</b> stopped."
        except Exception as exc:
            notice = f"Error: {html.escape(str(exc))}"
        text, markup = _server_menu_content(name)
        await query.edit_message_text(
            f"{notice}\n\n{text}", reply_markup=markup, parse_mode="HTML"
        )

    # Restart
    elif data.startswith("restart_"):
        name = data[len("restart_"):]
        await query.answer()
        try:
            sm.restart_server(name)
            notice = f"Server <b>{html.escape(name)}</b> restarted."
        except Exception as exc:
            notice = f"Error: {html.escape(str(exc))}"
        text, markup = _server_menu_content(name)
        await query.edit_message_text(
            f"{notice}\n\n{text}", reply_markup=markup, parse_mode="HTML"
        )

    # Logs
    elif data.startswith("logs_"):
        name = data[len("logs_"):]
        await query.answer()
        try:
            logs = sm.get_logs(name)
            safe_logs = html.escape(logs[-3000:])  # telegram message limit guard
            text = f"<b>Logs: {html.escape(name)}</b>\n\n<pre>{safe_logs}</pre>"
        except Exception as exc:
            text = f"Error: {html.escape(str(exc))}"
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Back", callback_data=f"server_{name}")]]
        )
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

    # Delete confirm
    elif data.startswith("confirm_delete_"):
        name = data[len("confirm_delete_"):]
        await query.answer()
        text = (
            f"Delete server <b>{html.escape(name)}</b>?\n"
            "This will remove all files."
        )
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Yes, delete", callback_data=f"delete_{name}"
                    ),
                    InlineKeyboardButton("Cancel", callback_data=f"server_{name}"),
                ]
            ]
        )
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

    # Delete confirmed
    elif data.startswith("delete_"):
        name = data[len("delete_"):]
        await query.answer()
        try:
            sm.delete_server(name)
            notice = f"Server <b>{html.escape(name)}</b> deleted."
        except Exception as exc:
            notice = f"Error: {html.escape(str(exc))}"
        text, markup = _servers_menu_content()
        await query.edit_message_text(
            f"{notice}\n\n{text}", reply_markup=markup, parse_mode="HTML"
        )

    # Create — choose type
    elif data == "menu_create":
        servers = sm.list_servers()
        if len(servers) >= config.MAX_SERVERS:
            await query.answer(f"Maximum {config.MAX_SERVERS} servers reached.", show_alert=True)
            return
        text = "<b>Create Server</b>\n\nChoose server type:"
        markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Paper", callback_data="create_type_paper")],
                [InlineKeyboardButton("Vanilla", callback_data="create_type_vanilla")],
                [InlineKeyboardButton("Spigot", callback_data="create_type_spigot")],
                [InlineKeyboardButton("Back", callback_data="menu_main")],
            ]
        )
        await _edit(update, text, markup)

    # Create — type selected, ask for name
    elif data.startswith("create_type_"):
        server_type = data[len("create_type_"):]
        await query.answer()
        context.user_data["pending_type"] = server_type
        text = (
            f"<b>Create Server</b>\n\n"
            f"Type: <b>{server_type}</b>\n\n"
            "Send the server name (letters, digits, underscores, max 24 chars):"
        )
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Cancel", callback_data="menu_main")]]
        )
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
        context.user_data["awaiting_server_name"] = True


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free-text messages (used only for entering a server name)."""
    if not context.user_data.get("awaiting_server_name"):
        return

    context.user_data["awaiting_server_name"] = False
    server_type = context.user_data.pop("pending_type", "paper")
    raw_name = (update.message.text or "").strip()

    if not re.fullmatch(r"[a-zA-Z0-9_]{1,24}", raw_name):
        await update.message.reply_text(
            "Invalid name. Use only letters, digits, underscores (max 24 chars).\n"
            "Send /start to return to the menu."
        )
        return

    # Acknowledge and start creation
    status_msg = await update.message.reply_text(
        f"Creating <b>{html.escape(raw_name)}</b> ({server_type})…",
        parse_mode="HTML",
    )

    steps: list[str] = []

    def progress(msg: str) -> None:
        steps.append(msg)
        asyncio.create_task(
            status_msg.edit_text(
                f"<b>Creating server…</b>\n\n" + "\n".join(html.escape(s) for s in steps),
                parse_mode="HTML",
            )
        )

    try:
        entry = await asyncio.to_thread(sm.create_server, raw_name, server_type, progress)
        ip = get_vps_ip()
        final = (
            f"Server <b>{html.escape(raw_name)}</b> created and started.\n\n"
            f"Address: <code>{ip}:{entry['port']}</code>"
        )
    except Exception as exc:
        final = f"Failed to create server: {html.escape(str(exc))}"

    text, markup = _main_menu_content()
    await status_msg.edit_text(
        f"{final}\n\n{text}", reply_markup=markup, parse_mode="HTML"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    init_db()

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
