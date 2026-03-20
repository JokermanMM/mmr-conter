import asyncio
import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from stratz_client import StratzClient
from data_manager import DataManager
from dotenv import load_dotenv

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

if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found in environment variables!")
    exit(1)

db = DataManager()
stratz = StratzClient(STRATZ_TOKEN)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_msg = (
        "👋 **Добро пожаловать в Dota 2 MMR Counter!**\n\n"
        "Я буду отслеживать твои матчи и присылать изменения ММР.\n\n"
        "Для начала привяжи свой **Steam ID** (его можно взять из ссылки на твой Dotabuff):\n"
        "`/set_id 12345678`"
    )
    await update.message.reply_text(welcome_msg, parse_mode="Markdown")

async def set_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Укажи ID: `/set_id 12345678`", parse_mode="Markdown")
        return
    
    try:
        steam_id = int(context.args[0])
        chat_id = update.effective_chat.id
        
        # Verify Steam ID with Stratz
        data = await stratz.get_latest_match(steam_id)
        if not data:
            await update.message.reply_text("❌ Игрок не найден. Убедись, что 'Общедоступная история матчей' включена в игре.", parse_mode="Markdown")
            return
        
        player_name = data["player_name"]
        match = data["match"]
        player_match = data["player_match"]
        
        last_match_id = match["id"] if match else None
        last_mmr = player_match["afterMmr"] if player_match and player_match.get("afterMmr") else None
        
        db.set_user(chat_id, steam_id, last_match_id, last_mmr)
        
        msg = (
            f"✅ **Привязано к {player_name}!**\n"
            f"Отслеживание запущено. Последний матч: `{last_match_id}`\n"
            f"Текущий MMR: `{last_mmr if last_mmr else 'Скрыт'}`"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Неверный формат Steam ID.", parse_mode="Markdown")

async def monitor_matches(context: ContextTypes.DEFAULT_TYPE):
    """Background task to poll for new matches."""
    users = db.get_all_users()
    for chat_id, user_info in users.items():
        steam_id = user_info["steam_id"]
        last_match_id = user_info.get("last_match_id")
        last_mmr = user_info.get("last_mmr")
        
        data = await stratz.get_latest_match(steam_id)
        if not data or not data["match"] or not data["player_match"]:
            continue
        
        match = data["match"]
        player_match = data["player_match"]
        match_id = match["id"]
        
        if match_id != last_match_id:
            # New match found!
            hero_name = player_match["hero"]["displayName"]
            is_win = player_match["isVictory"]
            new_mmr = player_match["afterMmr"]
            kills = player_match["numKills"]
            deaths = player_match["numDeaths"]
            assists = player_match["numAssists"]
            
            result_emoji = "🏆" if is_win else "💀"
            result_text = "ПОБЕДА" if is_win else "ПОРАЖЕНИЕ"
            
            mmr_text = f"📈 MMR: `{new_mmr}`" if new_mmr else "📈 MMR: `Скрыт`"
            if new_mmr and last_mmr:
                diff = new_mmr - last_mmr
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

if __name__ == '__main__':
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("set_id", set_id_command))
    
    # Job queue for polling (every 3 minutes)
    job_queue = application.job_queue
    job_queue.run_repeating(monitor_matches, interval=180, first=10)
    
    logger.info("Bot started...")
    application.run_polling()
