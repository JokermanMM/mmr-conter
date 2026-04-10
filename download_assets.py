import asyncio
import os
import httpx
from dota_client import DotaClient
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def download_image(client, url, path):
    """Download an image from url to path if it doesn't exist."""
    if os.path.exists(path):
        return False
    
    try:
        r = await client.get(url, timeout=10.0)
        if r.status_code == 200:
            with open(path, "wb") as f:
                f.write(r.content)
            return True
    except Exception as e:
        logger.error(f"Failed to download {url}: {e}")
    return False

async def main():
    load_dotenv()
    stratz_token = os.environ.get("STRATZ_TOKEN")
    dota = DotaClient(stratz_token)
    
    # Ensure directories exist
    os.makedirs("assets/heroes", exist_ok=True)
    os.makedirs("assets/items", exist_ok=True)
    
    # 1. Download Heroes
    logger.info("Fetching hero list...")
    heroes = await dota.get_all_heroes()
    if not heroes:
        logger.info("OpenDota failed, trying Stratz for heroes...")
        heroes = await dota.get_all_heroes_stratz()
        
    count_h = 0
    async with httpx.AsyncClient() as client:
        for hid, hdata in heroes.items():
            short_name = hdata.get("name", "").replace("npc_dota_hero_", "")
            if short_name:
                url = f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{short_name}.png"
                path = f"assets/heroes/{hid}.png"
                if await download_image(client, url, path):
                    count_h += 1
        
        logger.info(f"Finished downloading {count_h} new heroes.")
        
        # 2. Download Items
        logger.info("Fetching item list...")
        items = await dota.get_all_items_full()
        if not items:
            logger.info("OpenDota failed, trying Stratz for items...")
            items = await dota.get_all_items_stratz()
            
        count_i = 0
        for iid_str, idata in items.items():
            iid = idata.get("id")
            img_path = idata.get("img")
            if iid and img_path:
                url = f"https://cdn.cloudflare.steamstatic.com{img_path}"
                path = f"assets/items/{iid}.png"
                if await download_image(client, url, path):
                    count_i += 1
        
        logger.info(f"Finished downloading {count_i} new items.")

if __name__ == "__main__":
    asyncio.run(main())
