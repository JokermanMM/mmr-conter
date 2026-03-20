import asyncio
import os
import logging
from telegram import Update
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
        "Для начала привяжи свой **Steam ID**:\n"
        "`/set_id 12345678`"
    )
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
            await update.message.reply_text("❌ Игрок не найден.", parse_mode="Markdown")
            return
        
        last_match_id = data["match"]["id"] if data["match"] else None
        last_mmr = data["player_match"]["afterMmr"] if data["player_match"] else None
        db.set_user(chat_id, steam_id, last_match_id, last_mmr)
        
        await update.message.reply_text(f"✅ Привязано к {data['player_name']}!", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Set ID error: {e}")
        await update.message.reply_text("❌ Ошибка при привязке.", parse_mode="Markdown")

async def monitor_matches(context: ContextTypes.DEFAULT_TYPE):
    users = db.get_all_users()
    for chat_id, user_info in users.items():
        data = await stratz.get_latest_match(user_info["steam_id"])
        if not data or not data["match"] or not data["player_match"]:
            continue
        
        match_id = data["match"]["id"]
        if match_id != user_info.get("last_match_id"):
            pm = data["player_match"]
            res = "🏆 ПОБЕДА" if pm["isVictory"] else "💀 ПОРАЖЕНИЕ"
            mmr_diff = ""
            if pm["afterMmr"] and user_info.get("last_mmr"):
                diff = pm["afterMmr"] - user_info["last_mmr"]
                mmr_diff = f" (**{'+' if diff > 0 else ''}{diff}**)"
            
            msg = (
                f"{res} **Обновление матча**\n"
                f"👤 Игрок: **{data['player_name']}**\n"
                f"🦸 Герой: **{pm['hero']['displayName']}**\n"
                f"📈 MMR: `{pm['afterMmr']}`{mmr_diff}"
            )
            await context.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode="Markdown")
            db.update_match(chat_id, match_id, pm["afterMmr"])

async def main():
    # Start web server
    await start_web_server()
    
    # Start bot
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("set_id", set_id_command))
    
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
