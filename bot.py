import os
import asyncio
import sqlite3

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
# USERS
# =========================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


def admin_only(update):
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
            user.first_name or ""
        )
    )


# =========================================================
# PREMIUM EMOJI
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
        (emoji_id, emoji)
    )


def get_emojis():
    return db_all(
        "SELECT * FROM emojis ORDER BY id DESC"
    )


def premium_emoji(emoji_id, fallback="✨"):
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def build_emoji_text(text):
    if not text:
        return text

    for row in get_emojis():

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
# START SEQUENCE
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
            position,
            media_type,
            media_id,
            text,
            caption
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
    db_exec("DELETE FROM start_parts")


# =========================================================
# SEND START SEQUENCE
# =========================================================

async def send_start_sequence(bot, chat_id):

    parts = get_start_parts()

    if not parts:
        return False

    last_index = len(parts) - 1

    for i, part in enumerate(parts):

        media_type = part["media_type"]
        media_id = part["media_id"]

        caption = build_emoji_text(
            part["caption"] or ""
        ) or None

        try:

            if media_type == "video":

                await bot.send_video(
                    chat_id=chat_id,
                    video=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )

            elif media_type == "photo":

                await bot.send_photo(
                    chat_id=chat_id,
                    photo=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )

            elif media_type == "document":

                await bot.send_document(
                    chat_id=chat_id,
                    document=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )

            elif media_type == "animation":

                await bot.send_animation(
                    chat_id=chat_id,
                    animation=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )

            elif media_type == "audio":

                await bot.send_audio(
                    chat_id=chat_id,
                    audio=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )

            elif media_type == "voice":

                await bot.send_voice(
                    chat_id=chat_id,
                    voice=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )

            else:

                part_text = build_emoji_text(
                    part["text"] or ""
                )

                if part_text:

                    await bot.send_message(
                        chat_id=chat_id,
                        text=part_text,
                        parse_mode=ParseMode.HTML
                    )

        except Exception as e:

            print(
                f"Error sending start part {i + 1}: {e}"
            )

        if i != last_index:
            await asyncio.sleep(0.4)

    return True


# =========================================================
# ADMIN PANEL
# =========================================================

def admin_panel():

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
                "✏️ Edit /start Msg",
                callback_data="edit_start"
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
# PREMIUM MENU
# =========================================================

def premium_menu_text_and_keyboard():

    count = len(get_emojis())

    save_mode = get_setting(
        "save_mode",
        "0"
    )

    save_text = (
        "ON 🟢"
        if save_mode == "1"
        else
        "OFF 🔴"
    )

    text = (
        "✨ <b>Premium Emoji Manager</b>\n\n"
        f"🧠 Learned Emoji IDs: <b>{count}</b>\n"
        f"💾 Save Mode: <b>{save_text}</b>\n\n"
        "Turn Save Mode ON aur custom emoji "
        "is bot ko bhejo — bot khud uski ID seek lega.\n\n"
        "Use /emojis to view saved IDs "
        "and their {emoji:N} tags."
    )

    keyboard = InlineKeyboardMarkup([

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
        ],

    ])

    return text, keyboard


# =========================================================
# /START
# =========================================================

async def start(update, context):

    user = update.effective_user

    save_user(user)

    if is_admin(user.id):

        await update.message.reply_text(
            "⚙️ <b>Admin Panel</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel()
        )

        return

    sent = await send_start_sequence(
        context.bot,
        update.effective_chat.id
    )

    if not sent:

        await update.message.reply_text(
            "👋 <b>Welcome!</b>",
            parse_mode=ParseMode.HTML
        )


# =========================================================
# JOIN REQUEST
# =========================================================

async def join_request(update, context):

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

    try:

        sent = await send_start_sequence(
            context.bot,
            user.id
        )

        if not sent:

            await context.bot.send_message(
                chat_id=user.id,
                text="👋 <b>Welcome!</b>",
                parse_mode=ParseMode.HTML
            )

    except Exception as e:

        print(
            f"Join request DM error: {e}"
        )


# =========================================================
# CALLBACKS
# =========================================================

async def callbacks(update, context):

    q = update.callback_query

    if not is_admin(q.from_user.id):

        await q.answer(
            "Not allowed.",
            show_alert=True
        )

        return

    data = q.data

    # =====================================================
    # BROADCAST
    # =====================================================

    if data == "broadcast":

        context.user_data["state"] = "broadcast"

        await q.edit_message_text(
            "📢 <b>Broadcast</b>\n\n"
            "Send the message you want to broadcast.\n\n"
            "Use /cancel to cancel.",
            parse_mode=ParseMode.HTML
        )

        return

    # =====================================================
    # APPROVE ALL
    # =====================================================

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

            except Exception as e:

                print(
                    f"Approve error: {e}"
                )

            db_exec(
                """
                DELETE FROM pending_requests
                WHERE user_id=?
                """,
                (r["user_id"],)
            )

            await asyncio.sleep(0.1)

        await q.answer(
            f"{approved} requests approved.",
            show_alert=True
        )

        return

    # =====================================================
    # EDIT START
    # =====================================================

    if data == "edit_start":

        count = len(get_start_parts())

        await q.edit_message_text(

            "✏️ <b>Edit /start Message</b>\n\n"

            f"📦 Saved parts: <b>{count}</b>\n\n"

            "/start ek se zyada messages "
            "(parts) mein bhej sakte ho — "
            "text, photo, video, file, GIF, audio, "
            "voice etc.\n\n"

            "Jis order mein add karoge, "
            "usi order mein users ko bheja jayega.\n\n"

            "Naya sequence banane ke liye "
            "pehle 'Clear All Parts' karo.",

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

    # =====================================================
    # ADD START PART
    # =====================================================

    if data == "add_start_part":

        context.user_data["state"] = "start_part_add"

        await q.edit_message_text(

            "➕ <b>Add /start Part</b>\n\n"

            "Ab jo bhi message bhejoge "
            "(text, photo, video, file, GIF, "
            "audio, voice) — wo ek naya part "
            "ban kar saved parts ke last mein add hoga.\n\n"

            "Jitne chaho utne messages "
            "ek-ek karke bhejo.\n\n"

            "Sab parts bhejne ke baad "
            "<code>/done</code> bhejo.\n\n"

            "Cancel karne ke liye "
            "<code>/cancel</code> bhejo.",

            parse_mode=ParseMode.HTML
        )

        return

    # =====================================================
    # VIEW START PARTS
    # =====================================================

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
                        f"{i}. 📎 "
                        f"{p['media_type']}"
                        f"{extra}"
                    )

                else:

                    preview = (
                        p["text"] or ""
                    )[:40]

                    suffix = (
                        "..."
                        if len(p["text"] or "") > 40
                        else ""
                    )

                    lines.append(
                        f"{i}. 📝 "
                        f"{preview}{suffix}"
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

    # =====================================================
    # CLEAR START PARTS
    # =====================================================

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

    # =====================================================
    # PREMIUM MENU
    # =====================================================

    if data == "premium_menu":

        text, keyboard = (
            premium_menu_text_and_keyboard()
        )

        await q.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

        return

    # =====================================================
    # TOGGLE SAVE
    # =====================================================

    if data == "toggle_save":

        current = get_setting(
            "save_mode",
            "0"
        )

        set_setting(
            "save_mode",
            "0" if current == "1" else "1"
        )

        text, keyboard = (
            premium_menu_text_and_keyboard()
        )

        await q.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

        return

    # =====================================================
    # VIEW EMOJIS
    # =====================================================

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

    # =====================================================
    # CLEAR EMOJIS
    # =====================================================

    if data == "clear_emojis":

        db_exec(
            "DELETE FROM emojis"
        )

        await q.answer(
            "All emoji IDs cleared ✅",
            show_alert=True
        )

        await show_panel(
            update,
            context
        )

        return

    # =====================================================
    # BACK
    # =====================================================

    if data == "back_panel":

        await show_panel(
            update,
            context
        )

        return


# =========================================================
# ADMIN MEDIA HANDLER
# =========================================================

async def admin_media(update, context):

    if not admin_only(update):
        return

    if (
        context.user_data.get("state")
        != "start_part_add"
    ):
        return

    message = update.message

    if not message:
        return

    media_type = None
    media_id = None

    if message.video:

        media_type = "video"
        media_id = message.video.file_id

    elif message.photo:

        media_type = "photo"
        media_id = message.photo[-1].file_id

    elif message.document:

        media_type = "document"
        media_id = message.document.file_id

    elif message.animation:

        media_type = "animation"
        media_id = message.animation.file_id

    elif message.audio:

        media_type = "audio"
        media_id = message.audio.file_id

    elif message.voice:

        media_type = "voice"
        media_id = message.voice.file_id

    else:

        return

    caption = message.caption or ""

    position = add_start_part(
        media_type=media_type,
        media_id=media_id,
        caption=caption
    )

    labels = {

        "video": "🎥 Video",

        "photo": "🖼️ Photo",

        "document": "📁 File / Document",

        "animation": "🎞️ GIF / Animation",

        "audio": "🎵 Audio",

        "voice": "🎤 Voice"

    }

    await message.reply_text(

        f"✅ Part #{position} saved "
        f"({labels.get(media_type, media_type)}).\n\n"

        "Next part bhejo, ya "
        "<code>/done</code> likh kar finish karo.",

        parse_mode=ParseMode.HTML
    )


# =========================================================
# ADMIN TEXT HANDLER
# =========================================================

async def admin_text(update, context):

    if not admin_only(update):
        return

    message = update.message

    if not message:
        return

    text = message.text or ""

    state = context.user_data.get(
        "state"
    )

    # =====================================================
    # START PART ADD - TEXT
    # =====================================================

    if state == "start_part_add":

        position = add_start_part(
            text=text
        )

        await message.reply_text(

            f"✅ Part #{position} saved (text).\n\n"

            "Next part bhejo, ya "
            "<code>/done</code> likh kar finish karo.",

            parse_mode=ParseMode.HTML
        )

        return

    # =====================================================
    # BROADCAST
    # =====================================================

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

            except Exception as e:

                failed += 1

                print(
                    f"Broadcast error: {e}"
                )

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
# /DONE COMMAND
# =========================================================

async def done_command(update, context):

    if not admin_only(update):
        return

    if (
        context.user_data.get("state")
        != "start_part_add"
    ):

        await update.message.reply_text(
            "❌ Abhi /start part adding mode active nahi hai."
        )

        return

    total = len(
        get_start_parts()
    )

    if total == 0:

        await update.message.reply_text(
            "❌ Koi part save nahi hua."
        )

        return

    context.user_data.clear()

    await update.message.reply_text(

        "✅ <b>/start sequence saved!</b>\n\n"

        f"📦 Total parts: <b>{total}</b>\n\n"

        "Test karne ke liye "
        "<code>/start</code> bhejo.",

        parse_mode=ParseMode.HTML,

        reply_markup=admin_panel()
    )


# =========================================================
# /CANCEL COMMAND
# =========================================================

async def cancel_command(update, context):

    if not admin_only(update):
        return

    state = context.user_data.get(
        "state"
    )

    if not state:

        await update.message.reply_text(
            "ℹ️ Abhi koi active operation nahi hai."
        )

        return

    context.user_data.clear()

    await update.message.reply_text(

        "❌ <b>Cancelled.</b>",

        parse_mode=ParseMode.HTML,

        reply_markup=admin_panel()
    )


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

        emoji_id = entity.custom_emoji_id

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

                        "✨ <b>New Premium Emoji Learned!</b>\n\n"

                        f"ID:\n"
                        f"<code>{emoji_id}</code>\n\n"

                        "Use in message:\n"
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

    # =====================================================
    # COMMANDS
    # =====================================================

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

    # FIX: /done now has its own handler
    app.add_handler(
        CommandHandler(
            "done",
            done_command
        )
    )

    # FIX: /cancel now has its own handler
    app.add_handler(
        CommandHandler(
            "cancel",
            cancel_command
        )
    )

    # =====================================================
    # JOIN REQUEST
    # =====================================================

    app.add_handler(
        ChatJoinRequestHandler(
            join_request
        )
    )

    # =====================================================
    # CALLBACKS
    # =====================================================

    app.add_handler(
        CallbackQueryHandler(
            callbacks
        )
    )

    # =====================================================
    # MEDIA
    # =====================================================

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

    # =====================================================
    # TEXT
    # =====================================================

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            admin_text
        ),
        group=1
    )

    # =====================================================
    # PREMIUM EMOJI
    # =====================================================

    app.add_handler(
        MessageHandler(
            filters.ALL,
            learn_custom_emojis
        ),
        group=2
    )

    # =====================================================
    # START BOT
    # =====================================================

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
