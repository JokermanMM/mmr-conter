import asyncio
import os
import logging
from telegram import Update, InputMediaPhoto, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dota_client import DotaClient
from data_manager import DataManager
from dotenv import load_dotenv
from aiohttp import web
import aiohttp
import io
from PIL import Image

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
    """Calculate Dota 2 rank tier and local image ID from MMR."""
    if mmr is None:
        return "Неизвестно", None
    
    tiers = [
        ("Рекрут", 0, 0),
        ("Страж", 770, 1),
        ("Рыцарь", 1540, 2),
        ("Герой", 2310, 3),
        ("Легенда", 3080, 4),
        ("Властелин", 3850, 5),
        ("Божество", 4620, 6)
    ]
    
    if mmr >= 5420:
        return "Титан", 36
        
    current_tier = tiers[0]
    for tier in tiers:
        if mmr >= tier[1]:
            current_tier = tier
        else:
            break
            
    name, base_mmr, tier_idx = current_tier
    
    # Calculate stars (154 MMR per star)
    stars = min(5, max(1, int((mmr - base_mmr) / 154) + 1))
    image_id = tier_idx * 5 + stars
    
    return f"{name} {stars}", image_id

async def generate_composite_image(hero_img_url, rank_icon_id, items_urls=None, neutral_url=None):
    """Downloads hero image and overlays the rank icon, plus appends items bar underneath."""
    if not hero_img_url:
        return None
        
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(hero_img_url) as resp:
                if resp.status != 200:
                    return None
                hero_data = await resp.read()
            
            items_data = []
            if items_urls:
                for url in items_urls:
                    if url:
                        try:
                            r = await session.get(url)
                            items_data.append(await r.read() if r.status == 200 else None)
                        except:
                            items_data.append(None)
                    else:
                        items_data.append(None)
            
            neutral_data = None
            if neutral_url:
                try:
                    r = await session.get(neutral_url)
                    if r.status == 200:
                        neutral_data = await r.read()
                except:
                    pass
                
        hero_img = Image.open(io.BytesIO(hero_data)).convert("RGBA")
        
        # Determine items layout area
        # Hero image is typically 256x144. We will add a bottom bar of ~35px height.
        has_items = any(items_data) or neutral_data
        item_bar_height = 36 if has_items else 0
        final_height = hero_img.height + item_bar_height
        
        # Create new canvas
        canvas = Image.new("RGBA", (hero_img.width, final_height), (20, 20, 20, 255))
        canvas.paste(hero_img, (0, 0))
        
        # Paste Rank Icon (bottom right of the HERO area, not canvas)
        if rank_icon_id:
            rank_path = os.path.join("media", "ranks", f"{rank_icon_id}.png")
            if os.path.exists(rank_path):
                rank_img = Image.open(rank_path).convert("RGBA")
                
                # Scale rank image to ~40% of hero image height
                target_height = int(hero_img.height * 0.45)
                aspect_ratio = rank_img.width / rank_img.height
                target_width = int(target_height * aspect_ratio)
                rank_img = rank_img.resize((target_width, target_height), Image.Resampling.LANCZOS)
                
                # Paste in bottom right corner of the HERO image (above item bar)
                padding = int(hero_img.height * 0.05)
                x = hero_img.width - rank_img.width - padding
                y = hero_img.height - rank_img.height - padding
                
                canvas.paste(rank_img, (x, y), rank_img)
                
        # Paste Items
        if has_items:
            # We have 6 main items and 1 neutral. Let's arrange them.
            # Total width = 256. 6 items * 34 wide = 204. Gap = 2. 
            item_w = 34
            item_h = int(item_w * 64/85) # native aspect ratio is roughly 85:64
            
            start_x = 4
            start_y = hero_img.height + (item_bar_height - item_h) // 2
            
            for i, img_bytes in enumerate(items_data):
                if img_bytes:
                    item_img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
                    item_img = item_img.resize((item_w, item_h), Image.Resampling.LANCZOS)
                    canvas.paste(item_img, (start_x + i * (item_w + 2), start_y), item_img)
            
            if neutral_data:
                neut_img = Image.open(io.BytesIO(neutral_data)).convert("RGBA")
                neut_w = 30
                neut_h = int(neut_w * 64/85)
                neut_img = neut_img.resize((neut_w, neut_h), Image.Resampling.LANCZOS)
                
                # Create a radial mask or just paste it
                n_x = hero_img.width - neut_w - 4
                n_y = hero_img.height + (item_bar_height - neut_h) // 2
                canvas.paste(neut_img, (n_x, n_y), neut_img)

        output = io.BytesIO()
        # Convert back to RGB for typically sharing, or preserve RGBA
        canvas.save(output, format="PNG")
        output.seek(0)
        return output
    except Exception as e:
        logger.error(f"Error generating composite image: {e}")
        return None

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
    img_url = f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{system_name}.png" if system_name else None
    
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
        
        rank_name, rank_icon_id = get_rank_info(last_mmr)
        
        msg = (
            f"✅ **Привязано к {data['player_name']}!**\n"
            f"Отслеживание матчей запущено.\n\n"
            f"🏆 Твой ранг: **{rank_name}**\n"
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

async def test_msg_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a mock match result for testing UI tweaks."""
    chat_id = update.effective_chat.id
    
    # Example mock data
    is_win = True
    hero_id = 71  # Spirit Breaker
    kills, deaths, assists = 12, 2, 8
    gpm, xpm = 750, 820
    match_id = 7123456789
    
    hero_info = await get_hero_info(hero_id)
    hero_name = hero_info["name"]
    hero_img = hero_info["img_url"]
    
    result_emoji = "✨ ПОБЕДА ✨" if is_win else "💀 ПОРАЖЕНИЕ"
    match_type_label = " 🧪 (Тест)"
    
    user = db.get_user(chat_id)
    manual_mmr = user.get("manual_mmr") if user else 1500
    
    rank_name, rank_icon_id = get_rank_info(manual_mmr + 25)
    
    mmr_change_text = (
        f"🎰 **ММР:** `{manual_mmr + 25}` (**+25**)\n"
        f"🏆 **Ранг:** {rank_name}"
    )
    
    msg = (
        f"**{result_emoji}** 🪄 (Тест)\n\n"
        f"👾 **Герой:** {hero_name}\n"
        f"🩸 **KDA:** `12 / 2 / 8`\n"
        f"💰 **GPM:** `750` | 🎓 **XPM:** `820`\n"
        f"💰 **Networth:** `24 500`\n\n"
        f"{mmr_change_text}\n\n"
        f"🔗 [Dotabuff](https://www.dotabuff.com/matches/{match_id})"
    )
    
    # Mock items for test
    mock_items = ["https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/blink.png", None, None, None, None, None]
    
    try:
        composite_io = await generate_composite_image(hero_img, rank_icon_id, mock_items, None)
        
        if composite_io:
            await context.bot.send_photo(chat_id=chat_id, photo=composite_io, caption=msg, parse_mode="Markdown")
        elif hero_img:
            await context.bot.send_photo(chat_id=chat_id, photo=hero_img, caption=msg, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Error sending test msg: {e}")
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", disable_web_page_preview=True)

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
                nw = pm.get("net_worth", 0)
                
                items_urls = pm.get("items_urls", [])
                neutral_url = pm.get("neutral_url")
                
                result_emoji = "✨ ПОБЕДА ✨" if is_win else "💀 ПОРАЖЕНИЕ"
                
                manual_mmr = user_info.get("manual_mmr")
                matches_count = user_info.get("matches_since_calibration", 0)
                
                lobby_type = pm.get("lobby_type")
                game_mode = pm.get("game_mode")
                
                is_ranked = (lobby_type == 7)
                is_turbo = (game_mode == 23)
                is_custom = (lobby_type in [4, 15]) or (game_mode in [15, 19])
                
                if is_custom:
                    db.update_match(chat_id, match_id, user_info.get("last_mmr"))
                    continue
                
                match_type_label = ""
                if is_turbo:
                    match_type_label = " ⚡️ (Турбо)"
                elif not is_ranked:
                    match_type_label = " 🎮 (Обычный)"
                
                mmr_change_text = ""
                if manual_mmr is not None:
                    if is_ranked and not is_turbo:
                        diff = 25 if is_win else -25
                        new_manual_mmr = manual_mmr + diff
                        matches_count += 1
                        diff_sign = "+" if diff > 0 else ""
                        rank_name, rank_icon_id = get_rank_info(new_manual_mmr)
                        
                        mmr_change_text = (
                            f"🎰 **ММР:** `{new_manual_mmr}` (**{diff_sign}{diff}**)\n"
                            f"🏆 **Ранг:** {rank_name}"
                        )
                        db.update_match_and_mmr(chat_id, match_id, new_manual_mmr, matches_count)
                    else:
                        rank_name, rank_icon_id = get_rank_info(manual_mmr)
                        mmr_change_text = (
                            f"🎰 **ММР:** `{manual_mmr}` (без изменений)\n"
                            f"🏆 **Ранг:** {rank_name}"
                        )
                        db.update_match(chat_id, match_id, user_info.get("last_mmr"))
                    
                    if matches_count >= 10:
                        mmr_change_text += "\n\n🔄 *Пора обновить точный MMR: /set_mmr*"
                else:
                    new_mmr = data.get("mmr_estimate")
                    rank_icon_id = None
                    mmr_change_text = f"📈 Оценка MMR: `{new_mmr}`\n⚠️ *Установи MMR: /set_mmr*"
                    db.update_match(chat_id, match_id, new_mmr)
                
                formatted_nw = f"{nw:,}".replace(",", " ")
                
                msg = (
                    f"**{result_emoji}{match_type_label}**\n\n"
                    f"👾 **Герой:** {hero_name}\n"
                    f"🩸 **KDA:** `{kills} / {deaths} / {assists}`\n"
                    f"💰 **GPM:** `{gpm}` | 🎓 **XPM:** `{xpm}`\n"
                    f"💰 **Networth:** `{formatted_nw}`\n\n"
                    f"{mmr_change_text}\n\n"
                    f"🔗 [Dotabuff](https://www.dotabuff.com/matches/{match_id})"
                )
                
                try:
                    composite_io = await generate_composite_image(hero_img, rank_icon_id, items_urls, neutral_url)
                    
                    if composite_io:
                        await context.bot.send_photo(chat_id=int(chat_id), photo=composite_io, caption=msg, parse_mode="Markdown")
                    elif hero_img:
                        await context.bot.send_photo(chat_id=int(chat_id), photo=hero_img, caption=msg, parse_mode="Markdown")
                    else:
                        await context.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode="Markdown", disable_web_page_preview=True)
                except Exception as e:
                    logger.error(f"Error sending match msg: {e}")
                    await context.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode="Markdown", disable_web_page_preview=True)
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
    application.add_handler(CommandHandler("test", test_msg_command))
    
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
            BotCommand("set_mmr", "Обновить точный MMR (нужно написать /set_mmr <ММР>)"),
            BotCommand("test", "Тест: отправить пример уведомления о матче")
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
