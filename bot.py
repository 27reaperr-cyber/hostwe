"""
bot.py — Minecraft Server Manager Bot
All navigation via edit_message_text. State stored in module-level dict (survives
context resets). Server creation runs in a thread with thread-safe progress updates.
"""

from __future__ import annotations

import asyncio
import html
import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import server_manager as sm
from utils import get_vps_ip, init_db, logger, register_user, status_label

# ── Module-level state (survives context resets) ──────────────────────────────
# user_id -> True when we're waiting for a server name from that user
_awaiting_name: dict[int, bool] = {}


# ── Keyboard / text builders ──────────────────────────────────────────────────

def _main_kb() -> tuple[str, InlineKeyboardMarkup]:
    servers = sm.list_servers()
    running = sum(1 for s in servers if s["status"] == "running")
    text = (
        "<b>Minecraft Server Manager</b>\n\n"
        f"Servers: {len(servers)}\n"
        f"Running: {running}"
    )
    kb = [
        [InlineKeyboardButton("Servers", callback_data="menu_servers")],
        [InlineKeyboardButton("Create Server", callback_data="menu_create")],
    ]
    return text, InlineKeyboardMarkup(kb)


def _servers_kb() -> tuple[str, InlineKeyboardMarkup]:
    servers = sm.list_servers()
    if servers:
        lines = "\n".join(f"{s['name']} — {status_label(s['status'])}" for s in servers)
        text = f"<b>Servers</b>\n\n{lines}"
    else:
        text = "<b>Servers</b>\n\n(no servers yet)"
    kb = [[InlineKeyboardButton(s["name"], callback_data=f"srv_{s['name']}")] for s in servers]
    kb.append([InlineKeyboardButton("Back", callback_data="menu_main")])
    return text, InlineKeyboardMarkup(kb)


def _server_kb(name: str) -> tuple[str, InlineKeyboardMarkup]:
    srv = sm.get_server(name)
    if not srv:
        text = f"<b>Server '{html.escape(name)}' not found.</b>"
        kb = [[InlineKeyboardButton("Back", callback_data="menu_servers")]]
        return text, InlineKeyboardMarkup(kb)
    ip = get_vps_ip()
    text = (
        f"<b>Server: {html.escape(name)}</b>\n\n"
        f"Status:  {status_label(srv['status'])}\n"
        f"Address: <code>{ip}:{srv['port']}</code>"
    )
    kb = [
        [
            InlineKeyboardButton("▶️ Start",   callback_data=f"start_{name}"),
            InlineKeyboardButton("⏹️ Stop",    callback_data=f"stop_{name}"),
            InlineKeyboardButton("⚙️ Restart", callback_data=f"restart_{name}"),
        ],
        [
            InlineKeyboardButton("📄 Logs",   callback_data=f"logs_{name}"),
            InlineKeyboardButton("🗑️ Delete", callback_data=f"del_confirm_{name}"),
        ],
        [InlineKeyboardButton("Back", callback_data="menu_servers")],
    ]
    return text, InlineKeyboardMarkup(kb)


# ── Safe edit helper ──────────────────────────────────────────────────────────

async def _edit(msg: Message, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await msg.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    register_user(user.id, user.username)
    _awaiting_name.pop(user.id, None)
    text, markup = _main_kb()
    await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


# ── Callback handler ──────────────────────────────────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data: str = query.data
    msg: Message = query.message
    uid: int = update.effective_user.id

    # ── Navigation ────────────────────────────────────────────────────────────
    if data == "menu_main":
        _awaiting_name.pop(uid, None)
        await _edit(msg, *_main_kb())

    elif data == "menu_servers":
        await _edit(msg, *_servers_kb())

    elif data.startswith("srv_"):
        await _edit(msg, *_server_kb(data[4:]))

    # ── Create ────────────────────────────────────────────────────────────────
    elif data == "menu_create":
        if len(sm.list_servers()) >= config.MAX_SERVERS:
            await query.answer(f"Maximum {config.MAX_SERVERS} servers reached.", show_alert=True)
            return
        _awaiting_name[uid] = True
        text = (
            "<b>Create Server</b>\n\n"
            "Type: <b>Paper</b> (latest)\n\n"
            "Send the server name\n"
            "<i>letters, digits, underscores — max 24 chars</i>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="menu_main")]])
        await _edit(msg, text, kb)

    # ── Start ─────────────────────────────────────────────────────────────────
    elif data.startswith("start_"):
        name = data[6:]
        try:
            sm.start_server(name)
            notice = f"▶️ <b>{html.escape(name)}</b> started."
        except Exception as exc:
            notice = f"Error: {html.escape(str(exc))}"
        t, m = _server_kb(name)
        await _edit(msg, f"{notice}\n\n{t}", m)

    # ── Stop ──────────────────────────────────────────────────────────────────
    elif data.startswith("stop_"):
        name = data[5:]
        try:
            sm.stop_server(name)
            notice = f"⏹️ <b>{html.escape(name)}</b> stopped."
        except Exception as exc:
            notice = f"Error: {html.escape(str(exc))}"
        t, m = _server_kb(name)
        await _edit(msg, f"{notice}\n\n{t}", m)

    # ── Restart ───────────────────────────────────────────────────────────────
    elif data.startswith("restart_"):
        name = data[8:]
        try:
            sm.restart_server(name)
            notice = f"⚙️ <b>{html.escape(name)}</b> restarted."
        except Exception as exc:
            notice = f"Error: {html.escape(str(exc))}"
        t, m = _server_kb(name)
        await _edit(msg, f"{notice}\n\n{t}", m)

    # ── Logs ──────────────────────────────────────────────────────────────────
    elif data.startswith("logs_"):
        name = data[5:]
        try:
            logs = sm.get_logs(name)
            safe = html.escape(logs)
            text = f"<b>📄 Logs: {html.escape(name)}</b>\n\n<pre>{safe}</pre>"
        except Exception as exc:
            text = f"Error: {html.escape(str(exc))}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Refresh", callback_data=f"logs_{name}"),
             InlineKeyboardButton("Back",    callback_data=f"srv_{name}")],
        ])
        try:
            await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise

    # ── Delete confirm ────────────────────────────────────────────────────────
    elif data.startswith("del_confirm_"):
        name = data[12:]
        text = f"🗑️ Delete <b>{html.escape(name)}</b>?\nAll server files will be removed."
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("Yes, delete", callback_data=f"del_do_{name}"),
            InlineKeyboardButton("Cancel",      callback_data=f"srv_{name}"),
        ]])
        await _edit(msg, text, kb)

    # ── Delete execute ────────────────────────────────────────────────────────
    elif data.startswith("del_do_"):
        name = data[7:]
        try:
            sm.delete_server(name)
            notice = f"🗑️ <b>{html.escape(name)}</b> deleted."
        except Exception as exc:
            notice = f"Error: {html.escape(str(exc))}"
        t, m = _servers_kb()
        await _edit(msg, f"{notice}\n\n{t}", m)


# ── Text message handler (server name input) ──────────────────────────────────

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id

    if not _awaiting_name.pop(uid, False):
        return  # not waiting for input from this user

    raw = (update.message.text or "").strip()

    if not re.fullmatch(r"[a-zA-Z0-9_]{1,24}", raw):
        await update.message.reply_text(
            "Invalid name. Use only letters, digits, underscores (max 24 chars).\n"
            "Send /start to go back to the menu."
        )
        return

    if sm.get_server(raw):
        await update.message.reply_text(
            f"Server <b>{html.escape(raw)}</b> already exists. Choose a different name.",
            parse_mode="HTML",
        )
        return

    if len(sm.list_servers()) >= config.MAX_SERVERS:
        await update.message.reply_text(f"Maximum {config.MAX_SERVERS} servers reached.")
        return

    status_msg = await update.message.reply_text(
        f"⚙️ <b>Creating {html.escape(raw)}…</b>", parse_mode="HTML"
    )

    steps: list[str] = []
    loop = asyncio.get_event_loop()

    def _progress(line: str) -> None:
        steps.append(line)
        body = "\n".join(html.escape(s) for s in steps)
        future = asyncio.run_coroutine_threadsafe(
            status_msg.edit_text(
                f"⚙️ <b>Creating server…</b>\n\n{body}", parse_mode="HTML"
            ),
            loop,
        )
        try:
            future.result(timeout=10)
        except Exception:
            pass  # "not modified" or rate-limit — safe to ignore

    try:
        entry = await asyncio.to_thread(sm.create_server, raw, _progress)
        ip = get_vps_ip()
        final = (
            f"▶️ Server <b>{html.escape(raw)}</b> created and started.\n"
            f"Address: <code>{ip}:{entry['port']}</code>"
        )
    except Exception as exc:
        logger.exception("create_server failed")
        final = f"Failed: <code>{html.escape(str(exc))}</code>"

    main_text, main_kb = _main_kb()
    try:
        await status_msg.edit_text(
            f"{final}\n\n{main_text}", reply_markup=main_kb, parse_mode="HTML"
        )
    except BadRequest:
        await update.message.reply_text(
            f"{final}\n\n{main_text}", reply_markup=main_kb, parse_mode="HTML"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    init_db()
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
