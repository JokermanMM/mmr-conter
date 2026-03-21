import asyncio
import os
import logging
from telegram import Update, InputMediaPhoto, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dota_client import DotaClient
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
PORT = int(os.environ.get("PORT", 8080))

if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found in environment variables!")
    exit(1)

db = DataManager()
dota = DotaClient()

def get_rank_info(mmr):
    """Calculate Dota 2 rank tier and emoji from MMR."""
    if mmr is None:
        return "Unknown", "❓"
    
    tiers = [
        ("Herald", "🥉", 0),
        ("Guardian", "🛡️", 770),
        ("Crusader", "⚔️", 1540),
        ("Archon", "⚡", 2310),
        ("Legend", "💎", 3080),
        ("Ancient", "🟣", 3850),
        ("Divine", "👑", 4620),
        ("Immortal", "🔥", 5420)
    ]
    
    current_tier = tiers[0]
    for tier in tiers:
        if mmr >= tier[2]:
            current_tier = tier
        else:
            break
            
    name, emoji, base_mmr = current_tier
    
    if name == "Immortal":
        return name, emoji
        
    # Calculate stars (154 MMR per star)
    stars = min(5, max(1, int((mmr - base_mmr) / 154) + 1))
    return f"{name} {stars}", emoji

# Global hero cache for display
hero_display_cache = {}

async def get_hero_info(hero_id: int) -> dict:
    """Get hero name and image URL."""
    if hero_id in hero_display_cache:
        return hero_display_cache[hero_id]
    
    hero_data = await dota.get_hero_data(hero_id)
    name = hero_data.get("localized_name", f"Hero #{hero_id}")
    
    # Internal name like npc_dota_hero_antimage -> antimage
    system_name = hero_data.get("name", "").replace("npc_dota_hero_", "")
    img_url = f"https://api.opendota.com/apps/dota2/images/heroes/{system_name}_full.png" if system_name else None
    
    info = {"name": name, "img_url": img_url}
    hero_display_cache[hero_id] = info
    return info

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
        raw_id = context.args[0].strip('/')
        steam_id = int(raw_id)
        
        # Convert SteamID64 to Account ID if necessary
        if steam_id > 76561197960265728:
            steam_id = steam_id - 76561197960265728

        chat_id = update.effective_chat.id
        await update.message.reply_text(f"⏳ Проверяю профиль `{steam_id}`...", parse_mode="Markdown")
        
        data = await dota.get_latest_match(steam_id)
        if not data:
            await update.message.reply_text(
                "❌ **Игрок не найден.**\n\n"
                "**Что делать:**\n"
                "1. Убедись, что ID верный.\n"
                "2. Включи «Общедоступную историю матчей» в настройках Доты.\n"
                "3. Зайди на [opendota.com/players/" + str(steam_id) + "](https://www.opendota.com/players/" + str(steam_id) + ") чтобы обновить профиль.\n"
                "4. Подожди 2-3 минуты и попробуй снова.",
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            return
        
        last_match_id = data["match"]["id"] if data["match"] else None
        last_mmr = data.get("mmr_estimate")
        
        db.set_user(chat_id, steam_id, last_match_id, last_mmr)
        
        rank_name, rank_emoji = get_rank_info(last_mmr)
        
        msg = (
            f"✅ **Привязано к {data['player_name']}!**\n"
            f"Отслеживание матчей запущено.\n\n"
            f"🏅 Твой ранг: {rank_emoji} **{rank_name}**\n"
            f"📈 Оценка MMR: `{last_mmr if last_mmr else 'Неизвестно'}`\n\n"
            f"⚠️ **Важно:** Чтобы я считал текущий ММР точно (±25), "
            f"введи свой реальный MMR командой:\n`/set_mmr <число>`"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом. Пример: `/set_id 12345678`", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Set ID error: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуй позже.", parse_mode="Markdown")

async def set_mmr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Укажи свой текущий MMR: `/set_mmr 1695`", parse_mode="Markdown")
        return
    
    try:
        mmr = int(context.args[0])
        chat_id = update.effective_chat.id
        user = db.get_user(chat_id)
        if not user:
            await update.message.reply_text("❌ Сначала привяжи аккаунт с помощью `/set_id`.", parse_mode="Markdown")
            return
            
        db.set_manual_mmr(chat_id, mmr)
        await update.message.reply_text(
            f"✅ MMR успешно установлен на `{mmr}`!\n"
            f"Теперь я буду автоматически считать +25/-25 за каждый матч.", 
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ MMR должен быть числом. Пример: `/set_mmr 1695`", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Set MMR error: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуй позже.", parse_mode="Markdown")

async def monitor_matches(context: ContextTypes.DEFAULT_TYPE):
    """Background task to poll for new matches."""
    users = db.get_all_users()
    for chat_id, user_info in users.items():
        try:
            steam_id = user_info["steam_id"]
            
            # Force OpenDota to fetch data from Steam API to avoid long delays
            await dota.refresh_player(steam_id)
            # Give OpenDota a second to process the parse
            await asyncio.sleep(2)
            
            data = await dota.get_latest_match(steam_id)
            if not data or not data["match"] or not data["player_match"]:
                continue
            
            match_id = data["match"]["id"]
            if match_id != user_info.get("last_match_id"):
                # New match found!
                pm = data["player_match"]
                hero_id = pm.get("hero_id", 0)
                hero_info = await get_hero_info(hero_id)
                hero_name = hero_info["name"]
                hero_img = hero_info["img_url"]
                
                is_win = pm["isVictory"]
                kills = pm["numKills"]
                deaths = pm["numDeaths"]
                assists = pm["numAssists"]
                gpm = pm.get("gold_per_min", 0)
                xpm = pm.get("xp_per_min", 0)
                
                result_emoji = "🟩 ПОБЕДА" if is_win else "🟥 ПОРАЖЕНИЕ"
                
                manual_mmr = user_info.get("manual_mmr")
                matches_count = user_info.get("matches_since_calibration", 0)
                
                mmr_change_text = ""
                if manual_mmr is not None:
                    diff = 25 if is_win else -25
                    new_manual_mmr = manual_mmr + diff
                    matches_count += 1
                    
                    diff_sign = "+" if diff > 0 else ""
                    rank_name, rank_emoji = get_rank_info(new_manual_mmr)
                    
                    mmr_change_text = (
                        f"📊 **MMR:** `{new_manual_mmr}` (**{diff_sign}{diff}**)\n"
                        f"🏅 **Ранг:** {rank_emoji} {rank_name}"
                    )
                    
                    if matches_count >= 10:
                        mmr_change_text += "\n\n🔄 *Пора обновить точный MMR: /set_mmr*"
                    
                    db.update_match_and_mmr(chat_id, match_id, new_manual_mmr, matches_count)
                else:
                    new_mmr = data.get("mmr_estimate")
                    mmr_change_text = f"📈 Оценка MMR: `{new_mmr}`\n⚠️ *Установи MMR: /set_mmr*"
                    db.update_match(chat_id, match_id, new_mmr)
                
                msg = (
                    f"**{result_emoji}**\n\n"
                    f"🦸 **Герой:** {hero_name}\n"
                    f"⚔️ **KDA:** `{kills} / {deaths} / {assists}`\n"
                    f"💰 **GPM:** `{gpm}` | ✨ **XPM:** `{xpm}`\n\n"
                    f"{mmr_change_text}\n\n"
                    f"🔗 [OpenDota](https://www.opendota.com/matches/{match_id}) | "
                    f"[Dotabuff](https://www.dotabuff.com/matches/{match_id})"
                )
                
                try:
                    if hero_img:
                        await context.bot.send_photo(
                            chat_id=int(chat_id),
                            photo=hero_img,
                            caption=msg,
                            parse_mode="Markdown"
                        )
                    else:
                        await context.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Error sending match msg: {e}")
                    await context.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error monitoring {chat_id}: {e}")


async def main():
    # Start web server
    await start_web_server()
    
    # Start bot
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("set_id", set_id_command))
    application.add_handler(CommandHandler("set_mmr", set_mmr_command))
    
    # Job queue for polling (every 3 minutes)
    job_queue = application.job_queue
    job_queue.run_repeating(monitor_matches, interval=180, first=10)
    
    async with application:
        await application.initialize()
        await application.start()
        
        # Set up the menu button and command autocomplete
        await application.bot.set_my_commands([
            BotCommand("start", "Инструкция и главное меню"),
            BotCommand("set_id", "Привязать профиль (нужно написать /set_id <ID>)"),
            BotCommand("set_mmr", "Обновить точный MMR (нужно написать /set_mmr <ММР>)")
        ])
        
        # Wait a few seconds to let the old Render instance shut down completely to avoid Conflict errors
        await asyncio.sleep(5)
        await application.updater.start_polling(drop_pending_updates=True)
        while True:
            await asyncio.sleep(3600)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
