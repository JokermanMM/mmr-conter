import asyncio
import os
import logging
from telegram import Update, InputMediaPhoto
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from stratz_client import StratzClient
from data_manager import DataManager
from dotenv import load_dotenv
from aiohttp import web

# Load .env for local development
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Config from environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
STRATZ_TOKEN = os.environ.get("STRATZ_TOKEN")
PORT = int(os.environ.get("PORT", 8080)) # Render provides PORT

if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found in environment variables!")
    exit(1)

db = DataManager()
stratz = StratzClient(STRATZ_TOKEN)

# --- Web Server for Render Free Tier ---
async def handle_ping(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    app = web.Application()
    app.add_routes([web.get('/', handle_ping)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

# --- Bot Commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_msg = (
        "👋 **Добро пожаловать в Dota 2 MMR Counter!**\n\n"
        "Я буду отслеживать твои матчи и присылать изменения ММР.\n\n"
        "**Как привязать аккаунт:**\n"
        "1. В Steam нажми на ник -> **«Об аккаунте»** (скрин 1).\n"
        "2. Скопируй **Steam ID** под логином (скрин 2).\n"
        "3. В Dota 2 нажми настройки -> Сообщество -> **«Общедоступная история матчей»** (скрин 3).\n\n"
        "После присылай ID командой:\n"
        "`/set_id <твой_id>`"
    )
    
    # Send screenshots if they exist
    media = []
    for i in range(1, 4):
        path = os.path.join("media", f"step{i}.png")
        if os.path.exists(path):
            media.append(InputMediaPhoto(open(path, 'rb'), caption=f"Шаг {i}"))
    
    if media:
        try:
            await context.bot.send_media_group(chat_id=update.effective_chat.id, media=media)
        except Exception as e:
            logger.error(f"Error sending media: {e}")

    await update.message.reply_text(welcome_msg, parse_mode="Markdown")

async def set_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Укажи ID: `/set_id 12345678`", parse_mode="Markdown")
        return
    
    try:
        raw_id = context.args[0]
        # Remove any leading / if user made a mistake
        raw_id = raw_id.strip('/')
        steam_id = int(raw_id)
        
        # Convert SteamID64 to Account ID if necessary
        if steam_id > 76561197960265728:
            steam_id = steam_id - 76561197960265728

        chat_id = update.effective_chat.id
        await update.message.reply_text(f"⏳ Проверяю профиль `{steam_id}` в базе Stratz...")
        
        data = await stratz.get_latest_match(steam_id)
        if not data:
            await update.message.reply_text(
                "❌ **Игрок не найден в Stratz.**\n\n"
                "**Почему это может быть:**\n"
                "1. Профиль скрыт настройками приватности в Доте (см. шаг 3).\n"
                "2. Stratz еще не проиндексировал тебя.\n\n"
                "**Что делать:**\n"
                "Зайди на [stratz.com/players/" + str(steam_id) + "](https://stratz.com/players/" + str(steam_id) + ") и убедись, что там видны твои данные. Это заставит базу обновиться.",
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            return
        
        last_match_id = data["match"]["id"] if data["match"] else None
        last_mmr = data["player_match"]["afterMmr"] if data["player_match"] else None
        
        db.set_user(chat_id, steam_id, last_match_id, last_mmr)
        
        msg = (
            f"✅ **Привязано к {data['player_name']}!**\n"
            f"Отслеживание запущено.\n"
            f"Текущий MMR: `{last_mmr if last_mmr else 'Скрыт'}`"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом. Пример: `/set_id 12345678`", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Set ID error: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуй позже.", parse_mode="Markdown")

async def monitor_matches(context: ContextTypes.DEFAULT_TYPE):
    """Background task to poll for new matches."""
    users = db.get_all_users()
    for chat_id, user_info in users.items():
        try:
            data = await stratz.get_latest_match(user_info["steam_id"])
            if not data or not data["match"] or not data["player_match"]:
                continue
            
            match_id = data["match"]["id"]
            if match_id != user_info.get("last_match_id"):
                # New match found!
                pm = data["player_match"]
                hero_name = pm["hero"]["displayName"]
                is_win = pm["isVictory"]
                new_mmr = pm["afterMmr"]
                kills = pm["numKills"]
                deaths = pm["numDeaths"]
                assists = pm["numAssists"]
                
                result_emoji = "🏆" if is_win else "💀"
                result_text = "ПОБЕДА" if is_win else "ПОРАЖЕНИЕ"
                
                mmr_text = f"📈 MMR: `{new_mmr}`" if new_mmr else "📈 MMR: `Скрыт`"
                if new_mmr and user_info.get("last_mmr"):
                    diff = new_mmr - user_info["last_mmr"]
                    if diff != 0:
                        diff_sign = "+" if diff > 0 else ""
                        mmr_text += f" (**{diff_sign}{diff}**)"
                
                msg = (
                    f"{result_emoji} **Обновление матча Dota 2**\n\n"
                    f"👤 Игрок: **{data['player_name']}**\n"
                    f"📊 Результат: **{result_text}**\n"
                    f"🦸 Герой: **{hero_name}**\n"
                    f"⚔️ Статистика: `{kills}/{deaths}/{assists}`\n"
                    f"{mmr_text}"
                )
                
                await context.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode="Markdown")
                # Update DB
                db.update_match(chat_id, match_id, new_mmr)
        except Exception as e:
            logger.error(f"Error monitoring {chat_id}: {e}")

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug command to test Stratz API directly."""
    steam_id = 299539763  # Default test ID
    if context.args:
        try:
            steam_id = int(context.args[0])
            if steam_id > 76561197960265728:
                steam_id = steam_id - 76561197960265728
        except ValueError:
            pass
    
    await update.message.reply_text(f"🔧 Тестирую API для ID: {steam_id}...")
    
    token_status = f"Token set: {bool(STRATZ_TOKEN)}, length: {len(STRATZ_TOKEN) if STRATZ_TOKEN else 0}"
    await update.message.reply_text(f"🔑 {token_status}")
    
    result = await stratz.raw_query(steam_id)
    # Split long message
    for i in range(0, len(result), 4000):
        await update.message.reply_text(result[i:i+4000])

async def main():
    # Start web server
    await start_web_server()
    
    # Start bot
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("set_id", set_id_command))
    application.add_handler(CommandHandler("debug", debug_command))
    
    # Job queue for polling
    job_queue = application.job_queue
    job_queue.run_repeating(monitor_matches, interval=180, first=10)
    
    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        while True:
            await asyncio.sleep(3600)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
