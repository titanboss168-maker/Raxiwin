import os
import re
import sqlite3
import asyncio

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ContextTypes,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

DB = "bot.db"

# =========================================================
# DATABASE
# =========================================================

db = sqlite3.connect(DB, check_same_thread=False)
db.row_factory = sqlite3.Row

def db_exec(query, params=()):
    cur = db.cursor()
    cur.execute(query, params)
    db.commit()
    return cur

def db_one(query, params=()):
    return db_exec(query, params).fetchone()

def db_all(query, params=()):
    return db_exec(query, params).fetchall()


db_exec("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

db_exec("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT
)
""")

db_exec("""
CREATE TABLE IF NOT EXISTS pending_requests (
    user_id INTEGER PRIMARY KEY,
    chat_id INTEGER,
    username TEXT,
    first_name TEXT
)
""")

db_exec("""
CREATE TABLE IF NOT EXISTS buttons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT 'primary'
)
""")

db_exec("""
CREATE TABLE IF NOT EXISTS emojis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emoji_id TEXT UNIQUE NOT NULL,
    emoji TEXT DEFAULT '✨'
)
""")

def get_setting(key, default=""):
    row = db_one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default

def set_setting(key, value):
    db_exec(
        "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
        (key, str(value))
    )

# =========================================================
# HELPERS
# =========================================================

def is_admin(user_id):
    return user_id == ADMIN_ID

def save_user(user):
    if not user:
        return

    db_exec("""
        INSERT OR REPLACE INTO users(user_id, username, first_name)
        VALUES(?,?,?)
    """, (
        user.id,
        user.username or "",
        user.first_name or ""
    ))

def save_emoji_id(emoji_id, emoji="✨"):
    if not emoji_id:
        return

    db_exec("""
        INSERT OR IGNORE INTO emojis(emoji_id, emoji)
        VALUES(?,?)
    """, (emoji_id, emoji))

def get_emojis():
    return db_all("SELECT * FROM emojis ORDER BY id DESC")

def get_buttons():
    return db_all("SELECT * FROM buttons ORDER BY id ASC")

def admin_only(update):
    user = update.effective_user
    return user and is_admin(user.id)

# =========================================================
# CUSTOM EMOJI
# =========================================================

def premium_emoji(emoji_id, fallback="✨"):
    """
    Telegram custom emoji HTML format.
    """
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'

def build_emoji_text(text):
    """
    Replaces:
        {emoji:1}

    with saved custom emoji ID #1.
    """

    emojis = get_emojis()

    for row in emojis:
        tag = "{emoji:" + str(row["id"]) + "}"
        if tag in text:
            text = text.replace(
                tag,
                premium_emoji(
                    row["emoji_id"],
                    row["emoji"] or "✨"
                )
            )

    return text

# =========================================================
# BUTTONS
# =========================================================

def make_url_button(name, url, color="primary"):
    """
    Telegram supports:
      primary
      success
      danger
    """

    color = color.lower().strip()

    if color not in ("primary", "success", "danger"):
        color = "primary"

    # api_kwargs keeps compatibility with PTB versions
    # that don't expose style directly in constructor.
    return InlineKeyboardButton(
        text=name,
        url=url,
        api_kwargs={"style": color}
    )

def build_post_keyboard():
    rows = []

    for button in get_buttons():
        rows.append([
            make_url_button(
                button["name"],
                button["url"],
                button["color"]
            )
        ])

    return InlineKeyboardMarkup(rows) if rows else None

# =========================================================
# ADMIN PANEL
# =========================================================

def admin_panel():

    auto = get_setting("auto_approve", "0")
    save_mode = get_setting("save_mode", "0")

    auto_text = "ON 🟢" if auto == "1" else "OFF 🔴"
    save_text = "ON 🟢" if save_mode == "1" else "OFF 🔴"

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 Broadcast",
                callback_data="broadcast"
            )
        ],
        [
            InlineKeyboardButton(
                "✅ Approve All Requests",
                callback_data="approve_all"
            )
        ],
        [
            InlineKeyboardButton(
                f"⚙️ Auto-Approve: {auto_text}",
                callback_data="toggle_auto"
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ Edit /start Msg",
                callback_data="edit_start"
            )
        ],
        [
            InlineKeyboardButton(
                "🤖 Manage Auto DM",
                callback_data="auto_dm"
            )
        ],
        [
            InlineKeyboardButton(
                "➕ Add Button",
                callback_data="add_button"
            ),
            InlineKeyboardButton(
                "🗑 Delete Button",
                callback_data="delete_button"
            )
        ],
        [
            InlineKeyboardButton(
                "👀 View Buttons",
                callback_data="view_buttons"
            )
        ],
        [
            InlineKeyboardButton(
                f"💾 Save Mode: {save_text}",
                callback_data="toggle_save"
            )
        ],
        [
            InlineKeyboardButton(
                "✨ Premium Emoji",
                callback_data="premium_menu"
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)

async def show_panel(update, context, text=None):

    if update.callback_query:
        q = update.callback_query
        await q.answer()

        await q.edit_message_text(
            text or "⚙️ <b>Admin Panel</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel()
        )

    else:
        await update.message.reply_text(
            text or "⚙️ <b>Admin Panel</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel()
        )

# =========================================================
# /START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user
    save_user(user)

    if is_admin(user.id):
        await update.message.reply_text(
            "⚙️ <b>Admin Panel</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel()
        )
        return

    start_msg = get_setting(
        "start_message",
        "👋 <b>Welcome!</b>\n\nThanks for joining."
    )

    start_msg = build_emoji_text(start_msg)

    await update.message.reply_text(
        start_msg,
        parse_mode=ParseMode.HTML,
        reply_markup=build_post_keyboard()
    )

# =========================================================
# CHANNEL JOIN REQUEST
# =========================================================

async def join_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    request = update.chat_join_request
    user = request.from_user

    save_user(user)

    db_exec("""
        INSERT OR REPLACE INTO pending_requests
        (user_id, chat_id, username, first_name)
        VALUES(?,?,?,?)
    """, (
        user.id,
        request.chat.id,
        user.username or "",
        user.first_name or ""
    ))

    auto = get_setting("auto_approve", "0")

    # Auto approve
    if auto == "1":

        try:
            await request.approve()
        except Exception:
            pass

    # Send DM
    auto_dm = get_setting("auto_dm", "")

    if auto_dm:
        try:

            auto_dm = build_emoji_text(auto_dm)

            await context.bot.send_message(
                chat_id=user.id,
                text=auto_dm,
                parse_mode=ParseMode.HTML,
                reply_markup=build_post_keyboard()
            )

        except Exception:
            pass

# =========================================================
# CALLBACKS
# =========================================================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query

    if not is_admin(q.from_user.id):
        await q.answer("Not allowed.", show_alert=True)
        return

    data = q.data

    # -------------------------
    # Toggle Auto Approve
    # -------------------------

    if data == "toggle_auto":

        current = get_setting("auto_approve", "0")
        set_setting(
            "auto_approve",
            "0" if current == "1" else "1"
        )

        await show_panel(update, context)
        return

    # -------------------------
    # Toggle Save Mode
    # -------------------------

    if data == "toggle_save":

        current = get_setting("save_mode", "0")

        set_setting(
            "save_mode",
            "0" if current == "1" else "1"
        )

        await show_panel(update, context)
        return

    # -------------------------
    # Add Button
    # -------------------------

    if data == "add_button":

        context.user_data["state"] = "button_name"

        await q.edit_message_text(
            "➕ <b>Add Button</b>\n\n"
            "Fast method:\n\n"
            "<code>Button Name | https://yourlink.com | success</code>\n\n"
            "Colors:\n"
            "🔵 primary\n"
            "🟢 success\n"
            "🔴 danger\n\n"
            "Or simply send the Button Name and I'll ask for URL/color.\n\n"
            "Send /cancel to cancel.",
            parse_mode=ParseMode.HTML
        )

        return

    # -------------------------
    # Delete Button
    # -------------------------

    if data == "delete_button":

        buttons = get_buttons()

        if not buttons:
            await q.answer("No buttons saved.", show_alert=True)
            return

        keyboard = []

        for b in buttons:
            keyboard.append([
                InlineKeyboardButton(
                    f"🗑 {b['name']}",
                    callback_data=f"delbtn:{b['id']}"
                )
            ])

        keyboard.append([
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="back_panel"
            )
        ])

        await q.edit_message_text(
            "🗑 <b>Delete Button</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    # -------------------------
    # Delete selected button
    # -------------------------

    if data.startswith("delbtn:"):

        button_id = int(data.split(":")[1])

        db_exec(
            "DELETE FROM buttons WHERE id=?",
            (button_id,)
        )

        await q.answer("Button deleted ✅")

        await show_panel(update, context)
        return

    # -------------------------
    # View Buttons
    # -------------------------

    if data == "view_buttons":

        buttons = get_buttons()

        if not buttons:
            text = "📭 <b>No buttons saved.</b>"
        else:
            lines = ["🔘 <b>Saved Buttons</b>\n"]

            for i, b in enumerate(buttons, 1):
                lines.append(
                    f"{i}. <b>{b['name']}</b>\n"
                    f"🔗 {b['url']}\n"
                    f"🎨 {b['color']}\n"
                )

            text = "\n".join(lines)

        await q.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="back_panel"
                    )
                ]
            ])
        )

        return

    # -------------------------
    # Premium Menu
    # -------------------------

    if data == "premium_menu":

        count = len(get_emojis())

        text = (
            "✨ <b>Premium Emoji Manager</b>\n\n"
            f"🧠 Learned Emoji IDs: <b>{count}</b>\n\n"
            "Turn <b>Save Mode ON</b> and send custom "
            "emoji to this bot.\n\n"
            "The bot will automatically learn its ID.\n\n"
            "Use /emojis to view saved IDs."
        )

        await q.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "💾 Toggle Save Mode",
                        callback_data="toggle_save"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "👀 View Emojis & IDs",
                        callback_data="view_emojis"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🗑 Clear All Emojis",
                        callback_data="clear_emojis"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="back_panel"
                    )
                ]
            ])
        )

        return

    # -------------------------
    # View Emojis
    # -------------------------

    if data == "view_emojis":

        emojis = get_emojis()

        if not emojis:

            text = "📭 <b>No Premium Emojis saved.</b>"

        else:

            lines = [
                "✨ <b>Learned Emoji IDs</b>\n"
            ]

            for i, e in enumerate(emojis, 1):

                lines.append(
                    f"{i}. {premium_emoji(e['emoji_id'], e['emoji'])}\n"
                    f"<code>{e['emoji_id']}</code>\n"
                )

            text = "\n".join(lines)

        await q.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="premium_menu"
                    )
                ]
            ])
        )

        return

    # -------------------------
    # Clear Emojis
    # -------------------------

    if data == "clear_emojis":

        db_exec("DELETE FROM emojis")

        await q.answer("All emoji IDs cleared ✅")

        await show_panel(update, context)
        return

    # -------------------------
    # Auto DM
    # -------------------------

    if data == "auto_dm":

        current = get_setting(
            "auto_dm",
            "No Auto-DM message saved."
        )

        await q.edit_message_text(
            "🤖 <b>Auto DM Message</b>\n\n"
            f"{current}\n\n"
            "Send a new message to replace it.\n"
            "Use /cancel to cancel.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="back_panel"
                    )
                ]
            ])
        )

        context.user_data["state"] = "auto_dm"

        return

    # -------------------------
    # Edit Start
    # -------------------------

    if data == "edit_start":

        current = get_setting(
            "start_message",
            "👋 Welcome!"
        )

        await q.edit_message_text(
            "✏️ <b>Edit /start Message</b>\n\n"
            "Current:\n\n"
            f"{current}\n\n"
            "Send the new message.\n"
            "Use /cancel to cancel.",
            parse_mode=ParseMode.HTML
        )

        context.user_data["state"] = "start_message"

        return

    # -------------------------
    # Broadcast
    # -------------------------

    if data == "broadcast":

        context.user_data["state"] = "broadcast"

        await q.edit_message_text(
            "📢 <b>Broadcast</b>\n\n"
            "Send the message you want to broadcast.\n\n"
            "Use /cancel to cancel.",
            parse_mode=ParseMode.HTML
        )

        return

    # -------------------------
    # Approve All
    # -------------------------

    if data == "approve_all":

        requests = db_all(
            "SELECT * FROM pending_requests"
        )

        approved = 0

        for r in requests:

            try:

                await context.bot.approve_chat_join_request(
                    chat_id=r["chat_id"],
                    user_id=r["user_id"]
                )

                approved += 1

            except Exception:
                pass

            db_exec(
                "DELETE FROM pending_requests WHERE user_id=?",
                (r["user_id"],)
            )

            await asyncio.sleep(0.1)

        await q.answer(
            f"{approved} requests approved.",
            show_alert=True
        )

        return

    # -------------------------
    # Back
    # -------------------------

    if data == "back_panel":

        await show_panel(update, context)
        return

# =========================================================
# ADMIN TEXT HANDLER
# =========================================================

async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not admin_only(update):
        return

    message = update.message

    if not message:
        return

    text = message.text or ""

    state = context.user_data.get("state")

    # -------------------------
    # CANCEL
    # -------------------------

    if text.lower() == "/cancel":

        context.user_data.clear()

        await message.reply_text(
            "❌ Cancelled.",
            reply_markup=admin_panel()
        )

        return

    # -------------------------
    # ADD BUTTON
    # -------------------------

    if state == "button_name":

        parts = [x.strip() for x in text.split("|")]

        # Fast method
        if len(parts) >= 2:

            name = parts[0]
            url = parts[1]
            color = parts[2] if len(parts) >= 3 else "primary"

            if not re.match(r"^https?://", url):
                await message.reply_text(
                    "❌ URL must start with http:// or https://"
                )
                return

            if color not in ("primary", "success", "danger"):
                color = "primary"

            db_exec("""
                INSERT INTO buttons(name,url,color)
                VALUES(?,?,?)
            """, (
                name,
                url,
                color
            ))

            context.user_data.clear()

            await message.reply_text(
                "✅ <b>Button Added!</b>\n\n"
                f"🔘 {name}\n"
                f"🔗 {url}\n"
                f"🎨 {color}",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_panel()
            )

            return

        # Step-by-step
        context.user_data["button_name"] = text
        context.user_data["state"] = "button_url"

        await message.reply_text(
            "🔗 Now send the URL:"
        )

        return

    if state == "button_url":

        url = text.strip()

        if not re.match(r"^https?://", url):

            await message.reply_text(
                "❌ Invalid URL.\n\n"
                "Example:\n"
                "https://example.com"
            )

            return

        context.user_data["button_url"] = url
        context.user_data["state"] = "button_color"

        await message.reply_text(
            "🎨 Send color:\n\n"
            "🔵 primary\n"
            "🟢 success\n"
            "🔴 danger"
        )

        return

    if state == "button_color":

        color = text.lower().strip()

        if color not in ("primary", "success", "danger"):

            await message.reply_text(
                "❌ Wrong color.\n\n"
                "Use: primary / success / danger"
            )

            return

        name = context.user_data["button_name"]
        url = context.user_data["button_url"]

        db_exec("""
            INSERT INTO buttons(name,url,color)
            VALUES(?,?,?)
        """, (
            name,
            url,
            color
        ))

        context.user_data.clear()

        await message.reply_text(
            "✅ <b>Button Added!</b>\n\n"
            f"🔘 {name}\n"
            f"🔗 {url}\n"
            f"🎨 {color}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel()
        )

        return

    # -------------------------
    # AUTO DM
    # -------------------------

    if state == "auto_dm":

        set_setting("auto_dm", text)

        context.user_data.clear()

        await message.reply_text(
            "✅ Auto-DM message saved!",
            reply_markup=admin_panel()
        )

        return

    # -------------------------
    # START MESSAGE
    # -------------------------

    if state == "start_message":

        set_setting("start_message", text)

        context.user_data.clear()

        await message.reply_text(
            "✅ /start message updated!",
            reply_markup=admin_panel()
        )

        return

    # -------------------------
    # BROADCAST
    # -------------------------

    if state == "broadcast":

        users = db_all(
            "SELECT user_id FROM users"
        )

        sent = 0
        failed = 0

        for user in users:

            try:

                await message.copy(
                    chat_id=user["user_id"]
                )

                sent += 1

            except Exception:
                failed += 1

            await asyncio.sleep(0.05)

        context.user_data.clear()

        await message.reply_text(
            "📢 <b>Broadcast Finished</b>\n\n"
            f"✅ Sent: {sent}\n"
            f"❌ Failed: {failed}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel()
        )

        return

# =========================================================
# PREMIUM EMOJI LEARNING
# =========================================================

async def learn_custom_emojis(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_user:
        return

    if not is_admin(update.effective_user.id):
        return

    if get_setting("save_mode", "0") != "1":
        return

    message = update.effective_message

    if not message:
        return

    entities = []

    if message.entities:
        entities.extend(message.entities)

    if message.caption_entities:
        entities.extend(message.caption_entities)

    for entity in entities:

        if entity.type != "custom_emoji":
            continue

        emoji_id = entity.custom_emoji_id

        if not emoji_id:
            continue

        # Fallback emoji
        fallback = "✨"

        try:
            if message.text:
                # Get the actual emoji covered by entity.
                # Telegram entity offsets are UTF-16 based,
                # so using the first emoji as fallback is safest.
                fallback = "✨"
        except Exception:
            pass

        before = len(get_emojis())

        save_emoji_id(
            emoji_id,
            fallback
        )

        after = len(get_emojis())

        if after > before:

            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        "✨ <b>New Premium Emoji Learned!</b>\n\n"
                        f"ID:\n<code>{emoji_id}</code>\n\n"
                        f"Use in message:\n"
                        f"<code>{{emoji:{after}}}</code>"
                    ),
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

# =========================================================
# /EMOJIS
# =========================================================

async def emojis_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not admin_only(update):
        return

    emojis = get_emojis()

    if not emojis:

        await update.message.reply_text(
            "📭 No saved custom emojis."
        )

        return

    lines = [
        "✨ <b>Learned Emoji IDs</b>\n"
    ]

    for i, e in enumerate(emojis, 1):

        lines.append(
            f"{i}. {premium_emoji(e['emoji_id'], e['emoji'])}\n"
            f"ID: <code>{e['emoji_id']}</code>\n"
            f"Tag: <code>{{emoji:{i}}}</code>\n"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML
    )

# =========================================================
# /PANEL
# =========================================================

async def panel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not admin_only(update):
        return

    await update.message.reply_text(
        "⚙️ <b>Admin Panel</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_panel()
    )

# =========================================================
# MAIN
# =========================================================

def main():

    if BOT_TOKEN == "PASTE_BOT_TOKEN_HERE":
        print("❌ Please set BOT_TOKEN.")
        return

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("panel", panel_command)
    )

    app.add_handler(
        CommandHandler("emojis", emojis_command)
    )

    # Channel join requests
    app.add_handler(
        ChatJoinRequestHandler(join_request)
    )

    # Admin buttons
    app.add_handler(
        CallbackQueryHandler(callbacks)
    )

    # Admin text
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            admin_text
        ),
        group=1
    )

    # Learn custom emojis
    app.add_handler(
        MessageHandler(
            filters.ALL,
            learn_custom_emojis
        ),
        group=2
    )

    print("🤖 Bot started...")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
