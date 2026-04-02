import httpx
import logging

logger = logging.getLogger(__name__)

class DotaClient:
    """Client for OpenDota API - free, no auth required, no Cloudflare."""
    BASE_URL = "https://api.opendota.com/api"
    
    def __init__(self):
        self.headers = {
            "User-Agent": "Dota2MMRBot/1.0",
            "Accept": "application/json"
        }
        self.hero_cache = {}
        self.item_id_map = {}

    async def get_items_dict(self) -> dict:
        """Fetch items mapping from OpenDota."""
        if not self.item_id_map:
            url = f"{self.BASE_URL}/constants/items"
            async with httpx.AsyncClient() as client:
                try:
                    r = await client.get(url, headers=self.headers, timeout=15.0)
                    if r.status_code == 200:
                        data = r.json()
                        # Map item ID -> Image URL
                        for key, val in data.items():
                            item_id = val.get("id")
                            if item_id:
                                img_path = val.get("img")
                                if img_path:
                                    # Ensure it's a full URL
                                    full_url = f"https://cdn.cloudflare.steamstatic.com{img_path}"
                                    self.item_id_map[item_id] = full_url
                except Exception as e:
                    logger.error(f"Error fetching items: {e}")
        return self.item_id_map

    async def get_player(self, steam_id: int) -> dict | None:
        """Get player profile info."""
        url = f"{self.BASE_URL}/players/{steam_id}"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(url, headers=self.headers, timeout=15.0)
                if r.status_code != 200:
                    logger.error(f"OpenDota player request failed: {r.status_code}")
                    return None
                data = r.json()
                if not data.get("profile"):
                    return None
                return data
            except Exception as e:
                logger.error(f"Error fetching player: {e}")
                return None

    async def refresh_player(self, steam_id: int) -> bool:
        """Force OpenDota to sync with Steam API for this player."""
        url = f"{self.BASE_URL}/players/{steam_id}/refresh"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.post(url, headers=self.headers, timeout=15.0)
                return r.status_code == 200
            except Exception as e:
                logger.error(f"Error refreshing player: {e}")
                return False

    async def get_latest_match(self, steam_id: int) -> dict | None:
        """Get latest match with player stats."""
        # First get player profile
        player = await self.get_player(steam_id)
        if not player:
            return None
        
        player_name = player.get("profile", {}).get("personaname", "Unknown")
        # Try multiple MMR sources
        mmr_estimate = player.get("computed_mmr")
        if not mmr_estimate:
            mmr_est_obj = player.get("mmr_estimate")
            if isinstance(mmr_est_obj, dict):
                mmr_estimate = mmr_est_obj.get("estimate")
        if mmr_estimate:
            mmr_estimate = int(mmr_estimate)
        
        # Get recent matches
        url = f"{self.BASE_URL}/players/{steam_id}/recentMatches"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(url, headers=self.headers, timeout=15.0)
                if r.status_code != 200:
                    logger.error(f"OpenDota matches request failed: {r.status_code}")
                    return {
                        "player_name": player_name,
                        "mmr_estimate": mmr_estimate,
                        "match": None,
                        "player_match": None
                    }
                
                matches = r.json()
                if not matches:
                    return {
                        "player_name": player_name,
                        "mmr_estimate": mmr_estimate,
                        "match": None,
                        "player_match": None
                    }
                
                latest = matches[0]
                
                # Determine win/loss
                # player_slot < 128 = radiant, >= 128 = dire
                player_slot = latest.get("player_slot", 0)
                radiant_win = latest.get("radiant_win", False)
                is_radiant = player_slot < 128
                is_victory = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)
                
                match_id = latest.get("match_id")
                
                # We need to fetch details for items and net worth
                match_url = f"{self.BASE_URL}/matches/{match_id}"
                match_details = {}
                try:
                    rm = await client.get(match_url, headers=self.headers, timeout=15.0)
                    if rm.status_code == 200:
                        match_details = rm.json()
                except Exception as e:
                    logger.error(f"Error fetching match details: {e}")
                
                # Find player in the detailed match data
                full_player_data = {}
                for p in match_details.get("players", []):
                    if p.get("player_slot") == player_slot:
                        full_player_data = p
                        break
                
                item_ids = [full_player_data.get(f"item_{i}") for i in range(6)]
                neutral_id = full_player_data.get("item_neutral")
                net_worth = full_player_data.get("net_worth", 0)
                
                # Pre-fetch items if needed
                items_map = await self.get_items_dict()
                
                items_urls = [items_map.get(iid) for iid in item_ids]
                neutral_url = items_map.get(neutral_id) if neutral_id else None
                
                return {
                    "player_name": player_name,
                    "mmr_estimate": mmr_estimate,
                    "match": {
                        "id": match_id,
                        "duration": latest.get("duration"),
                    },
                    "player_match": {
                        "isVictory": is_victory,
                        "hero_id": latest.get("hero_id"),
                        "numKills": latest.get("kills", 0),
                        "numDeaths": latest.get("deaths", 0),
                        "numAssists": latest.get("assists", 0),
                        "xp_per_min": latest.get("xp_per_min", 0),
                        "gold_per_min": latest.get("gold_per_min", 0),
                        "lobby_type": latest.get("lobby_type"),
                        "game_mode": latest.get("game_mode"),
                        "net_worth": net_worth,
                        "items_urls": items_urls,
                        "neutral_url": neutral_url
                    }
                }
            except Exception as e:
                logger.error(f"Error fetching matches: {e}")
                return {
                    "player_name": player_name,
                    "mmr_estimate": mmr_estimate,
                    "match": None,
                    "player_match": None
                }

    async def get_recent_stats(self, steam_id: int) -> dict | None:
        """Get winrate and favorite hero for the last 20 matches."""
        url = f"{self.BASE_URL}/players/{steam_id}/recentMatches"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(url, headers=self.headers, timeout=15.0)
                if r.status_code != 200:
                    return None
                
                matches = r.json()
                if not matches:
                    return None
                    
                wins = 0
                losses = 0
                hero_counts = {}
                
                for m in matches: # Up to 20 by default
                    # Win/loss calc
                    player_slot = m.get("player_slot", 0)
                    radiant_win = m.get("radiant_win", False)
                    is_radiant = player_slot < 128
                    is_victory = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)
                    
                    if is_victory:
                        wins += 1
                    else:
                        losses += 1
                        
                    # Hero calc
                    hid = m.get("hero_id")
                    if hid:
                        hero_counts[hid] = hero_counts.get(hid, 0) + 1
                        
                most_played_hero_id = None
                most_played_count = 0
                if hero_counts:
                    # Find highest count
                    most_played_hero_id = max(hero_counts, key=hero_counts.get)
                    most_played_count = hero_counts[most_played_hero_id]
                    
                total = wins + losses
                winrate = (wins / total * 100) if total > 0 else 0.0
                
                return {
                    "wins": wins,
                    "losses": losses,
                    "winrate_percent": round(winrate, 1),
                    "favorite_hero_id": most_played_hero_id,
                    "favorite_hero_count": most_played_count
                }
            except Exception as e:
                logger.error(f"Error fetching recent stats: {e}")
                return None

    async def get_hero_data(self, hero_id: int) -> dict:
        """Get hero detailed data by ID."""
        if not self.hero_cache:
            url = f"{self.BASE_URL}/heroes"
            async with httpx.AsyncClient() as client:
                try:
                    r = await client.get(url, headers=self.headers, timeout=15.0)
                    if r.status_code == 200:
                        heroes = r.json()
                        for hero in heroes:
                            self.hero_cache[hero["id"]] = hero
                except Exception as e:
                    logger.error(f"Error fetching heroes: {e}")
        
        return self.hero_cache.get(hero_id, {})

    async def get_hero_name(self, hero_id: int) -> str:
        """Get hero display name by ID."""
        hero = await self.get_hero_data(hero_id)
        return hero.get("localized_name", f"Hero #{hero_id}")

    async def raw_query(self, steam_id: int) -> str:
        """Return raw API response for debugging."""
        url = f"{self.BASE_URL}/players/{steam_id}"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(url, headers=self.headers, timeout=15.0)
                body = r.text[:1000]
                return f"Status: {r.status_code}\nBody: {body}"
            except Exception as e:
                return f"Error: {e}"
