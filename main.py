
import asyncio
import logging
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.enums import ChatMemberStatus
from google import genai

# ==============================
# CONFIGURATION
# ==============================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-3.8-flash"
DB_NAME = "jojo_bot.db"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
ai = genai.Client(api_key=GEMINI_API_KEY)

# ==============================
# DATABASE
# ==============================

def db():
    return sqlite3.connect(DB_NAME)


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                xp INTEGER NOT NULL DEFAULT 0,
                warnings INTEGER NOT NULL DEFAULT 0,
                title TEXT DEFAULT 'Новичок',
                joined_at TEXT NOT NULL,
                last_xp TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS warnings_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                admin_id INTEGER NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity (
                user_id INTEGER PRIMARY KEY,
                messages INTEGER DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_history (
                user_id INTEGER PRIMARY KEY,
                interaction_id TEXT
            )
        """)


def register_user(user):
    with db() as conn:
        conn.execute("""
            INSERT INTO users
            (user_id, username, full_name, joined_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name
        """, (
            user.id,
            user.username,
            user.full_name,
            datetime.now(timezone.utc).isoformat()
        ))


def get_user(user_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()


def add_xp(user_id, amount):
    with db() as conn:
        conn.execute(
            "UPDATE users SET xp = xp + ? WHERE user_id = ?",
            (amount, user_id)
        )


def get_rank(xp):
    ranks = [
        (0, "Новичок"),
        (100, "Ученик"),
        (300, "Боец"),
        (600, "Опытный боец"),
        (1000, "Пользователь стенда"),
        (2000, "Мастер стенда"),
        (3500, "Легенда"),
        (5000, "Повелитель времени")
    ]

    current = ranks[0][1]
    next_rank = None

    for threshold, name in ranks:
        if xp >= threshold:
            current = name
        else:
            next_rank = (threshold, name)
            break

    return current, next_rank


def update_activity(user_id):
    with db() as conn:
        conn.execute("""
            INSERT INTO activity (user_id, messages)
            VALUES (?, 1)
            ON CONFLICT(user_id)
            DO UPDATE SET messages = messages + 1
        """, (user_id,))


def get_message_count(user_id):
    with db() as conn:
        row = conn.execute(
            "SELECT messages FROM activity WHERE user_id = ?",
            (user_id,)
        ).fetchone()

    return row[0] if row else 0


def get_top():
    with db() as conn:
        return conn.execute("""
            SELECT full_name, xp, title
            FROM users
            ORDER BY xp DESC
            LIMIT 10
        """).fetchall()


# ==============================
# USER REGISTRATION AND XP
# ==============================

@dp.message(F.new_chat_members)
async def new_members(message: Message):
    for user in message.new_chat_members:
        register_user(user)

        await message.answer(
            f"⚡ Добро пожаловать, {user.full_name}!\n"
            "Добро пожаловать в JOJO OVERDRIVE!"
        )


@dp.message(F.from_user)
async def activity_handler(message: Message):
    if message.from_user.is_bot:
        return

    register_user(message.from_user)

    if not message.text:
        update_activity(message.from_user.id)
        return

    if message.text.startswith("/"):
        return

    update_activity(message.from_user.id)

    user = get_user(message.from_user.id)
    now = datetime.now(timezone.utc)

    last_xp = user[7]

    if last_xp:
        try:
            previous = datetime.fromisoformat(last_xp)
            if now - previous < timedelta(seconds=60):
                return
        except ValueError:
            pass

    amount = random.randint(3, 7)

    with db() as conn:
        conn.execute("""
            UPDATE users
            SET xp = xp + ?, last_xp = ?
            WHERE user_id = ?
        """, (amount, now.isoformat(), message.from_user.id))


# ==============================
# BASIC COMMANDS
# ==============================

@dp.message(Command("start"))
async def start(message: Message):
    register_user(message.from_user)

    await message.answer(
        "⚡ JOJO OVERDRIVE BOT\n\n"
        "Добро пожаловать в мир JoJo!\n"
        "Используй /help, чтобы узнать мои команды."
    )


@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "⚡ КОМАНДЫ JOJO OVERDRIVE\n\n"
        "👤 ПРОФИЛЬ\n"
        "/profile — твой профиль\n"
        "/rank — твой ранг\n"
        "/top — таблица лидеров\n\n"
        "🤖 ИСКУССТВЕННЫЙ ИНТЕЛЛЕКТ\n"
        "/ask текст — задать вопрос Gemini\n\n"
        "🛡 МОДЕРАЦИЯ\n"
        "/mute — ограничить участника\n"
        "/unmute — снять ограничение\n"
        "/ban — заблокировать\n"
        "/unban — разблокировать\n"
        "/kick — исключить\n"
        "/warn — выдать предупреждение\n"
        "/warnings — посмотреть предупреждения\n"
        "/clearwarns — очистить предупреждения"
    )


@dp.message(Command("profile"))
async def profile(message: Message):
    target = message.reply_to_message.from_user if (
        message.reply_to_message
    ) else message.from_user

    register_user(target)
    user = get_user(target.id)

    xp = user[3]
    rank, next_rank = get_rank(xp)

    progress = (
        f"Следующий ранг: {next_rank[1]}\n"
        f"До него: {next_rank[0] - xp} XP"
        if next_rank else "Максимальный ранг достигнут!"
    )

    await message.answer(
        f"👤 ПРОФИЛЬ\n\n"
        f"Имя: {target.full_name}\n"
        f"ID: {target.id}\n"
        f"Титул: {user[5]}\n"
        f"Ранг: {rank}\n"
        f"Опыт: {xp} XP\n"
        f"Сообщений: {get_message_count(target.id)}\n"
        f"Предупреждений: {user[4]}\n\n"
        f"{progress}"
    )


@dp.message(Command("rank"))
async def rank_command(message: Message):
    register_user(message.from_user)
    user = get_user(message.from_user.id)

    rank, next_rank = get_rank(user[3])

    result = f"⚔️ ТВОЙ РАНГ: {rank}\nXP: {user[3]}"

    if next_rank:
        result += (
            f"\nСледующий ранг: {next_rank[1]}"
            f"\nОсталось XP: {next_rank[0] - user[3]}"
        )

    await message.answer(result)


@dp.message(Command("top"))
async def top_command(message: Message):
    users = get_top()

    if not users:
        await message.answer("Пока нет участников в рейтинге.")
        return

    result = "🏆 ТОП УЧАСТНИКОВ JOJO OVERDRIVE\n\n"

    for index, user in enumerate(users, 1):
        name = user[0] or "Неизвестный"
        result += (
            f"{index}. {name}\n"
            f"   {user[1]} XP | {user[2]}\n\n"
        )

    await message.answer(result)


# ==============================
# GEMINI AI
# ==============================

@dp.message(Command("ask"))
async def ask_command(
    message: Message,
    command: CommandObject
):
    question = command.args

    if not question:
        await message.answer(
            "Напиши вопрос после команды.\n"
            "Пример: /ask Кто такой Джотаро?"
        )
        return

    user_id = message.from_user.id

    await bot.send_chat_action(
        message.chat.id,
        "typing"
    )

    with db() as conn:
        row = conn.execute(
            "SELECT interaction_id FROM ai_history WHERE user_id = ?",
            (user_id,)
        ).fetchone()

    previous_id = row[0] if row else None

    prompt = (
        "Ты ИИ-ассистент Telegram-сообщества JOJO OVERDRIVE. "
        "Общайся естественно, дружелюбно и понятно. "
        "Ты хорошо разбираешься в JoJo's Bizarre Adventure, "
        "аниме, манге и других темах. "
        "Не выдумывай факты. "
        "Отвечай на языке пользователя.\n\n"
        f"Сообщение пользователя: {question}"
    )

    try:
        response = await asyncio.to_thread(
            ai.interactions.create,
            model=GEMINI_MODEL,
            input=prompt,
            previous_interaction_id=previous_id
        )

        answer = response.output_text or "Не удалось получить ответ."

        with db() as conn:
            conn.execute("""
                INSERT INTO ai_history (user_id, interaction_id)
                VALUES (?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET interaction_id = excluded.interaction_id
            """, (user_id, response.id))

        await message.answer(answer[:4000])

    except Exception as error:
        logging.exception("Gemini error")

        await message.answer(
            "⚠️ Не удалось получить ответ от Gemini.\n"
            "Попробуй ещё раз немного позже."
        )


# ==============================
# MODERATION HELPERS
# ==============================

async def is_admin(message: Message):
    if not message.from_user:
        return False

    member = await bot.get_chat_member(
        message.chat.id,
        message.from_user.id
    )

    return member.status in (
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR
    )


async def get_target(message: Message, args=None):
    if message.reply_to_message:
        return message.reply_to_message.from_user

    if args:
        try:
            user_id = int(args.split()[0])
            member = await bot.get_chat_member(
                message.chat.id,
                user_id
            )
            return member.user
        except Exception:
            return None

    return None


def parse_duration(args):
    if not args:
        return 600

    match = re.search(r"(\d+)([smhd])", args.lower())

    if not match:
        return 600

    value = int(match.group(1))
    unit = match.group(2)

    multipliers = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400
    }

    return min(value * multipliers[unit], 366 * 86400)


async def moderation_check(message):
    if not await is_admin(message):
        await message.answer("⛔ Эта команда доступна только администраторам.")
        return False

    return True


# ==============================
# MUTE / UNMUTE
# ==============================

@dp.message(Command("mute"))
async def mute_command(message: Message, command: CommandObject):
    if not await moderation_check(message):
        return

    target = await get_target(message, command.args)

    if not target:
        await message.answer(
            "Использование: ответ на сообщение /mute 10m\n"
            "Или: /mute ID 10m"
        )
        return

    seconds = parse_duration(command.args)

    until = datetime.now(timezone.utc) + timedelta(seconds=seconds)

    try:
        await bot.restrict_chat_member(
            message.chat.id,
            target.id,
            permissions={
                "can_send_messages": False
            },
            until_date=until
        )

        await message.answer(
            f"🔇 {target.full_name} получил мут."
        )

    except Exception as error:
        logging.exception("Mute error")
        await message.answer("Не удалось выдать мут.")


@dp.message(Command("unmute"))
async def unmute_command(message: Message, command: CommandObject):
    if not await moderation_check(message):
        return

    target = await get_target(message, command.args)

    if not target:
        await message.answer("Укажи пользователя через ответ или ID.")
        return

    try:
        await bot.restrict_chat_member(
            message.chat.id,
            target.id,
            permissions={
                "can_send_messages": True,
                "can_send_audios": True,
                "can_send_documents": True,
                "can_send_photos": True,
                "can_send_videos": True,
                "can_send_video_notes": True,
                "can_send_voice_notes": True,
                "can_send_polls": True,
                "can_send_other_messages": True,
                "can_add_web_page_previews": True
            }
        )

        await message.answer(
            f"🔊 Мут с {target.full_name} снят."
        )

    except Exception:
        await message.answer("Не удалось снять мут.")


# ==============================
# BAN / UNBAN / KICK
# ==============================

@dp.message(Command("ban"))
async def ban_command(message: Message, command: CommandObject):
    if not await moderation_check(message):
        return

    target = await get_target(message, command.args)

    if not target:
        await message.answer("Укажи пользователя через ответ или ID.")
        return

    try:
        await bot.ban_chat_member(
            message.chat.id,
            target.id
        )

        await message.answer(
            f"🔨 {target.full_name} заблокирован."
        )

    except Exception:
        await message.answer("Не удалось заблокировать участника.")


@dp.message(Command("unban"))
async def unban_command(message: Message, command: CommandObject):
    if not await moderation_check(message):
        return

    target = await get_target(message, command.args)

    if not target:
        await message.answer("Укажи пользователя через ID.")
        return

    try:
        await bot.unban_chat_member(
            message.chat.id,
            target.id,
            only_if_banned=True
        )

        await message.answer(
            f"🔓 {target.full_name} разблокирован."
        )

    except Exception:
        await message.answer("Не удалось разблокировать участника.")


@dp.message(Command("kick"))
async def kick_command(message: Message, command: CommandObject):
    if not await moderation_check(message):
        return

    target = await get_target(message, command.args)

    if not target:
        await message.answer("Укажи пользователя через ответ или ID.")
        return

    try:
        await bot.ban_chat_member(
            message.chat.id,
            target.id
        )

        await bot.unban_chat_member(
            message.chat.id,
            target.id
        )

        await message.answer(
            f"👢 {target.full_name} исключён из группы."
        )

    except Exception:
        await message.answer("Не удалось исключить участника.")


# ==============================
# WARNINGS
# ==============================

@dp.message(Command("warn"))
async def warn_command(message: Message, command: CommandObject):
    if not await moderation_check(message):
        return

    target = await get_target(message, command.args)

    if not target:
        await message.answer("Используй ответ на сообщение или ID.")
        return

    reason = " ".join(
        (command.args or "").split()[1:]
    ) or "Причина не указана"

    register_user(target)

    with db() as conn:
        conn.execute("""
            UPDATE users
            SET warnings = warnings + 1
            WHERE user_id = ?
        """, (target.id,))

        conn.execute("""
            INSERT INTO warnings_log
            (user_id, admin_id, reason, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            target.id,
            message.from_user.id,
            reason,
            datetime.now(timezone.utc).isoformat()
        ))

    user = get_user(target.id)

    await message.answer(
        f"⚠️ Предупреждение выдано!\n\n"
        f"Участник: {target.full_name}\n"
        f"Причина: {reason}\n"
        f"Всего предупреждений: {user[4]}"
    )


@dp.message(Command("warnings"))
async def warnings_command(message: Message, command: CommandObject):
    target = await get_target(message, command.args)

    if not target:
        target = message.from_user

    user = get_user(target.id)

    if not user:
        await message.answer("Пользователь не найден.")
        return

    with db() as conn:
        records = conn.execute("""
            SELECT reason, created_at
            FROM warnings_log
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 10
        """, (target.id,)).fetchall()

    result = (
        f"⚠️ ПРЕДУПРЕЖДЕНИЯ\n\n"
        f"Участник: {target.full_name}\n"
        f"Всего: {user[4]}\n\n"
    )

    if records:
        for index, record in enumerate(records, 1):
            result += f"{index}. {record[0]}\n"
    else:
        result += "Предупреждений нет."

    await message.answer(result)


@dp.message(Command("clearwarns"))
async def clearwarns_command(
    message: Message,
    command: CommandObject
):
    if not await moderation_check(message):
        return

    target = await get_target(message, command.args)

    if not target:
        await message.answer("Укажи пользователя через ответ или ID.")
        return

    with db() as conn:
        conn.execute(
            "UPDATE users SET warnings = 0 WHERE user_id = ?",
            (target.id,)
        )

        conn.execute(
            "DELETE FROM warnings_log WHERE user_id = ?",
            (target.id,)
        )

    await message.answer(
        f"✅ Предупреждения {target.full_name} очищены."
    )


# ==============================
# ERROR HANDLING AND STARTUP
# ==============================

async def main():
    init_db()

    logging.info("JOJO OVERDRIVE BOT STARTING")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        ai.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped")

