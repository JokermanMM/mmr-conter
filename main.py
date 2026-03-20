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
        "**Как найти свой Steam ID:**\n"
        "1. В Steam нажми на свой ник в правом верхнем углу.\n"
        "2. Выбери пункт **«Об аккаунте»** (скриншот 1).\n"
        "3. Скопируй **Steam ID**, который указан под твоим логином (скриншот 2).\n\n"
        "После этого пришли мне ID командой:\n"
        "`/set_id <твой_id>`"
    )
    
    # Send screenshots if they exist
    step1_path = os.path.join("media", "step1.png")
    step2_path = os.path.join("media", "step2.png")
    
    if os.path.exists(step1_path) and os.path.exists(step2_path):
        try:
            # We must open files in each call or use a more complex way for persistent files
            with open(step1_path, 'rb') as f1, open(step2_path, 'rb') as f2:
                await context.bot.send_media_group(
                    chat_id=update.effective_chat.id,
                    media=[
                        InputMediaPhoto(f1, caption="1. Меню «Об аккаунте»"),
                        InputMediaPhoto(f2, caption="2. Где находится Steam ID")
                    ]
                )
        except Exception as e:
            logger.error(f"Error sending media: {e}")

    await update.message.reply_text(welcome_msg, parse_mode="Markdown")

async def set_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Укажи ID: `/set_id 12345678`", parse_mode="Markdown")
        return
    
    try:
        steam_id = int(context.args[0])
        
        # Convert SteamID64 to Account ID if necessary
        if steam_id > 76561197960265728:
            steam_id = steam_id - 76561197960265728
            logger.info(f"Converted SteamID64 to Account ID: {steam_id}")

        chat_id = update.effective_chat.id
        data = await stratz.get_latest_match(steam_id)
        if not data:
            await update.message.reply_text("❌ Игрок не найден. Убедись, что 'Общедоступная история матчей' включена в игре.", parse_mode="Markdown")
            return
        
        last_match_id = data["match"]["id"] if data["match"] else None
        last_mmr = data["player_match"]["afterMmr"] if data["player_match"] else None
        
        db.set_user(chat_id, steam_id, last_match_id, last_mmr)
        
        msg = (
            f"✅ **Привязано к {data['player_name']}!**\n"
            f"Отслеживание запущено. Последний матч: `{last_match_id}`\n"
            f"Текущий MMR: `{last_mmr if last_mmr else 'Скрыт'}`"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Set ID error: {e}")
        await update.message.reply_text("❌ Ошибка при привязке. Проверь ID.", parse_mode="Markdown")

async def monitor_matches(context: ContextTypes.DEFAULT_TYPE):
    """Background task to poll for new matches."""
    users = db.get_all_users()
    for chat_id, user_info in users.items():
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
            
            try:
                await context.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode="Markdown")
                # Update DB
                db.update_match(chat_id, match_id, new_mmr)
            except Exception as e:
                logger.error(f"Failed to send message to {chat_id}: {e}")

async def main():
    # Start web server
    await start_web_server()
    
    # Start bot
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("set_id", set_id_command))
    
    # Job queue for polling (every 3 minutes)
    job_queue = application.job_queue
    job_queue.run_repeating(monitor_matches, interval=180, first=10)
    
    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        # Keep running until cancelled
        while True:
            await asyncio.sleep(3600)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
