import os
import asyncio
import sqlite3
from urllib.parse import urlparse

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

# NEW: Buttons for every individual start part
db_exec("""
CREATE TABLE IF NOT EXISTS start_buttons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_position INTEGER NOT NULL,
    button_position INTEGER NOT NULL,
    button_text TEXT NOT NULL,
    button_url TEXT NOT NULL
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
        "SELECT * FROM emojis ORDER BY id ASC"
    )


def premium_emoji(emoji_id, fallback="✨"):
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def build_emoji_text(text):
    """
    Converts:
        {emoji:1}

    into:
        Telegram premium/custom emoji HTML.
    """

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
# START PARTS
# =========================================================

def get_start_parts():
    return db_all(
        """
        SELECT *
        FROM start_parts
        ORDER BY position ASC
        """
    )


def get_start_part(position):
    return db_one(
        """
        SELECT *
        FROM start_parts
        WHERE position=?
        """,
        (position,)
    )


def add_start_part(
    media_type=None,
    media_id=None,
    text=None,
    caption=None
):

    row = db_one(
        "SELECT MAX(position) AS maxpos FROM start_parts"
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

    db_exec(
        "DELETE FROM start_parts"
    )

    db_exec(
        "DELETE FROM start_buttons"
    )


# =========================================================
# BUTTON FUNCTIONS
# =========================================================

def get_part_buttons(part_position):

    return db_all(
        """
        SELECT *
        FROM start_buttons
        WHERE part_position=?
        ORDER BY button_position ASC
        """,
        (part_position,)
    )


def add_part_button(
    part_position,
    button_text,
    button_url
):

    row = db_one(
        """
        SELECT MAX(button_position) AS maxpos
        FROM start_buttons
        WHERE part_position=?
        """,
        (part_position,)
    )

    next_position = (row["maxpos"] or 0) + 1

    db_exec(
        """
        INSERT INTO start_buttons(
            part_position,
            button_position,
            button_text,
            button_url
        )
        VALUES(?,?,?,?)
        """,
        (
            part_position,
            next_position,
            button_text,
            button_url
        )
    )


def build_part_keyboard(part_position):

    buttons = get_part_buttons(
        part_position
    )

    if not buttons:
        return None

    keyboard = []

    # IMPORTANT:
    # Every button is a separate row.
    # So buttons appear vertically.
    for button in buttons:

        keyboard.append([
            InlineKeyboardButton(
                button["button_text"],
                url=button["button_url"]
            )
        ])

    return InlineKeyboardMarkup(
        keyboard
    )


def is_valid_url(url):

    try:

        parsed = urlparse(
            url.strip()
        )

        return parsed.scheme in (
            "http",
            "https"
        ) and bool(parsed.netloc)

    except Exception:
        return False


# =========================================================
# SEND START SEQUENCE
# =========================================================

async def send_start_sequence(
    bot,
    chat_id
):

    parts = get_start_parts()

    if not parts:
        return False

    last_index = len(parts) - 1

    for i, part in enumerate(parts):

        position = part["position"]

        media_type = part["media_type"]
        media_id = part["media_id"]

        caption = build_emoji_text(
            part["caption"] or ""
        ) or None

        reply_markup = build_part_keyboard(
            position
        )

        try:

            if media_type == "video":

                await bot.send_video(
                    chat_id=chat_id,
                    video=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )

            elif media_type == "photo":

                await bot.send_photo(
                    chat_id=chat_id,
                    photo=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )

            elif media_type == "document":

                await bot.send_document(
                    chat_id=chat_id,
                    document=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )

            elif media_type == "animation":

                await bot.send_animation(
                    chat_id=chat_id,
                    animation=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )

            elif media_type == "audio":

                await bot.send_audio(
                    chat_id=chat_id,
                    audio=media_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )

            elif media_type == "voice":

                await bot.send_voice(
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

                    await bot.send_message(
                        chat_id=chat_id,
                        text=part_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup
                    )

        except Exception as e:

            print(
                f"Error sending part "
                f"{position}: {e}"
            )

        if i != last_index:

            await asyncio.sleep(
                0.4
            )

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

    return InlineKeyboardMarkup(
        keyboard
    )


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
# PREMIUM MENU
# =========================================================

def premium_menu_text_and_keyboard():

    count = len(
        get_emojis()
    )

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

        f"🧠 Learned Emoji IDs: "
        f"<b>{count}</b>\n"

        f"💾 Save Mode: "
        f"<b>{save_text}</b>\n\n"

        "Save Mode ON karo.\n"
        "Phir koi Premium Emoji wala message "
        "bot ko bhejo.\n\n"

        "Bot uski ID save karke "
        "use karne ka tag dega:\n"

        "<code>{emoji:1}</code>"

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
        ]

    ])

    return text, keyboard


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
            f"Join request error: {e}"
        )


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

    # =====================================================
    # BROADCAST
    # =====================================================

    if data == "broadcast":

        context.user_data.clear()

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

            await asyncio.sleep(
                0.1
            )

        await q.answer(
            f"{approved} requests approved.",
            show_alert=True
        )

        return

    # =====================================================
    # EDIT START
    # =====================================================

    if data == "edit_start":

        context.user_data.pop(
            "button_part",
            None
        )

        count = len(
            get_start_parts()
        )

        await q.edit_message_text(

            "✏️ <b>Edit /start Message</b>\n\n"

            f"📦 Saved Parts: "
            f"<b>{count}</b>\n\n"

            "Text, video, file etc. "
            "ek-ek karke add kar sakte ho.\n\n"

            "Buttons bhi kisi bhi Part ke niche "
            "alag se add kar sakte ho.",

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
                        "🔘 Add Button",
                        callback_data="add_button"
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
                ]

            ])
        )

        return

    # =====================================================
    # ADD PART
    # =====================================================

    if data == "add_start_part":

        context.user_data.clear()

        context.user_data[
            "state"
        ] = "start_part_add"

        await q.edit_message_text(

            "➕ <b>Add /start Part</b>\n\n"

            "Ab message bhejo:\n"
            "🎥 Video\n"
            "🖼 Photo\n"
            "📁 File\n"
            "🎞 GIF\n"
            "🎵 Audio\n"
            "🎤 Voice\n"
            "📝 Text\n\n"

            "Har message ek alag Part banega.\n\n"

            "Sab parts ke baad:\n"
            "<code>/done</code>\n\n"

            "Cancel:\n"
            "<code>/cancel</code>",

            parse_mode=ParseMode.HTML
        )

        return

    # =====================================================
    # ADD BUTTON
    # =====================================================

    if data == "add_button":

        parts = get_start_parts()

        if not parts:

            await q.answer(
                "Pehle kam se kam 1 Part add karo.",
                show_alert=True
            )

            return

        keyboard = []

        for p in parts:

            keyboard.append([

                InlineKeyboardButton(
                    f"📦 Part #{p['position']}",
                    callback_data=(
                        f"button_part_{p['position']}"
                    )
                )

            ])

        keyboard.append([

            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="edit_start"
            )

        ])

        await q.edit_message_text(

            "🔘 <b>Select Part</b>\n\n"

            "Jis Part ke niche button lagana hai "
            "wo select karo:",

            parse_mode=ParseMode.HTML,

            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        return

    # =====================================================
    # SELECT BUTTON PART
    # =====================================================

    if data.startswith(
        "button_part_"
    ):

        try:

            part_position = int(
                data.replace(
                    "button_part_",
                    ""
                )
            )

        except ValueError:

            await q.answer(
                "Invalid Part.",
                show_alert=True
            )

            return

        part = get_start_part(
            part_position
        )

        if not part:

            await q.answer(
                "Part not found.",
                show_alert=True
            )

            return

        context.user_data.clear()

        context.user_data[
            "state"
        ] = "button_name"

        context.user_data[
            "button_part"
        ] = part_position

        await q.edit_message_text(

            f"🔘 <b>Part #{part_position}</b>\n\n"

            "Ab button ka naam bhejo.\n\n"

            "Example:\n"
            "<code>REGISTER NOW</code>",

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

            for p in parts:

                position = p["position"]

                if p["media_type"]:

                    label = (
                        p["media_type"]
                    )

                else:

                    label = "text"

                buttons = get_part_buttons(
                    position
                )

                lines.append(
                    f"📦 <b>Part #{position}</b> "
                    f"— {label}"
                )

                if buttons:

                    for b in buttons:

                        lines.append(
                            f"   🔘 {b['button_text']}"
                        )

                lines.append("")

            text = "\n".join(
                lines
            )

        await q.edit_message_text(

            text,

            parse_mode=ParseMode.HTML,

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "🔘 Add Button",
                        callback_data="add_button"
                    )
                ],

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
            "All Parts + Buttons cleared ✅",
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
            "0"
            if current == "1"
            else "1"
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
                "✨ <b>Learned Premium Emojis</b>\n"
            ]

            for e in emojis:

                lines.append(

                    f"{premium_emoji(e['emoji_id'], e['emoji'])} "
                    f"<code>{{emoji:{e['id']}}}</code>\n"

                    f"ID: <code>{e['emoji_id']}</code>\n"

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
# ADMIN MEDIA
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

        "photo": "🖼 Photo",

        "document": "📁 File / Document",

        "animation": "🎞 GIF / Animation",

        "audio": "🎵 Audio",

        "voice": "🎤 Voice"

    }

    await message.reply_text(

        f"✅ <b>Part #{position} saved</b> "
        f"({labels.get(media_type, media_type)}).\n\n"

        "Next Part bhejo, ya:\n"
        "<code>/done</code>",

        parse_mode=ParseMode.HTML
    )


# =========================================================
# ADMIN TEXT
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

    # =====================================================
    # ADD TEXT PART
    # =====================================================

    if state == "start_part_add":

        position = add_start_part(
            text=text
        )

        await message.reply_text(

            f"✅ <b>Part #{position} saved</b> "
            "(text).\n\n"

            "Next Part bhejo, ya:\n"
            "<code>/done</code>",

            parse_mode=ParseMode.HTML
        )

        return

    # =====================================================
    # BUTTON NAME
    # =====================================================

    if state == "button_name":

        if not text.strip():

            await message.reply_text(
                "❌ Button name empty nahi ho sakta."
            )

            return

        context.user_data[
            "button_name"
        ] = text.strip()

        context.user_data[
            "state"
        ] = "button_url"

        await message.reply_text(

            "🔗 <b>Ab Button URL bhejo</b>\n\n"

            "Example:\n"
            "<code>https://example.com</code>",

            parse_mode=ParseMode.HTML
        )

        return

    # =====================================================
    # BUTTON URL
    # =====================================================

    if state == "button_url":

        url = text.strip()

        if not is_valid_url(url):

            await message.reply_text(

                "❌ <b>Invalid URL</b>\n\n"

                "URL <code>https://</code> "
                "ya <code>http://</code> se start hona chahiye.",

                parse_mode=ParseMode.HTML
            )

            return

        part_position = context.user_data.get(
            "button_part"
        )

        button_name = context.user_data.get(
            "button_name"
        )

        if not part_position or not button_name:

            context.user_data.clear()

            await message.reply_text(
                "❌ Button session expire ho gaya."
            )

            return

        add_part_button(
            part_position=part_position,
            button_text=button_name,
            button_url=url
        )

        context.user_data.clear()

        await message.reply_text(

            f"✅ <b>Button Added!</b>\n\n"

            f"📦 Part: <b>#{part_position}</b>\n"
            f"🔘 Button: <b>{button_name}</b>\n"
            f"🔗 URL: <code>{url}</code>\n\n"

            "Button Part ke niche "
            "alag line mein dikhega.\n\n"

            "Aur button add karna ho to "
            "<b>Admin Panel → Edit /start Msg → Add Button</b> use karo.",

            parse_mode=ParseMode.HTML,

            reply_markup=admin_panel()
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
# /DONE
# =========================================================

async def done_command(
    update,
    context
):

    if not admin_only(update):
        return

    if context.user_data.get(
        "state"
    ) != "start_part_add":

        await update.message.reply_text(
            "❌ Abhi Part adding mode active nahi hai."
        )

        return

    total = len(
        get_start_parts()
    )

    if total == 0:

        await update.message.reply_text(
            "❌ Koi Part save nahi hua."
        )

        return

    context.user_data.clear()

    await update.message.reply_text(

        "✅ <b>/start sequence saved!</b>\n\n"

        f"📦 Total Parts: <b>{total}</b>\n\n"

        "Test karne ke liye "
        "<code>/start</code> bhejo.",

        parse_mode=ParseMode.HTML,

        reply_markup=admin_panel()
    )


# =========================================================
# /CANCEL
# =========================================================

async def cancel_command(
    update,
    context
):

    if not admin_only(update):
        return

    if not context.user_data:

        await update.message.reply_text(
            "ℹ️ Koi active operation nahi hai."
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

    found = []

    for entity in entities:

        if entity.type != "custom_emoji":
            continue

        emoji_id = entity.custom_emoji_id

        if not emoji_id:
            continue

        if emoji_id in found:
            continue

        found.append(
            emoji_id
        )

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

            row = db_one(
                """
                SELECT id
                FROM emojis
                WHERE emoji_id=?
                """,
                (emoji_id,)
            )

            if not row:
                continue

            tag_id = row["id"]

            try:

                await context.bot.send_message(

                    chat_id=ADMIN_ID,

                    text=(

                        "✨ <b>Premium Emoji Learned!</b>\n\n"

                        f"🆔 ID:\n"
                        f"<code>{emoji_id}</code>\n\n"

                        f"🏷️ Tag:\n"
                        f"<code>{{emoji:{tag_id}}}</code>\n\n"

                        "Is tag ko apne Part ke "
                        "text/caption mein use karo."

                    ),

                    parse_mode=ParseMode.HTML
                )

            except Exception as e:

                print(
                    f"Emoji notification error: {e}"
                )


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
        "✨ <b>Learned Premium Emojis</b>\n"
    ]

    for e in emojis:

        lines.append(

            f"{premium_emoji(e['emoji_id'], e['emoji'])}\n"

            f"🆔 ID: "
            f"<code>{e['emoji_id']}</code>\n"

            f"🏷️ Tag: "
            f"<code>{{emoji:{e['id']}}}</code>\n"

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

    app.add_handler(
        CommandHandler(
            "done",
            done_command
        )
    )

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
    # PREMIUM EMOJI LEARNING
    # =====================================================

    app.add_handler(
        MessageHandler(
            filters.ALL,
            learn_custom_emojis
        ),
        group=2
    )

    # =====================================================
    # START
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
