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
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json"
            }
        
        self.hero_cache = {}
        self.item_id_map = {}
        self.ability_cache = {}

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
                    logger.error(f"Stratz request failed: {r.status_code}")
                    return None
                
                try:
                    resp_json = r.json()
                except Exception:
                    logger.error("Stratz returned non-JSON response (possibly Cloudflare block)")
                    return None

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

    async def get_abilities_dict(self) -> dict:
        """Fetch abilities/talents info from Stratz constants."""
        if not self.ability_cache:
            # We use GraphQL to fetch all abilities for mapping
            query = """
            {
              constants {
                abilities {
                  id
                  name
                  displayName
                  description
                  isTalent
                }
              }
            }
            """
            data = await self._query_stratz(query)
            if data and "data" in data and "constants" in data["data"]:
                for ability in data["data"]["constants"]["abilities"]:
                    self.ability_cache[ability["id"]] = ability
        return self.ability_cache

    async def get_all_heroes(self) -> dict:
        """Fetch all heroes constants from OpenDota."""
        url = f"{self.OPENDOTA_URL}/constants/heroes"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(url, headers=self.headers, timeout=15.0)
                if r.status_code == 200:
                    return r.json()
            except Exception as e:
                logger.error(f"Error fetching all heroes: {e}")
        return {}

    async def get_all_items_full(self) -> dict:
        """Fetch all items constants with full data from OpenDota."""
        url = f"{self.OPENDOTA_URL}/constants/items"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(url, headers=self.headers, timeout=15.0)
                if r.status_code == 200:
                    return r.json()
            except Exception as e:
                logger.error(f"Error fetching all items: {e}")
        return {}

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
        """Get latest match. Tries Stratz first, falls back to OpenDota."""
        # Try Stratz first
        result = await self._get_latest_match_stratz(steam_id)
        if result:
            return result
        
        # Fallback to OpenDota
        logger.info(f"Stratz failed for {steam_id}, trying OpenDota...")
        return await self._get_latest_match_opendota(steam_id)
    
    async def _get_latest_match_stratz(self, steam_id: int) -> dict | None:
        """Get latest match via Stratz GraphQL."""
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
                hero {
                  id
                  shortName
                }
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
                # Abilities and talents
                abilities {
                  abilityId
                  level
                  isTalent
                }
                # Detailed stats for timings and timeline
                stats {
                    networthTimeline
                    itemPurchases {
                        itemId
                        time
                    }
                }
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
        
        player_stats = None
        for p in latest.get("players", []):
            if p.get("steamAccountId") == steam_id:
                player_stats = p
                break
        
        if not player_stats:
            return None
        
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
                "item_ids": item_ids,
                "items_urls": items_urls,
                "neutral_url": neutral_url,
                "hero_short_name": player_stats.get("hero", {}).get("shortName"),
                "item_purchases": player_stats.get("stats", {}).get("itemPurchases", []),
                "abilities": player_stats.get("abilities", []),
                "networth_timeline": player_stats.get("stats", {}).get("networthTimeline", [])
            }
        }

    async def _get_latest_match_opendota(self, steam_id: int) -> dict | None:
        """Get latest match via OpenDota REST API (fallback)."""
        # Get player profile
        player_name = "Unknown"
        url = f"{self.OPENDOTA_URL}/players/{steam_id}"
        async with httpx.AsyncClient() as client:
            try:
                # Force OpenDota to sync with Valve API first 
                # (essential because otherwise recentMatches is cached for a long time)
                refresh_url = f"{self.OPENDOTA_URL}/players/{steam_id}/refresh"
                await client.post(refresh_url, headers=self.headers, timeout=15.0)
            except Exception as e:
                logger.error(f"OpenDota refresh error: {e}")
                
            try:
                r = await client.get(url, headers=self.headers, timeout=15.0)
                if r.status_code == 200:
                    pdata = r.json()
                    player_name = pdata.get("profile", {}).get("personaname", "Unknown")
            except Exception as e:
                logger.error(f"OpenDota player fetch error: {e}")
            
            # Get recent matches
            try:
                r = await client.get(f"{self.OPENDOTA_URL}/players/{steam_id}/recentMatches", 
                                    headers=self.headers, timeout=15.0)
                if r.status_code != 200 or not r.json():
                    return None
                    
                matches = r.json()
                latest = matches[0]
                
                player_slot = latest.get("player_slot", 0)
                radiant_win = latest.get("radiant_win", False)
                is_radiant = player_slot < 128
                is_victory = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)
                match_id = latest.get("match_id")
                
                # Fetch full match details for items and net_worth
                items_urls = [None] * 6
                neutral_url = None
                net_worth = 0
                try:
                    rm = await client.get(f"{self.OPENDOTA_URL}/matches/{match_id}",
                                         headers=self.headers, timeout=15.0)
                    if rm.status_code == 200:
                        match_details = rm.json()
                        for p in match_details.get("players", []):
                            if p.get("player_slot") == player_slot:
                                item_ids = [p.get(f"item_{i}") for i in range(6)]
                                neutral_id = p.get("item_neutral")
                                net_worth = p.get("net_worth", 0)
                                items_map = await self.get_items_dict()
                                items_urls = [items_map.get(iid) for iid in item_ids]
                                neutral_url = items_map.get(neutral_id) if neutral_id else None
                                break
                except Exception as e:
                    logger.error(f"OpenDota match details error: {e}")
                
                return {
                    "player_name": player_name,
                    "match": {
                        "id": str(match_id),
                        "duration": latest.get("duration")
                    },
                    "player_match": {
                        "isVictory": is_victory,
                        "hero_id": latest.get("hero_id"),
                        "numKills": latest.get("kills", 0),
                        "numDeaths": latest.get("deaths", 0),
                        "numAssists": latest.get("assists", 0),
                        "xp_per_min": latest.get("xp_per_min", 0),
                        "gold_per_min": latest.get("gold_per_min", 0),
                        "net_worth": net_worth,
                        "lobby_type": latest.get("lobby_type"),
                        "game_mode": latest.get("game_mode"),
                        "items_urls": items_urls,
                        "neutral_url": neutral_url
                    }
                }
            except Exception as e:
                logger.error(f"OpenDota matches error: {e}")
                return None

    async def get_recent_stats(self, steam_id: int) -> dict | None:
        """Get winrate and favorite hero. Tries Stratz first, falls back to OpenDota."""
        result = await self._get_recent_stats_stratz(steam_id)
        if result:
            return result
        
        logger.info(f"Stratz recent stats failed for {steam_id}, trying OpenDota...")
        return await self._get_recent_stats_opendota(steam_id)

    async def _get_recent_stats_stratz(self, steam_id: int) -> dict | None:
        """Get recent stats via Stratz."""
        query = """
        query($steamId: Long!) {
          player(steamAccountId: $steamId) {
            matches(take: 20) {
              players {
                steamAccountId
                isVictory
                heroId
                numKills
                numDeaths
                numAssists
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
            
        return self._calc_recent_stats(matches, steam_id, source="stratz")
    
    async def _get_recent_stats_opendota(self, steam_id: int) -> dict | None:
        """Get recent stats via OpenDota (fallback)."""
        url = f"{self.OPENDOTA_URL}/players/{steam_id}/recentMatches"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(url, headers=self.headers, timeout=15.0)
                if r.status_code != 200:
                    return None
                matches = r.json()
                if not matches:
                    return None
                return self._calc_recent_stats(matches, steam_id, source="opendota")
            except Exception as e:
                logger.error(f"OpenDota recent stats error: {e}")
                return None
    
    def _calc_recent_stats(self, matches: list, steam_id: int, source: str) -> dict | None:
        """Calculate win/loss, top-3 heroes, and best KDA from match list."""
        wins = 0
        losses = 0
        hero_stats = {}  # {hero_id: {"count": N, "wins": N}}
        best_kda = {"hero_id": None, "kills": 0, "deaths": 0, "assists": 0, "kda_value": 0}
        
        for m in matches:
            if source == "stratz":
                p = None
                for mp in m.get("players", []):
                    if mp.get("steamAccountId") == steam_id:
                        p = mp
                        break
                if not p:
                    continue
                is_victory = p["isVictory"]
                hid = p["heroId"]
                kills = p.get("numKills", 0)
                deaths = p.get("numDeaths", 0)
                assists = p.get("numAssists", 0)
            else:  # opendota
                player_slot = m.get("player_slot", 0)
                radiant_win = m.get("radiant_win", False)
                is_radiant = player_slot < 128
                is_victory = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)
                hid = m.get("hero_id")
                kills = m.get("kills", 0)
                deaths = m.get("deaths", 0)
                assists = m.get("assists", 0)
            
            if is_victory:
                wins += 1
            else:
                losses += 1
            
            if hid:
                if hid not in hero_stats:
                    hero_stats[hid] = {"count": 0, "wins": 0}
                hero_stats[hid]["count"] += 1
                if is_victory:
                    hero_stats[hid]["wins"] += 1
            
            # Track best KDA
            kda_value = (kills + assists) / max(1, deaths)
            if kda_value > best_kda["kda_value"]:
                best_kda = {
                    "hero_id": hid,
                    "kills": kills,
                    "deaths": deaths,
                    "assists": assists,
                    "kda_value": round(kda_value, 1)
                }
        
        # Build top-3 heroes sorted by count
        top_heroes = []
        sorted_heroes = sorted(hero_stats.items(), key=lambda x: x[1]["count"], reverse=True)[:3]
        for hid, stats in sorted_heroes:
            wr = round(stats["wins"] / stats["count"] * 100) if stats["count"] > 0 else 0
            top_heroes.append({
                "hero_id": hid,
                "count": stats["count"],
                "wins": stats["wins"],
                "winrate": wr
            })
            
        total = wins + losses
        winrate = (wins / total * 100) if total > 0 else 0.0
        
        return {
            "wins": wins,
            "losses": losses,
            "winrate_percent": round(winrate, 1),
            "top_heroes": top_heroes,
            "best_kda": best_kda if best_kda["hero_id"] else None
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
