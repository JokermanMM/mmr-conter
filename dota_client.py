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

    async def get_latest_match(self, steam_id: int) -> dict | None:
        """Get latest match with player stats."""
        # First get player profile
        player = await self.get_player(steam_id)
        if not player:
            return None
        
        player_name = player.get("profile", {}).get("personaname", "Unknown")
        mmr_estimate = player.get("mmr_estimate", {}).get("estimate")
        
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
                
                return {
                    "player_name": player_name,
                    "mmr_estimate": mmr_estimate,
                    "match": {
                        "id": latest.get("match_id"),
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

    async def get_hero_name(self, hero_id: int) -> str:
        """Get hero display name by ID."""
        url = f"{self.BASE_URL}/heroes"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(url, headers=self.headers, timeout=15.0)
                if r.status_code == 200:
                    heroes = r.json()
                    for hero in heroes:
                        if hero.get("id") == hero_id:
                            return hero.get("localized_name", f"Hero #{hero_id}")
                return f"Hero #{hero_id}"
            except Exception:
                return f"Hero #{hero_id}"

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
