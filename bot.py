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

db_exec("""
CREATE TABLE IF NOT EXISTS start_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position INTEGER NOT NULL,
    media_type TEXT,
    media_id TEXT,
    text TEXT,
    caption TEXT
)
""")


# =========================================================
# SETTINGS
# =========================================================

def get_setting(key, default=""):
    row = db_one(
        "SELECT value FROM settings WHERE key=?",
        (key,)
    )
    return row["value"] if row else default


def set_setting(key, value):
    db_exec(
        "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
        (key, str(value))
    )


# =========================================================
# USER FUNCTIONS
# =========================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


def admin_only(update):
    """
    Returns True only if the update comes from the configured admin.
    Used to gate admin-only handlers (admin_media, admin_text,
    panel_command, emojis_command). This was called throughout the
    original file but never defined, causing a NameError crash.
    """
    user = update.effective_user
    return bool(user and is_admin(user.id))


def save_user(user):
    if not user:
        return

    db_exec(
        """
        INSERT OR REPLACE INTO users(
            user_id,
            username,
            first_name
        )
        VALUES(?,?,?)
        """,
        (
            user.id,
            user.username or "",
            user.first_name or "",
        )
    )


# =========================================================
# EMOJI FUNCTIONS
# =========================================================

def save_emoji_id(emoji_id, emoji="✨"):
    if not emoji_id:
        return

    db_exec(
        """
        INSERT OR IGNORE INTO emojis(
            emoji_id,
            emoji
        )
        VALUES(?,?)
        """,
        (
            emoji_id,
            emoji,
        )
    )


def get_emojis():
    return db_all(
        "SELECT * FROM emojis ORDER BY id DESC"
    )


def premium_emoji(emoji_id, fallback="✨"):
    return (
        f'<tg-emoji emoji-id="{emoji_id}">'
        f'{fallback}'
        f'</tg-emoji>'
    )


def build_emoji_text(text):
    if not text:
        return text

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
# START SEQUENCE (multi-part /start message)
# =========================================================

def get_start_parts():
    return db_all(
        "SELECT * FROM start_parts ORDER BY position ASC"
    )


def add_start_part(
    media_type=None,
    media_id=None,
    text=None,
    caption=None
):
    row = db_one(
        "SELECT MAX(position) as maxpos FROM start_parts"
    )

    next_pos = (row["maxpos"] or 0) + 1

    db_exec(
        """
        INSERT INTO start_parts(
            position, media_type, media_id, text, caption
        )
        VALUES(?,?,?,?,?)
        """,
        (
            next_pos,
            media_type,
            media_id,
            text,
            caption
        )
    )

    return next_pos


def clear_start_parts():
    db_exec(
        "DELETE FROM start_parts"
    )


# =========================================================
# BUTTON FUNCTIONS
# =========================================================

def get_buttons():
    return db_all(
        "SELECT * FROM buttons ORDER BY id ASC"
    )


def make_url_button(name, url, color="primary"):

    color = color.lower().strip()

    if color not in (
        "primary",
        "success",
        "danger"
    ):
        color = "primary"

    return InlineKeyboardButton(
        text=name,
        url=url,
        api_kwargs={
            "style": color
        }
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

    if not rows:
        return None

    return InlineKeyboardMarkup(rows)


# =========================================================
# ADMIN PANEL
# =========================================================

def admin_panel():

    auto = get_setting(
        "auto_approve",
        "0"
    )

    save_mode = get_setting(
        "save_mode",
        "0"
    )

    auto_text = (
        "ON 🟢"
        if auto == "1"
        else
        "OFF 🔴"
    )

    save_text = (
        "ON 🟢"
        if save_mode == "1"
        else
        "OFF 🔴"
    )

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


# =========================================================
# SHOW PANEL
# =========================================================

async def show_panel(
    update,
    context,
    text=None
):

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
# SEND SAVED /START MESSAGE
# =========================================================

async def send_saved_start(
    update,
    context
):

    # -----------------------------------------------------
    # MULTI-PART /start SEQUENCE (2-3-4+ messages)
    # -----------------------------------------------------

    parts = get_start_parts()

    if parts:

        chat_id = update.effective_chat.id

        keyboard = build_post_keyboard()

        last_index = len(parts) - 1

        for i, part in enumerate(parts):

            is_last = (i == last_index)

            reply_markup = keyboard if is_last else None

            media_type = part["media_type"]
            media_id = part["media_id"]

            caption = build_emoji_text(
                part["caption"] or ""
            ) or None

            if media_type == "video":

                await context.bot.send_video(
                    chat_id=chat_id,
                    video=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )

            elif media_type == "photo":

                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )

            elif media_type == "document":

                await context.bot.send_document(
                    chat_id=chat_id,
                    document=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )

            elif media_type == "animation":

                await context.bot.send_animation(
                    chat_id=chat_id,
                    animation=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )

            elif media_type == "audio":

                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )

            elif media_type == "voice":

                await context.bot.send_voice(
                    chat_id=chat_id,
                    voice=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )

            else:

                part_text = build_emoji_text(
                    part["text"] or ""
                )

                if part_text:

                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=part_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup
                    )

            if not is_last:
                await asyncio.sleep(0.4)

        return

    # -----------------------------------------------------
    # LEGACY SINGLE /start MESSAGE (fallback if no parts saved)
    # -----------------------------------------------------

    media_type = get_setting(
        "start_media_type",
        ""
    )

    media_id = get_setting(
        "start_media_id",
        ""
    )

    caption = build_emoji_text(
        get_setting(
            "start_caption",
            ""
        )
    )

    keyboard = build_post_keyboard()

    # -----------------------------------------------------
    # MEDIA START MESSAGE
    # -----------------------------------------------------

    if media_type and media_id:

        if media_type == "video":

            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=media_id,
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )

            return

        if media_type == "photo":

            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=media_id,
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )

            return

        if media_type == "document":

            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=media_id,
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )

            return

        if media_type == "animation":

            await context.bot.send_animation(
                chat_id=update.effective_chat.id,
                animation=media_id,
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )

            return

        if media_type == "audio":

            await context.bot.send_audio(
                chat_id=update.effective_chat.id,
                audio=media_id,
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )

            return

        if media_type == "voice":

            await context.bot.send_voice(
                chat_id=update.effective_chat.id,
                voice=media_id,
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )

            return

    # -----------------------------------------------------
    # TEXT START MESSAGE
    # -----------------------------------------------------

    start_msg = build_emoji_text(
        get_setting(
            "start_message",
            "👋 <b>Welcome!</b>\n\nThanks for joining."
        )
    )

    await update.message.reply_text(
        start_msg,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )


# =========================================================
# /START
# =========================================================

async def start(
    update,
    context
):

    user = update.effective_user

    save_user(user)

    if is_admin(user.id):

        await update.message.reply_text(
            "⚙️ <b>Admin Panel</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel()
        )

        return

    await send_saved_start(
        update,
        context
    )


# =========================================================
# JOIN REQUEST
# =========================================================

async def join_request(
    update,
    context
):

    request = update.chat_join_request

    user = request.from_user

    save_user(user)

    db_exec(
        """
        INSERT OR REPLACE INTO pending_requests(
            user_id,
            chat_id,
            username,
            first_name
        )
        VALUES(?,?,?,?)
        """,
        (
            user.id,
            request.chat.id,
            user.username or "",
            user.first_name or ""
        )
    )

    # Auto approve

    if get_setting(
        "auto_approve",
        "0"
    ) == "1":

        try:

            await request.approve()

        except Exception:

            pass

    # Auto DM

    auto_dm = get_setting(
        "auto_dm",
        ""
    )

    if auto_dm:

        try:

            auto_dm = build_emoji_text(
                auto_dm
            )

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

async def callbacks(
    update,
    context
):

    q = update.callback_query

    if not is_admin(
        q.from_user.id
    ):

        await q.answer(
            "Not allowed.",
            show_alert=True
        )

        return

    data = q.data

    # -----------------------------------------------------
    # AUTO APPROVE
    # -----------------------------------------------------

    if data == "toggle_auto":

        current = get_setting(
            "auto_approve",
            "0"
        )

        set_setting(
            "auto_approve",
            "0" if current == "1" else "1"
        )

        await show_panel(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # SAVE MODE
    # -----------------------------------------------------

    if data == "toggle_save":

        current = get_setting(
            "save_mode",
            "0"
        )

        set_setting(
            "save_mode",
            "0" if current == "1" else "1"
        )

        await show_panel(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # ADD BUTTON
    # -----------------------------------------------------

    if data == "add_button":

        context.user_data[
            "state"
        ] = "button_name"

        await q.edit_message_text(

            "➕ <b>Add Button</b>\n\n"

            "Fast method:\n\n"

            "<code>"
            "Button Name | https://yourlink.com | success"
            "</code>\n\n"

            "Colors:\n"
            "🔵 primary\n"
            "🟢 success\n"
            "🔴 danger\n\n"

            "Or simply send the Button Name "
            "and I'll ask for URL/color.\n\n"

            "Send /cancel to cancel.",

            parse_mode=ParseMode.HTML
        )

        return

    # -----------------------------------------------------
    # DELETE BUTTON
    # -----------------------------------------------------

    if data == "delete_button":

        buttons = get_buttons()

        if not buttons:

            await q.answer(
                "No buttons saved.",
                show_alert=True
            )

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
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        return

    # -----------------------------------------------------
    # DELETE BUTTON
    # -----------------------------------------------------

    if data.startswith("delbtn:"):

        button_id = int(
            data.split(":")[1]
        )

        db_exec(
            "DELETE FROM buttons WHERE id=?",
            (button_id,)
        )

        await q.answer(
            "Button deleted ✅"
        )

        await show_panel(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # VIEW BUTTONS
    # -----------------------------------------------------

    if data == "view_buttons":

        buttons = get_buttons()

        if not buttons:

            text = (
                "📭 <b>No buttons saved.</b>"
            )

        else:

            lines = [
                "🔘 <b>Saved Buttons</b>\n"
            ]

            for i, b in enumerate(
                buttons,
                1
            ):

                lines.append(
                    f"{i}. <b>{b['name']}</b>\n"
                    f"🔗 {b['url']}\n"
                    f"🎨 {b['color']}\n"
                )

            text = "\n".join(
                lines
            )

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

    # -----------------------------------------------------
    # PREMIUM MENU
    # -----------------------------------------------------

    if data == "premium_menu":

        count = len(
            get_emojis()
        )

        text = (
            "✨ <b>Premium Emoji Manager</b>\n\n"

            f"🧠 Learned Emoji IDs: "
            f"<b>{count}</b>\n\n"

            "Turn <b>Save Mode ON</b> "
            "and send custom emoji to this bot.\n\n"

            "The bot will automatically learn "
            "its ID.\n\n"

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

    # -----------------------------------------------------
    # VIEW EMOJIS
    # -----------------------------------------------------

    if data == "view_emojis":

        emojis = get_emojis()

        if not emojis:

            text = (
                "📭 <b>No Premium Emojis saved.</b>"
            )

        else:

            lines = [
                "✨ <b>Learned Emoji IDs</b>\n"
            ]

            for i, e in enumerate(
                emojis,
                1
            ):

                lines.append(

                    f"{i}. "
                    f"{premium_emoji(e['emoji_id'], e['emoji'])}\n"

                    f"<code>{e['emoji_id']}</code>\n"

                )

            text = "\n".join(
                lines
            )

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

    # -----------------------------------------------------
    # CLEAR EMOJIS
    # -----------------------------------------------------

    if data == "clear_emojis":

        db_exec(
            "DELETE FROM emojis"
        )

        await q.answer(
            "All emoji IDs cleared ✅"
        )

        await show_panel(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # AUTO DM
    # -----------------------------------------------------

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

        context.user_data[
            "state"
        ] = "auto_dm"

        return

    # -----------------------------------------------------
    # EDIT START
    # -----------------------------------------------------

    if data == "edit_start":

        count = len(
            get_start_parts()
        )

        await q.edit_message_text(

            "✏️ <b>Edit /start Message</b>\n\n"

            f"📦 Saved parts: <b>{count}</b>\n\n"

            "/start ab ek se zyada messages (parts) mein "
            "bhi bhej sakte ho — jaise pehle text, phir "
            "photo, phir video, waghera.\n\n"

            "Parts wahi order mein bhejte honge jis order "
            "mein aapne add kiye the.",

            parse_mode=ParseMode.HTML,

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "➕ Add Part",
                        callback_data="add_start_part"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "👀 View Parts",
                        callback_data="view_start_parts"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "🗑 Clear All Parts",
                        callback_data="clear_start_parts"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="back_panel"
                    )
                ],

            ])
        )

        return

    # -----------------------------------------------------
    # ADD START PART
    # -----------------------------------------------------

    if data == "add_start_part":

        context.user_data[
            "state"
        ] = "start_part_add"

        await q.edit_message_text(

            "➕ <b>Add /start Part</b>\n\n"

            "Ab jo bhi message bhejoge (text, photo, "
            "video, file, gif, audio, voice) — wo ek naya "
            "part ban kar saved parts ke last mein add ho "
            "jayega.\n\n"

            "Jitne chaho utne messages ek-ek karke bhejo.\n\n"

            "Jab sab parts bhej chuke ho to <code>/done</code> "
            "bhejo.\n\n"

            "Cancel karne ke liye /cancel bhejo.",

            parse_mode=ParseMode.HTML
        )

        return

    # -----------------------------------------------------
    # VIEW START PARTS
    # -----------------------------------------------------

    if data == "view_start_parts":

        parts = get_start_parts()

        if not parts:

            text = (
                "📭 <b>No /start parts saved.</b>"
            )

        else:

            lines = [
                "📦 <b>Saved /start Parts</b>\n"
            ]

            for i, p in enumerate(parts, 1):

                if p["media_type"]:

                    extra = (
                        " (with caption)"
                        if p["caption"]
                        else ""
                    )

                    lines.append(
                        f"{i}. 📎 {p['media_type']}{extra}"
                    )

                else:

                    preview = (p["text"] or "")[:40]

                    suffix = (
                        "..."
                        if len(p["text"] or "") > 40
                        else ""
                    )

                    lines.append(
                        f"{i}. 📝 {preview}{suffix}"
                    )

            text = "\n".join(lines)

        await q.edit_message_text(

            text,

            parse_mode=ParseMode.HTML,

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="edit_start"
                    )
                ]

            ])
        )

        return

    # -----------------------------------------------------
    # CLEAR START PARTS
    # -----------------------------------------------------

    if data == "clear_start_parts":

        clear_start_parts()

        await q.answer(
            "All /start parts cleared ✅",
            show_alert=True
        )

        await show_panel(
            update,
            context
        )

        return

    # -----------------------------------------------------
    # BROADCAST
    # -----------------------------------------------------

    if data == "broadcast":

        context.user_data[
            "state"
        ] = "broadcast"

        await q.edit_message_text(

            "📢 <b>Broadcast</b>\n\n"

            "Send the message you want "
            "to broadcast.\n\n"

            "Use /cancel to cancel.",

            parse_mode=ParseMode.HTML
        )

        return

    # -----------------------------------------------------
    # APPROVE ALL
    # -----------------------------------------------------

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
                "DELETE FROM pending_requests "
                "WHERE user_id=?",
                (r["user_id"],)
            )

            await asyncio.sleep(
                0.1
            )

        await q.answer(
            f"{approved} requests approved.",
            show_alert=True
        )

        return

    # -----------------------------------------------------
    # BACK
    # -----------------------------------------------------

    if data == "back_panel":

        await show_panel(
            update,
            context
        )

        return


# =========================================================
# ADMIN MEDIA / FILE HANDLER
# =========================================================

async def admin_media(
    update,
    context
):

    if not admin_only(update):
        return

    if context.user_data.get(
        "state"
    ) != "start_part_add":

        return

    message = update.message

    if not message:
        return

    media_type = None
    media_id = None

    # VIDEO

    if message.video:

        media_type = "video"
        media_id = message.video.file_id

    # PHOTO

    elif message.photo:

        media_type = "photo"

        media_id = (
            message.photo[-1].file_id
        )

    # DOCUMENT / FILE

    elif message.document:

        media_type = "document"
        media_id = (
            message.document.file_id
        )

    # GIF / ANIMATION

    elif message.animation:

        media_type = "animation"
        media_id = (
            message.animation.file_id
        )

    # AUDIO

    elif message.audio:

        media_type = "audio"
        media_id = (
            message.audio.file_id
        )

    # VOICE

    elif message.voice:

        media_type = "voice"
        media_id = (
            message.voice.file_id
        )

    else:

        return

    # Caption

    caption = (
        message.caption or ""
    )

    # Save as a new part (state stays "start_part_add" so
    # the admin can keep sending more parts)

    position = add_start_part(
        media_type=media_type,
        media_id=media_id,
        caption=caption
    )

    labels = {

        "video":
        "🎥 Video",

        "photo":
        "🖼️ Photo",

        "document":
        "📁 File / Document",

        "animation":
        "🎞️ GIF / Animation",

        "audio":
        "🎵 Audio",

        "voice":
        "🎤 Voice"

    }

    await message.reply_text(

        f"✅ Part #{position} saved "
        f"({labels.get(media_type, media_type)}).\n\n"

        "Next part bhejo, ya <code>/done</code> "
        "likh kar finish karo.",

        parse_mode=ParseMode.HTML
    )


# =========================================================
# ADMIN TEXT HANDLER
# =========================================================

async def admin_text(
    update,
    context
):

    if not admin_only(update):
        return

    message = update.message

    if not message:
        return

    text = message.text or ""

    state = context.user_data.get(
        "state"
    )

    # -----------------------------------------------------
    # CANCEL
    # -----------------------------------------------------

    if text.lower() == "/cancel":

        context.user_data.clear()

        await message.reply_text(
            "❌ Cancelled.",
            reply_markup=admin_panel()
        )

        return

    # -----------------------------------------------------
    # START PART ADD (text part / finish sequence)
    # -----------------------------------------------------

    if state == "start_part_add":

        if text.lower() == "/done":

            context.user_data.clear()

            total = len(
                get_start_parts()
            )

            await message.reply_text(

                "✅ <b>/start sequence saved!</b>\n\n"

                f"📦 Total parts: <b>{total}</b>\n\n"

                "Test karne ke liye /start bhejo.",

                parse_mode=ParseMode.HTML,

                reply_markup=admin_panel()
            )

            return

        position = add_start_part(
            text=text
        )

        await message.reply_text(

            f"✅ Part #{position} saved (text).\n\n"

            "Next part bhejo, ya <code>/done</code> "
            "likh kar finish karo.",

            parse_mode=ParseMode.HTML
        )

        return

    # -----------------------------------------------------
    # BUTTON NAME
    # -----------------------------------------------------

    if state == "button_name":

        parts = [
            x.strip()
            for x in text.split("|")
        ]

        if len(parts) >= 2:

            name = parts[0]

            url = parts[1]

            color = (
                parts[2]
                if len(parts) >= 3
                else
                "primary"
            )

            if not re.match(
                r"^https?://",
                url
            ):

                await message.reply_text(
                    "❌ URL must start with "
                    "http:// or https://"
                )

                return

            if color not in (
                "primary",
                "success",
                "danger"
            ):

                color = "primary"

            db_exec(

                "INSERT INTO buttons("
                "name,url,color"
                ") VALUES(?,?,?)",

                (
                    name,
                    url,
                    color
                )

            )

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

        # Step 2

        context.user_data[
            "button_name"
        ] = text

        context.user_data[
            "state"
        ] = "button_url"

        await message.reply_text(
            "🔗 Now send the URL:"
        )

        return

    # -----------------------------------------------------
    # BUTTON URL
    # -----------------------------------------------------

    if state == "button_url":

        url = text.strip()

        if not re.match(
            r"^https?://",
            url
        ):

            await message.reply_text(

                "❌ Invalid URL.\n\n"

                "Example:\n"
                "https://example.com"

            )

            return

        context.user_data[
            "button_url"
        ] = url

        context.user_data[
            "state"
        ] = "button_color"

        await message.reply_text(

            "🎨 Send color:\n\n"

            "🔵 primary\n"
            "🟢 success\n"
            "🔴 danger"

        )

        return

    # -----------------------------------------------------
    # BUTTON COLOR
    # -----------------------------------------------------

    if state == "button_color":

        color = text.lower().strip()

        if color not in (
            "primary",
            "success",
            "danger"
        ):

            await message.reply_text(

                "❌ Wrong color.\n\n"

                "Use:\n"
                "primary / success / danger"

            )

            return

        name = context.user_data[
            "button_name"
        ]

        url = context.user_data[
            "button_url"
        ]

        db_exec(

            "INSERT INTO buttons("
            "name,url,color"
            ") VALUES(?,?,?)",

            (
                name,
                url,
                color
            )

        )

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

    # -----------------------------------------------------
    # AUTO DM
    # -----------------------------------------------------

    if state == "auto_dm":

        set_setting(
            "auto_dm",
            text
        )

        context.user_data.clear()

        await message.reply_text(

            "✅ Auto-DM message saved!",

            reply_markup=admin_panel()
        )

        return

    # -----------------------------------------------------
    # START MESSAGE TEXT
    # -----------------------------------------------------

    if state == "start_message":

        # Clear old media

        set_setting(
            "start_media_type",
            ""
        )

        set_setting(
            "start_media_id",
            ""
        )

        set_setting(
            "start_caption",
            ""
        )

        # Save text

        set_setting(
            "start_message",
            text
        )

        context.user_data.clear()

        await message.reply_text(

            "✅ <b>/start message updated!</b>\n\n"

            "Video/photo/file etc. bhi "
            "Edit /start Msg se save kar sakte ho.",

            parse_mode=ParseMode.HTML,

            reply_markup=admin_panel()
        )

        return

    # -----------------------------------------------------
    # BROADCAST
    # -----------------------------------------------------

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

            await asyncio.sleep(
                0.05
            )

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
    update,
    context
):

    if not update.effective_user:
        return

    if not is_admin(
        update.effective_user.id
    ):
        return

    if get_setting(
        "save_mode",
        "0"
    ) != "1":
        return

    message = update.effective_message

    if not message:
        return

    entities = []

    if message.entities:
        entities.extend(
            message.entities
        )

    if message.caption_entities:
        entities.extend(
            message.caption_entities
        )

    for entity in entities:

        if entity.type != "custom_emoji":
            continue

        emoji_id = (
            entity.custom_emoji_id
        )

        if not emoji_id:
            continue

        before = len(
            get_emojis()
        )

        save_emoji_id(
            emoji_id,
            "✨"
        )

        after = len(
            get_emojis()
        )

        if after > before:

            try:

                await context.bot.send_message(

                    chat_id=ADMIN_ID,

                    text=(

                        "✨ <b>New Premium "
                        "Emoji Learned!</b>\n\n"

                        f"ID:\n"
                        f"<code>{emoji_id}</code>\n\n"

                        "Use in message:\n"

                        f"<code>"
                        f"{{emoji:{after}}}"
                        f"</code>"

                    ),

                    parse_mode=ParseMode.HTML

                )

            except Exception:

                pass


# =========================================================
# /EMOJIS
# =========================================================

async def emojis_command(
    update,
    context
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

    for i, e in enumerate(
        emojis,
        1
    ):

        lines.append(

            f"{i}. "
            f"{premium_emoji(e['emoji_id'], e['emoji'])}\n"

            f"ID: "
            f"<code>{e['emoji_id']}</code>\n"

            f"Tag: "
            f"<code>{{emoji:{i}}}</code>\n"

        )

    await update.message.reply_text(

        "\n".join(lines),

        parse_mode=ParseMode.HTML
    )


# =========================================================
# /PANEL
# =========================================================

async def panel_command(
    update,
    context
):

    if not admin_only(update):
        return

    await update.message.reply_text(

        "⚙️ <b>Admin Panel</b>",

        parse_mode=ParseMode.HTML,

        reply_markup=admin_panel()
    )


# =========================================================
# /ID
# =========================================================

async def id_command(
    update,
    context
):

    if not update.effective_user:
        return

    await update.message.reply_text(

        f"Your Telegram ID: "
        f"{update.effective_user.id}"

    )


# =========================================================
# MAIN
# =========================================================

def main():

    if BOT_TOKEN == "PASTE_BOT_TOKEN_HERE":

        print(
            "❌ Please set BOT_TOKEN."
        )

        return

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "panel",
            panel_command
        )
    )

    app.add_handler(
        CommandHandler(
            "emojis",
            emojis_command
        )
    )

    app.add_handler(
        CommandHandler(
            "id",
            id_command
        )
    )

    # Join requests

    app.add_handler(
        ChatJoinRequestHandler(
            join_request
        )
    )

    # Buttons / callbacks

    app.add_handler(
        CallbackQueryHandler(
            callbacks
        )
    )

    # -----------------------------------------------------
    # MEDIA / FILES
    # -----------------------------------------------------

    media_filter = (

        filters.VIDEO

        | filters.PHOTO

        | filters.Document.ALL

        | filters.ANIMATION

        | filters.AUDIO

        | filters.VOICE

    )

    app.add_handler(

        MessageHandler(
            media_filter,
            admin_media
        ),

        group=0

    )

    # -----------------------------------------------------
    # TEXT
    # -----------------------------------------------------

    app.add_handler(

        MessageHandler(

            filters.TEXT
            & ~filters.COMMAND,

            admin_text

        ),

        group=1

    )

    # -----------------------------------------------------
    # PREMIUM EMOJI
    # -----------------------------------------------------

    app.add_handler(

        MessageHandler(

            filters.ALL,

            learn_custom_emojis

        ),

        group=2

    )

    print(
        "🤖 Bot started..."
    )

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
