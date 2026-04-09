import asyncio
# CI/CD Test: Checking automated restart logic
import os
import logging
from datetime import time, datetime, timezone, timedelta
from telegram import Update, InputMediaPhoto, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dota_client import DotaClient
from data_manager import DataManager
from dotenv import load_dotenv
from aiohttp import web
import aiohttp
import io

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False
    logger.warning("Pillow not installed. Hero images and graphs will be disabled.")

# Config from environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
STRATZ_TOKEN = os.environ.get("STRATZ_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")  # Your Telegram chat_id
PORT = int(os.environ.get("PORT", 8080))

# Moscow timezone (UTC+3)
MSK = timezone(timedelta(hours=3))

if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found in environment variables!")
    exit(1)

db = DataManager()
dota = DotaClient(stratz_token=STRATZ_TOKEN)

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

async def generate_composite_image(hero_short_name, rank_icon_id, items_urls=None, neutral_url=None, item_purchases=None, abilities=None, ability_cache=None, stats=None):
    """
    Generates a premium modernized match card.
    Layout: 900x500
    - Left (0-300): Vertical hero banner.
    - Right (300-900): Stats, Item Timings, and Talents.
    """
    if not HAS_PILLOW:
        return None

    W, H = 900, 280
    canvas = Image.new("RGBA", (W, H), (15, 15, 18, 255))
    draw = ImageDraw.Draw(canvas)
    
    # Load fonts
    font_bold, font_reg, font_sm, font_tiny = None, None, None, None
    font_paths = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "arial.ttf"]
    for path in font_paths:
        try:
            font_bold = ImageFont.truetype(path, 28)
            font_reg = ImageFont.truetype(path, 18)
            font_sm = ImageFont.truetype(path, 14)
            font_tiny = ImageFont.truetype(path, 11)
            break
        except: continue
    if not font_bold:
        font_bold = ImageFont.load_default()
        font_reg, font_sm, font_tiny = font_bold, font_bold, font_bold

    try:
        # Headers to bypass Cloudflare/bot detection
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            # 1. Background/Hero Banner (Portraits)
            hero_banner = None
            if hero_short_name:
                # Use reliable Steam CDN for standard portraits
                banner_url = f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{hero_short_name}.png"
                try:
                    async with session.get(banner_url, timeout=10.0) as resp:
                        if resp.status == 200:
                            hero_banner = Image.open(io.BytesIO(await resp.read())).convert("RGBA")
                except Exception as e:
                    logger.error(f"Error loading hero portrait: {e}")
            
            # 2. Rank Icon
            rank_img = None
            if rank_icon_id:
                rank_path = os.path.join("media", "ranks", f"{rank_icon_id}.png")
                if os.path.exists(rank_path):
                    rank_img = Image.open(rank_path).convert("RGBA")

            # 3. Item Icons
            items_imgs = []
            item_timings = []
            if items_urls:
                # Find timing for each item in inventory
                purchase_map = {}
                if item_purchases:
                    for p in item_purchases:
                        iid = p.get("itemId")
                        if iid not in purchase_map:
                            purchase_map[iid] = p.get("time")

                for i, url in enumerate(items_urls):
                    if url:
                        async with session.get(url) as r:
                            items_imgs.append(Image.open(io.BytesIO(await r.read())).convert("RGBA") if r.status == 200 else None)
                        
                        # Get timing
                        item_id = stats.get("item_ids", [None]*6)[i]
                        item_timings.append(purchase_map.get(item_id))
                    else:
                        items_imgs.append(None)
                        item_timings.append(None)
            
            neutral_img = None
            if neutral_url:
                async with session.get(neutral_url) as r:
                    if r.status == 200:
                        neutral_img = Image.open(io.BytesIO(await r.read())).convert("RGBA")

        # --- DRAWING ---
        
        # Draw Hero Portrait (Left Panel)
        if hero_banner:
            # Target box is approx 280x250. Scale keeping aspect ratio, then crop center
            target_h = 250
            target_w = int(hero_banner.width * (target_h / hero_banner.height))
            hero_banner = hero_banner.resize((target_w, target_h), Image.Resampling.LANCZOS)
            
            # Crop to 280 width (centered)
            left = (target_w - 280) // 2
            hero_banner = hero_banner.crop((left, 0, left + 280, target_h))
            
            # Paste symmetrically at 10, 15
            canvas.paste(hero_banner, (10, 15), hero_banner)
        
        # Draw Header info
        res_text = stats.get("result_text", "МАТЧ")
        res_text = res_text.replace("✨", "").replace("💀", "").strip()
        
        res_color = (76, 175, 80) if "ПОБЕДА" in stats.get("result_text", "") else (244, 67, 54)
        draw.text((315, 20), res_text, fill=res_color, font=font_bold)
        
        # Draw Rank dynamically next to header text
        if rank_img:
            try:
                res_w = int(draw.textlength(res_text, font=font_bold))
            except AttributeError:
                res_w = len(res_text) * 20
                
            r_w = 55
            r_h = int(rank_img.height * (r_w / rank_img.width))
            rank_img = rank_img.resize((r_w, r_h), Image.Resampling.LANCZOS)
            canvas.paste(rank_img, (315 + res_w + 10, 5), rank_img)
        
        draw.text((315, 50), stats.get("hero_name", "Герой"), fill=(200, 200, 210), font=font_reg)
        
        # Draw Main Stats Dashboard
        stats_y = 90
        stats_x = 315
        def draw_stat(x, y, label, value, color=(255, 255, 255)):
            draw.text((x, y), label, fill=(150, 150, 160), font=font_tiny)
            draw.text((x, y + 15), str(value), fill=color, font=font_reg)

        draw_stat(stats_x, stats_y, "KDA", f"{stats.get('kills')}/{stats.get('deaths')}/{stats.get('assists')}")
        draw_stat(stats_x + 85, stats_y, "GPM/XPM", f"{stats.get('gpm')}/{stats.get('xpm')}")
        draw_stat(stats_x + 195, stats_y, "NW 10:00", f"{stats.get('nw_10', 0):,}".replace(",", " "))
        draw_stat(stats_x + 285, stats_y, "NET WORTH", f"{stats.get('net_worth', 0):,}".replace(",", " "))
        
        # Add MMR back to the stats row
        mmr_val = stats.get("new_mmr")
        mmr_diff = stats.get("mmr_diff")
        if mmr_val:
            draw_stat(stats_x + 395, stats_y, "MMR", str(mmr_val))
            if mmr_diff:
                diff_str = f"({'+' if mmr_diff > 0 else ''}{mmr_diff})"
                diff_col = (76, 175, 80) if mmr_diff > 0 else (244, 67, 54)
                
                # Measure text width to add a proper gap before the diff
                try:
                    mmr_val_w = int(draw.textlength(str(mmr_val), font=font_reg))
                except AttributeError:
                    mmr_val_w = len(str(mmr_val)) * 11
                
                # 6 pixels is a nice small space
                draw.text((stats_x + 395 + mmr_val_w + 6, stats_y + 15), diff_str, fill=diff_col, font=font_reg)

        draw_stat(stats_x + 510, stats_y, "DURATION", stats.get("duration", "00:00"))

        # Draw Items with Timings
        draw.text((315, 155), "ПРЕДМЕТЫ И ТАЙМИНГИ", fill=(100, 100, 110), font=font_tiny)
        item_x = 315
        item_y = 180
        icon_w, icon_h = 60, 45
        
        # Sort items by timing for the timeline
        items_data = list(zip(items_imgs, item_timings))
        # Sort by timing (items with None timing go to the end)
        items_data.sort(key=lambda x: x[1] if x[1] is not None else float('inf'))
        
        for i, (item_icon, t) in enumerate(items_data):
            # Draw Slot Frame
            draw.rectangle([item_x + i*75, item_y, item_x + i*75 + icon_w, item_y + icon_h], outline=(40, 40, 45), width=1)
            if item_icon:
                item_icon = item_icon.resize((icon_w, icon_h), Image.Resampling.LANCZOS)
                canvas.paste(item_icon, (item_x + i*75, item_y), item_icon)
                
            # Draw purchase time
            if t is not None:
                t_str = format_duration(t)
                # Center time under icon
                draw.text((item_x + i*75 + 10, item_y + icon_h + 5), t_str, fill=(180, 180, 190), font=font_tiny)

        # Draw Neutral Item
        if neutral_img:
            ni_w, ni_h = 45, 45
            neutral_img = neutral_img.resize((ni_w, ni_h), Image.Resampling.LANCZOS)
            # Draw circle background
            draw.ellipse([item_x + 6*75, item_y, item_x + 6*75 + ni_w, item_y + ni_h], fill=(30, 30, 35))
            canvas.paste(neutral_img, (item_x + 6*75, item_y), neutral_img)

        # Removed old MMR drawing block as it is now in the stats row

        output = io.BytesIO()
        canvas.save(output, format="PNG")
        output.seek(0)
        return output
    except Exception as e:
        logger.error(f"Error generating premium match image: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None
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
        
        # Try to validate via API, but save even if API is down
        player_name = None
        last_match_id = None
        last_mmr = None
        
        try:
            player_data = await dota.get_player(steam_id)
            if player_data:
                player_name = player_data.get("profile", {}).get("personaname", "Unknown")
            
            data = await dota.get_latest_match(steam_id)
            if data:
                last_match_id = data["match"]["id"] if data.get("match") else None
                last_mmr = data.get("mmr_estimate")
                if not player_name:
                    player_name = data.get("player_name", "Unknown")
        except Exception as e:
            logger.warning(f"API check failed during set_id: {e}")
        
        # Save the ID regardless of API response
        db.set_user(chat_id, steam_id, last_match_id, last_mmr)
        
        if player_name:
            rank_name, rank_icon_id = get_rank_info(last_mmr)
            msg = (
                f"✅ **Привязано к {player_name}!**\n"
                f"Отслеживание матчей запущено.\n\n"
                f"🏆 Твой ранг: **{rank_name}**\n"
                f"📈 Оценка MMR: `{last_mmr if last_mmr else 'Неизвестно'}`\n\n"
                f"⚠️ **Важно:** Чтобы я считал текущий ММР точно (±25), "
                f"введи свой реальный MMR командой:\n`/set_mmr <число>`"
            )
        else:
            msg = (
                f"✅ **ID `{steam_id}` сохранён!**\n"
                f"⚠️ Не удалось проверить профиль (API временно недоступен), "
                f"но отслеживание запущено.\n\n"
                f"Введи свой реальный MMR командой:\n`/set_mmr <число>`"
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
    """Sends a mock match result for testing UI tweaks. Admin only."""
    chat_id = update.effective_chat.id
    
    # Admin check
    if ADMIN_CHAT_ID and str(chat_id) != str(ADMIN_CHAT_ID):
        await update.message.reply_text("🔒 Эта команда доступна только администратору бота.")
        return
    
    # Example mock data
    is_win = True
    result_emoji = "✨ ПОБЕДА ✨"
    hero_id = 71  # Spirit Breaker
    kills, deaths, assists = 12, 2, 8
    gpm, xpm = 750, 820
    match_id = 7123456789
    
    hero_info = await get_hero_info(hero_id)
    hero_name = hero_info["name"]
    
    user = db.get_user(chat_id)
    manual_mmr = (user.get("manual_mmr") or 1500) if user else 1500
    
    rank_name, rank_icon_id = get_rank_info(manual_mmr + 25)
    
    stats = {
        "result_text": result_emoji,
        "hero_name": hero_name,
        "kills": kills, "deaths": deaths, "assists": assists,
        "gpm": gpm, "xpm": xpm, "net_worth": 24500,
        "duration": "38:15",
        "rank_name": rank_name,
        "new_mmr": manual_mmr + 25,
        "mmr_diff": 25
    }
    
    msg = (
        f"**{result_emoji}** 🪄 (Тест)\n\n"
        f"👾 **Герой:** {hero_name}\n"
        f"🩸 **KDA:** `{kills} / {deaths} / {assists}`\n"
        f"💰 **GPM:** `{gpm}` | 🎓 **XPM:** `{xpm}`\n"
        f"💵 **Networth:** `24 500`\n"
        f"⏱️ **Длительность:** `38:15`\n\n"
        f"🎰 **ММР:** `{manual_mmr + 25}` (**+25**)\n"
        f"🏆 **Ранг:** {rank_name}\n"
        f"\n🔥🔥🔥 3 победы подряд!\n"
        f"\n🎖️ *Убийца! — 10+ убийств*\n"
        f"\n🔗 [Dotabuff](https://www.dotabuff.com/matches/{match_id})"
    )
    
    mock_items = [
        "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/blink.png",
        "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/black_king_bar.png",
        "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/power_treads.png",
        "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/echo_sabre.png",
        "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/heavens_halberd.png",
        "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/ultimate_scepter.png"
    ]
    mock_neutral = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/philosophers_stone.png"
    
    item_purchases = [
        {"itemId": 1, "time": 740}, # Blink
        {"itemId": 2, "time": 1250}, # BKB
        {"itemId": 3, "time": 450}, # Treads
        {"itemId": 4, "time": 900}, # Echo
        {"itemId": 5, "time": 1800}, # Halberd
        {"itemId": 6, "time": 2100}  # Scepter
    ]
    
    mock_abilities = [
        {"abilityId": 597, "level": 10, "isTalent": True},
        {"abilityId": 598, "level": 15, "isTalent": True},
        {"abilityId": 599, "level": 20, "isTalent": True},
        {"abilityId": 600, "level": 25, "isTalent": True}
    ]
    
    abilities_dict = await dota.get_abilities_dict() or {
        597: {"displayName": "+1.5s к длительности Charge"},
        598: {"displayName": "+10 к урону за ед. скорости"},
        599: {"displayName": "-3s перезарядки Bulldog"},
        600: {"displayName": "500 ко все радиусу Greater Bash"}
    }
    
    try:
        composite_io = await generate_composite_image(
            hero_short_name="spirit_breaker", 
            rank_icon_id=rank_icon_id, 
            items_urls=mock_items, 
            neutral_url=mock_neutral,
            stats={**stats, "item_ids": [1, 2, 3, 4, 5, 6], "nw_10": 4200},
            item_purchases=item_purchases,
            abilities=mock_abilities,
            ability_cache=abilities_dict
        )
        if composite_io:
            await context.bot.send_photo(chat_id=chat_id, photo=composite_io, caption=msg, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Error sending test msg: {e}")
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", disable_web_page_preview=True)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the tracking status and statistics of the bound account."""
    chat_id = str(update.effective_chat.id)
    user_info = db.get_user(chat_id)
    
    if not user_info or not user_info.get("steam_id"):
        await update.message.reply_text("❌ Привязанный Steam ID не найден. Используйте /set_id")
        return
        
    steam_id = int(user_info["steam_id"])
    mmr = user_info.get("manual_mmr", "Не установлен")
    rank_name, _ = get_rank_info(mmr) if isinstance(mmr, int) else ("Неизвестно", None)
    streak = user_info.get("win_streak", 0)
    
    msg = await update.message.reply_text("⏳ Собираю статус аккаунта...")
    
    player_data = await dota.get_player(steam_id)
    player_name = player_data.get("profile", {}).get("personaname", "Unknown") if player_data else "Unknown"
    
    recent_stats = await dota.get_recent_stats(steam_id)
    
    status_text = (
        f"📊 **Статус аккаунта**\n\n"
        f"👤 **Игрок:** `{player_name}`\n"
        f"🏆 **Ранг:** {rank_name}\n"
        f"🎰 **MMR:** `{mmr}`\n"
    )
    
    if streak >= 3:
        status_text += f"🔥 **Серия побед:** {streak} подряд!\n"
    
    if recent_stats:
        wins = recent_stats.get('wins', 0)
        losses = recent_stats.get('losses', 0)
        winrate = recent_stats.get('winrate_percent', 0.0)
        
        status_text += (
            f"\n📈 **Винрейт (20 игр):** `{winrate}%` ({wins}W / {losses}L)\n"
        )
        
        # Top-3 heroes
        top_heroes = recent_stats.get('top_heroes', [])
        if top_heroes:
            status_text += "\n🦸 **Топ герои (20 игр):**\n"
            medals = ["🥇", "🥈", "🥉"]
            for i, h in enumerate(top_heroes):
                hero_name = await dota.get_hero_name(h['hero_id'])
                status_text += f"  {medals[i]} {hero_name} — {h['count']}x ({h['winrate']}% WR)\n"
        
        # Best KDA match
        best_kda = recent_stats.get('best_kda')
        if best_kda and best_kda.get('hero_id'):
            bk_hero = await dota.get_hero_name(best_kda['hero_id'])
            status_text += (
                f"\n🏅 **Лучший матч:** {bk_hero} — "
                f"`{best_kda['kills']}/{best_kda['deaths']}/{best_kda['assists']}` "
                f"(KDA: {best_kda['kda_value']})"
            )
    else:
        status_text += "\n⚠️ _Не удалось загрузить последние матчи_"
        
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=msg.message_id,
        text=status_text,
        parse_mode="Markdown"
    )

# --- Achievements ---
def check_achievements(pm, new_mmr, old_mmr, streak):
    """Check for one-time achievements to show in the match card."""
    achievements = []
    kills = pm.get("numKills", 0)
    deaths = pm.get("numDeaths", 0)
    assists = pm.get("numAssists", 0)
    is_win = pm.get("isVictory", False)
    
    # Kill milestones
    if kills >= 30:
        achievements.append("😈 *Богоподобный! — 30+ убийств*")
    elif kills >= 20:
        achievements.append("☠️ *Бойня! — 20+ убийств*")
    elif kills >= 15:
        achievements.append("⚔️ *Доминация! — 15+ убийств*")
    elif kills >= 10:
        achievements.append("🎖️ *Убийца! — 10+ убийств*")
    
    # Assist milestones
    if assists >= 25:
        achievements.append("🤝 *Лучший напарник! — 25+ ассистов*")
    
    # Perfect game  
    if deaths == 0 and kills >= 5:
        achievements.append("😇 *Бессмертный! — 0 смертей*")
    
    # KDA monster
    kda = (kills + assists) / max(1, deaths)
    if kda >= 15:
        achievements.append("👑 *Легенда! — KDA 15+*")
    
    # Win streak
    if streak == 5:
        achievements.append("🔥 *В огне! — 5 побед подряд*")
    elif streak == 10:
        achievements.append("🌋 *Неудержимый! — 10 побед подряд*")
    
    # MMR milestones (crossed a hundred boundary)
    if new_mmr and old_mmr and is_win:
        new_hundred = new_mmr // 100
        old_hundred = old_mmr // 100
        if new_hundred > old_hundred:
            achievements.append(f"📈 *Покоритель! — Достиг {new_hundred * 100} MMR*")
    
    return achievements

# --- /graph command ---
async def graph_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates and sends an MMR history graph."""
    chat_id = str(update.effective_chat.id)
    user_info = db.get_user(chat_id)
    
    if not user_info:
        await update.message.reply_text("❌ Сначала привяжи аккаунт: /set_id")
        return
    
    history = db.get_mmr_history(chat_id, limit=30)
    if len(history) < 2:
        await update.message.reply_text("📈 Недостаточно данных. Сыграй ещё несколько рейтинговых матчей!")
        return
    
    # Generate graph image
    img = generate_mmr_graph(history)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    
    await context.bot.send_photo(
        chat_id=int(chat_id),
        photo=buf,
        caption=f"📈 **График MMR** (последние {len(history)} матчей)",
        parse_mode="Markdown"
    )

def generate_mmr_graph(history):
    """Create an MMR graph image using Pillow."""
    if not HAS_PILLOW:
        return None

    W, H = 800, 400
    PAD_L, PAD_R, PAD_T, PAD_B = 70, 30, 40, 50
    
    img = Image.new('RGB', (W, H), color=(24, 26, 33))
    draw = ImageDraw.Draw(img)
    
    # Try different font paths common for Linux/Windows
    font = None
    font_sm = None
    font_title = None
    
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "arial.ttf",
        "DejaVuSans.ttf"
    ]
    
    for path in font_paths:
        try:
            font = ImageFont.truetype(path, 14)
            font_sm = ImageFont.truetype(path, 11)
            font_title = ImageFont.truetype(path, 16)
            break
        except:
            continue
            
    if not font:
        font = ImageFont.load_default()
        font_sm = font
        font_title = font
    
    mmr_values = [h[0] for h in history]
    is_wins = [h[1] for h in history]
    
    min_mmr = min(mmr_values) - 50
    max_mmr = max(mmr_values) + 50
    if max_mmr == min_mmr:
        max_mmr += 100
    
    n = len(mmr_values)
    graph_w = W - PAD_L - PAD_R
    graph_h = H - PAD_T - PAD_B
    
    # Title
    draw.text((W // 2 - 60, 10), "MMR History", fill=(255, 255, 255), font=font_title)
    
    # Grid lines
    num_grid = 5
    for i in range(num_grid + 1):
        y = PAD_T + int(graph_h * i / num_grid)
        mmr_val = max_mmr - (max_mmr - min_mmr) * i / num_grid
        draw.line([(PAD_L, y), (W - PAD_R, y)], fill=(50, 50, 60), width=1)
        draw.text((5, y - 7), f"{int(mmr_val)}", fill=(150, 150, 160), font=font_sm)
    
    def mmr_to_y(mmr):
        return PAD_T + int(graph_h * (1 - (mmr - min_mmr) / (max_mmr - min_mmr)))
    
    def idx_to_x(i):
        return PAD_L + int(graph_w * i / max(1, n - 1))
    
    # Draw line
    points = [(idx_to_x(i), mmr_to_y(v)) for i, v in enumerate(mmr_values)]
    for i in range(1, len(points)):
        color = (76, 175, 80) if mmr_values[i] >= mmr_values[i-1] else (244, 67, 54)
        draw.line([points[i-1], points[i]], fill=color, width=2)
    
    # Draw dots
    for i, (x, y) in enumerate(points):
        dot_color = (76, 175, 80) if is_wins[i] else (244, 67, 54)
        r = 5
        draw.ellipse([x-r, y-r, x+r, y+r], fill=dot_color, outline=(255, 255, 255), width=1)
    
    # X-axis labels
    for i in range(0, n, max(1, n // 6)):
        x = idx_to_x(i)
        draw.text((x - 5, H - PAD_B + 10), f"{i+1}", fill=(150, 150, 160), font=font_sm)
    
    # Start and end MMR labels
    draw.text((points[0][0] - 10, points[0][1] - 20), f"{mmr_values[0]}", fill=(255, 255, 255), font=font_sm)
    draw.text((points[-1][0] - 10, points[-1][1] - 20), f"{mmr_values[-1]}", fill=(255, 255, 255), font=font_sm)
    
    # Legend
    draw.ellipse([W - 120, H - 20, W - 112, H - 12], fill=(76, 175, 80))
    draw.text((W - 108, H - 22), "Win", fill=(150, 150, 160), font=font_sm)
    draw.ellipse([W - 70, H - 20, W - 62, H - 12], fill=(244, 67, 54))
    draw.text((W - 58, H - 22), "Loss", fill=(150, 150, 160), font=font_sm)
    
    return img

# --- Helper to format duration ---
def format_duration(seconds):
    """Format seconds into MM:SS string."""
    if not seconds:
        return "??:??"
    m = seconds // 60
    s = seconds % 60
    return f"{m}:{s:02d}"

async def monitor_matches(context: ContextTypes.DEFAULT_TYPE):
    """Background task to poll for new matches."""
    users = db.get_all_users()
    for chat_id, user_info in users.items():
        try:
            steam_id = user_info["steam_id"]
            
            # Use Stratz to get latest match (much faster than OpenDota)
            data = await dota.get_latest_match(steam_id)
            if not data or not data.get("match") or not data.get("player_match"):
                continue
            
            match_id = data["match"]["id"]
            if str(match_id) != str(user_info.get("last_match_id")):
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
                duration = data["match"].get("duration", 0)
                
                items_urls = pm.get("items_urls", [])
                neutral_url = pm.get("neutral_url")
                
                # New fields for premium card
                hero_short_name = pm.get("hero_short_name")
                item_purchases = pm.get("item_purchases", [])
                abilities_data = pm.get("abilities", [])
                ability_cache = await dota.get_abilities_dict()
                
                result_emoji = "✨ ПОБЕДА ✨" if is_win else "💀 ПОРАЖЕНИЕ"
                
                manual_mmr = user_info.get("manual_mmr")
                matches_count = user_info.get("matches_since_calibration", 0)
                old_streak = user_info.get("win_streak", 0)
                new_streak = (old_streak + 1) if is_win else 0
                
                lobby_type = pm.get("lobby_type")
                game_mode = pm.get("game_mode")
                
                is_ranked = (lobby_type == 7)
                is_turbo = (game_mode == 23)
                is_custom = (lobby_type in [4, 15]) or (game_mode in [15, 19])
                
                if is_custom:
                    db.update_match(chat_id, match_id, user_info.get("last_mmr"), new_streak)
                    continue
                
                match_type_label = ""
                if is_turbo:
                    match_type_label = " ⚡️ (Турбо)"
                elif not is_ranked:
                    match_type_label = " 🎮 (Обычный)"
                
                mmr_change_text = ""
                new_mmr = manual_mmr
                old_mmr = manual_mmr
                if manual_mmr is not None:
                    if is_ranked and not is_turbo:
                        diff = 25 if is_win else -25
                        new_mmr = manual_mmr + diff
                        matches_count += 1
                        diff_sign = "+" if diff > 0 else ""
                        rank_name, rank_icon_id = get_rank_info(new_mmr)
                        
                        mmr_change_text = (
                            f"🎰 **ММР:** `{new_mmr}` (**{diff_sign}{diff}**)\n"
                            f"🏆 **Ранг:** {rank_name}"
                        )
                        db.update_match_and_mmr(chat_id, match_id, new_mmr, matches_count, new_streak)
                        
                        # Log to MMR history for /graph
                        db.add_mmr_history(chat_id, match_id, new_mmr, is_win)
                    else:
                        rank_name, rank_icon_id = get_rank_info(manual_mmr)
                        mmr_change_text = (
                            f"🎰 **ММР:** `{manual_mmr}` (без изменений)\n"
                            f"🏆 **Ранг:** {rank_name}"
                        )
                        db.update_match(chat_id, match_id, user_info.get("last_mmr"), new_streak)
                    
                    if matches_count >= 10:
                        mmr_change_text += "\n\n🔄 *Пора обновить точный MMR: /set_mmr*"
                else:
                    new_mmr = data.get("mmr_estimate")
                    rank_icon_id = None
                    mmr_change_text = f"📈 Оценка MMR: `{new_mmr}`\n⚠️ *Установи MMR: /set_mmr*"
                    db.update_match(chat_id, match_id, new_mmr, new_streak)
                
                # Log match for daily/weekly summaries
                db.log_match(chat_id, match_id, hero_id, is_win, kills, deaths, assists, new_mmr)
                
                formatted_nw = f"{nw:,}".replace(",", " ")
                duration_text = format_duration(duration)
                
                msg = (
                    f"**{result_emoji}{match_type_label}**\n\n"
                    f"👾 **Герой:** {hero_name}\n"
                    f"🩸 **KDA:** `{kills} / {deaths} / {assists}`\n"
                    f"💰 **GPM:** `{gpm}` | 🎓 **XPM:** `{xpm}`\n"
                    f"💵 **Networth:** `{formatted_nw}`\n"
                    f"⏱️ **Длительность:** `{duration_text}`\n\n"
                    f"{mmr_change_text}"
                )
                
                # Win streak (3+)
                if is_win and new_streak >= 3:
                    fires = "🔥" * min(new_streak, 10)
                    msg += f"\n\n{fires} {new_streak} побед подряд!"
                
                # Achievements
                achs = check_achievements(pm, new_mmr, old_mmr, new_streak)
                if achs:
                    msg += "\n" + "\n".join(f"\n{a}" for a in achs)
                
                msg += f"\n\n🔗 [Dotabuff](https://www.dotabuff.com/matches/{match_id})"
                
                # NW at 10:00 from timeline
                nw_timeline = pm.get("networth_timeline", [])
                nw_10 = nw_timeline[10] if len(nw_timeline) > 10 else None
                
                # Prepare stats object for image generator
                match_stats = {
                    "result_text": f"{result_emoji}{match_type_label}",
                    "hero_name": hero_name,
                    "kills": kills, "deaths": deaths, "assists": assists,
                    "gpm": gpm, "xpm": xpm, "net_worth": nw,
                    "duration": duration_text,
                    "rank_name": rank_name if 'rank_name' in locals() else "Неизвестно",
                    "new_mmr": new_mmr,
                    "mmr_diff": (new_mmr - manual_mmr) if (manual_mmr and new_mmr and is_ranked) else None,
                    "nw_10": nw_10
                }
                
                try:
                    composite_io = await generate_composite_image(
                        hero_short_name=hero_short_name, 
                        rank_icon_id=rank_icon_id, 
                        items_urls=items_urls, 
                        neutral_url=neutral_url,
                        item_purchases=item_purchases,
                        abilities=abilities_data,
                        ability_cache=ability_cache,
                        stats={**match_stats, "item_ids": pm.get("item_ids", [None]*6)}
                    )
                    
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

# --- Daily/Weekly Summaries ---
async def daily_summary(context: ContextTypes.DEFAULT_TYPE):
    """Send daily summary to all users at 23:30 MSK."""
    users = db.get_all_users()
    for chat_id, user_info in users.items():
        try:
            matches = db.get_matches_since(chat_id, since_hours=24)
            if not matches:
                continue
            
            wins = sum(1 for m in matches if m[2])  # is_win
            losses = len(matches) - wins
            total = len(matches)
            
            # MMR change
            mmr_values = [m[6] for m in matches if m[6] is not None]  # mmr_after
            mmr_start = mmr_values[0] if mmr_values else None
            mmr_end = mmr_values[-1] if mmr_values else None
            
            mmr_text = ""
            if mmr_start and mmr_end:
                diff = mmr_end - mmr_start
                sign = "+" if diff >= 0 else ""
                mmr_text = f"📈 **MMR:** `{mmr_start}` → `{mmr_end}` ({sign}{diff})\n"
            
            # Best hero today
            hero_counts = {}
            for m in matches:
                hid = m[1]  # hero_id
                if hid:
                    if hid not in hero_counts:
                        hero_counts[hid] = {"count": 0, "wins": 0}
                    hero_counts[hid]["count"] += 1
                    if m[2]:  # is_win
                        hero_counts[hid]["wins"] += 1
            
            best_hero_text = ""
            if hero_counts:
                best_hid = max(hero_counts, key=lambda x: hero_counts[x]["wins"])
                bh = hero_counts[best_hid]
                hero_name = await dota.get_hero_name(best_hid)
                best_hero_text = f"🦸 **Лучший герой:** {hero_name} ({bh['wins']}W / {bh['count'] - bh['wins']}L)\n"
            
            today = datetime.now(MSK).strftime("%d.%m.%Y")
            text = (
                f"📋 **Итоги дня — {today}**\n\n"
                f"🎮 **Матчей:** {total}\n"
                f"✅ Побед: {wins} | ❌ Поражений: {losses}\n"
                f"{mmr_text}"
                f"{best_hero_text}"
            )
            
            await context.bot.send_message(chat_id=int(chat_id), text=text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error sending daily summary to {chat_id}: {e}")

async def weekly_summary(context: ContextTypes.DEFAULT_TYPE):
    """Send weekly summary to all users on Sunday at 23:31 MSK."""
    users = db.get_all_users()
    for chat_id, user_info in users.items():
        try:
            matches = db.get_matches_since(chat_id, since_hours=168)  # 7 days
            if not matches:
                continue
            
            wins = sum(1 for m in matches if m[2])
            losses = len(matches) - wins
            total = len(matches)
            winrate = round(wins / total * 100, 1) if total > 0 else 0
            
            mmr_values = [m[6] for m in matches if m[6] is not None]
            mmr_start = mmr_values[0] if mmr_values else None
            mmr_end = mmr_values[-1] if mmr_values else None
            
            mmr_text = ""
            if mmr_start and mmr_end:
                diff = mmr_end - mmr_start
                sign = "+" if diff >= 0 else ""
                mmr_text = f"📈 **MMR:** `{mmr_start}` → `{mmr_end}` ({sign}{diff})\n"
            
            # Top-3 heroes of the week
            hero_counts = {}
            for m in matches:
                hid = m[1]
                if hid:
                    if hid not in hero_counts:
                        hero_counts[hid] = {"count": 0, "wins": 0}
                    hero_counts[hid]["count"] += 1
                    if m[2]:
                        hero_counts[hid]["wins"] += 1
            
            heroes_text = ""
            if hero_counts:
                sorted_heroes = sorted(hero_counts.items(), key=lambda x: x[1]["count"], reverse=True)[:3]
                heroes_text = "\n🦸 **Топ герои недели:**\n"
                medals = ["🥇", "🥈", "🥉"]
                for i, (hid, stats) in enumerate(sorted_heroes):
                    hero_name = await dota.get_hero_name(hid)
                    wr = round(stats["wins"] / stats["count"] * 100) if stats["count"] > 0 else 0
                    heroes_text += f"  {medals[i]} {hero_name} — {stats['count']}x ({wr}% WR)\n"
            
            # Best KDA of the week
            best_kda_text = ""
            best_kda_val = 0
            for m in matches:
                k, d, a = m[3] or 0, m[4] or 0, m[5] or 0
                kda = (k + a) / max(1, d)
                if kda > best_kda_val:
                    best_kda_val = kda
                    best_hid = m[1]
                    best_k, best_d, best_a = k, d, a
            if best_kda_val > 0 and best_hid:
                bk_hero = await dota.get_hero_name(best_hid)
                best_kda_text = f"\n🏅 **Лучший матч:** {bk_hero} — `{best_k}/{best_d}/{best_a}` (KDA: {round(best_kda_val, 1)})"
            
            text = (
                f"📆 **Итоги недели**\n\n"
                f"🎮 **Матчей:** {total}\n"
                f"✅ Побед: {wins} | ❌ Поражений: {losses}\n"
                f"📊 **Винрейт:** {winrate}%\n"
                f"{mmr_text}"
                f"{heroes_text}"
                f"{best_kda_text}"
            )
            
            await context.bot.send_message(chat_id=int(chat_id), text=text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error sending weekly summary to {chat_id}: {e}")


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
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("graph", graph_command))
    
    # Job queue
    job_queue = application.job_queue
    
    # Match polling every 3 minutes
    job_queue.run_repeating(monitor_matches, interval=180, first=10)
    
    # Daily summary at 23:30 MSK (20:30 UTC)
    job_queue.run_daily(daily_summary, time=time(hour=20, minute=30, tzinfo=timezone.utc))
    
    # Weekly summary on Sunday at 23:31 MSK (20:31 UTC)
    job_queue.run_daily(
        weekly_summary, 
        time=time(hour=20, minute=31, tzinfo=timezone.utc),
        days=(6,)  # 6 = Sunday
    )
    
    async with application:
        await application.initialize()
        await application.start()
        
        # Set up the menu button and command autocomplete
        await application.bot.set_my_commands([
            BotCommand("start", "Запустить бота"),
            BotCommand("set_id", "Привязать ID (пример: /set_id <ID>)"),
            BotCommand("set_mmr", "Установить MMR (пример: /set_mmr <ММР>)"),
            BotCommand("status", "Показать статус аккаунта"),
            BotCommand("graph", "График MMR"),
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
