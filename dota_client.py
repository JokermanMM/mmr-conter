import httpx
import logging

logger = logging.getLogger(__name__)

class DotaClient:
    """Client for Dota 2 data. Uses Stratz GraphQL as primary and OpenDota as secondary/constants."""
    OPENDOTA_URL = "https://api.opendota.com/api"
    STRATZ_URL = "https://api.stratz.com/graphql"
    
    def __init__(self, stratz_token: str = None):
        self.headers = {
            "User-Agent": "Dota2MMRBot/1.0",
            "Accept": "application/json"
        }
        self.stratz_token = stratz_token
        if stratz_token:
            self.stratz_headers = {
                "Authorization": f"Bearer {stratz_token}",
                "Content-Type": "application/json",
                "User-Agent": "Dota2MMRBot/1.0"
            }
        
        self.hero_cache = {}
        self.item_id_map = {}

    async def _query_stratz(self, query: str, variables: dict = None) -> dict | None:
        """Helper to send GraphQL queries to Stratz."""
        if not self.stratz_token:
            logger.error("Stratz token not provided")
            return None
            
        async with httpx.AsyncClient() as client:
            try:
                payload = {"query": query}
                if variables:
                    payload["variables"] = variables
                    
                r = await client.post(self.STRATZ_URL, json=payload, headers=self.stratz_headers, timeout=20.0)
                if r.status_code != 200:
                    logger.error(f"Stratz request failed: {r.status_code} - {r.text}")
                    return None
                
                resp_json = r.json()
                if "errors" in resp_json:
                    logger.error(f"Stratz GraphQL errors: {resp_json['errors']}")
                return resp_json
            except Exception as e:
                logger.error(f"Error querying Stratz: {e}")
                return None

    async def get_items_dict(self) -> dict:
        """Fetch items mapping from OpenDota (static constants)."""
        if not self.item_id_map:
            url = f"{self.OPENDOTA_URL}/constants/items"
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
        """Get player profile info via Stratz."""
        query = """
        query($steamId: Long!) {
          player(steamAccountId: $steamId) {
            steamAccount {
              name
              seasonRank
            }
          }
        }
        """
        data = await self._query_stratz(query, {"steamId": steam_id})
        if not data or not data.get("data") or not data["data"].get("player"):
            # Fallback to OpenDota if Stratz fails
            url = f"{self.OPENDOTA_URL}/players/{steam_id}"
            async with httpx.AsyncClient() as client:
                try:
                    r = await client.get(url, headers=self.headers, timeout=15.0)
                    if r.status_code == 200:
                        od_data = r.json()
                        if od_data.get("profile"):
                            return od_data
                    return None
                except Exception:
                    return None
        
        p = data["data"]["player"]
        return {
            "profile": {
                "personaname": p["steamAccount"]["name"]
            },
            "rank_tier": p["steamAccount"].get("seasonRank")
        }

    async def refresh_player(self, steam_id: int) -> bool:
        """Force OpenDota/Stratz to sync. (Mainly OpenDota specific)."""
        url = f"{self.OPENDOTA_URL}/players/{steam_id}/refresh"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.post(url, headers=self.headers, timeout=15.0)
                return r.status_code == 200
            except Exception as e:
                logger.error(f"Error refreshing player: {e}")
                return False

    async def get_latest_match(self, steam_id: int) -> dict | None:
        """Get latest match with player stats using Stratz."""
        query = """
        query($steamId: Long!) {
          player(steamAccountId: $steamId) {
            steamAccount {
              name
            }
            matches(take: 1) {
              id
              gameMode
              lobbyType
              durationSeconds
              players {
                steamAccountId
                isVictory
                heroId
                numKills
                numDeaths
                numAssists
                goldPerMinute
                experiencePerMinute
                netWorth
                item0Id
                item1Id
                item2Id
                item3Id
                item4Id
                item5Id
                neutral0Id
              }
            }
          }
        }
        """
        data = await self._query_stratz(query, {"steamId": steam_id})
        if not data or not data.get("data") or not data["data"].get("player"):
            return None
        
        player = data["data"]["player"]
        matches = player.get("matches")
        if not matches:
            return None
            
        latest = matches[0]
        match_id = latest["id"]
        
        # Find our player in the match
        player_stats = None
        for p in latest.get("players", []):
            if p.get("steamAccountId") == steam_id:
                player_stats = p
                break
        
        if not player_stats:
            logger.warning(f"Player {steam_id} not found in Stratz match {match_id}")
            return None
        
        # Prepare item URLs (still using OpenDota for constants mapping)
        items_map = await self.get_items_dict()
        item_ids = [player_stats.get(f"item{i}Id") for i in range(6)]
        items_urls = [items_map.get(iid) for iid in item_ids]
        neutral_url = items_map.get(player_stats.get("neutral0Id"))
        
        return {
            "player_name": player["steamAccount"]["name"],
            "match": {
                "id": str(match_id),
                "duration": latest["durationSeconds"]
            },
            "player_match": {
                "isVictory": player_stats["isVictory"],
                "hero_id": player_stats.get("heroId"),
                "numKills": player_stats["numKills"],
                "numDeaths": player_stats["numDeaths"],
                "numAssists": player_stats["numAssists"],
                "xp_per_min": player_stats["experiencePerMinute"],
                "gold_per_min": player_stats["goldPerMinute"],
                "net_worth": player_stats["netWorth"],
                "lobby_type": latest["lobbyType"],
                "game_mode": latest["gameMode"],
                "items_urls": items_urls,
                "neutral_url": neutral_url
            }
        }

    async def get_recent_stats(self, steam_id: int) -> dict | None:
        """Get winrate and favorite hero for the last 20 matches using Stratz."""
        query = """
        query($steamId: Long!) {
          player(steamAccountId: $steamId) {
            matches(take: 20) {
              players {
                steamAccountId
                isVictory
                heroId
              }
            }
          }
        }
        """
        data = await self._query_stratz(query, {"steamId": steam_id})
        if not data or not data.get("data") or not data["data"].get("player"):
            return None
            
        matches = data["data"]["player"].get("matches", [])
        if not matches:
            return None
            
        wins = 0
        losses = 0
        hero_counts = {}
        
        for m in matches:
            # Find our player
            p = None
            for mp in m.get("players", []):
                if mp.get("steamAccountId") == steam_id:
                    p = mp
                    break
            
            if not p:
                continue
                
            if p["isVictory"]:
                wins += 1
            else:
                losses += 1
            
            hid = p["heroId"]
            if hid:
                hero_counts[hid] = hero_counts.get(hid, 0) + 1
        
        most_played_hero_id = None
        most_played_count = 0
        if hero_counts:
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

    async def get_hero_data(self, hero_id: int) -> dict:
        """Get hero detailed data by ID using OpenDota (rarely changes)."""
        if not self.hero_cache:
            url = f"{self.OPENDOTA_URL}/heroes"
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
        """Return some info for debug."""
        return f"Stratz debugging enabled for {steam_id}"
