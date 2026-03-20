import httpx
import logging

logger = logging.getLogger(__name__)

class StratzClient:
    URL = "https://api.stratz.com/graphql"
    
    def __init__(self, api_token: str = None):
        self.headers = {
            "Content-Type": "application/json",
            "User-Agent": "Dota2MMRBot/1.0"
        }
        if api_token:
            self.headers["Authorization"] = f"Bearer {api_token}"

    async def get_latest_match(self, steam_id: int):
        # We'll use two queries or one combined to ensure we see the player even without matches
        query = """
        query($steamId: Long!) {
          player(steamAccountId: $steamId) {
            steamAccount {
              id
              name
            }
            matches(request: { take: 1 }) {
              id
              endDateTime
              players(steamAccountId: $steamId) {
                steamAccountId
                isVictory
                heroId
                afterMmr
                numKills
                numDeaths
                numAssists
                imp
                hero {
                  displayName
                }
              }
            }
          }
        }
        """
        variables = {"steamId": steam_id}
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.URL, 
                    json={"query": query, "variables": variables},
                    headers=self.headers,
                    timeout=15.0
                )
                response.raise_for_status()
                data = response.json()
                
                if "errors" in data:
                    logger.error(f"Stratz GraphQL Errors: {data['errors']}")
                    # If it's a specific error about matches but we have player info, we can proceed
                
                player_data = data.get("data", {}).get("player")
                if not player_data or not player_data.get("steamAccount"):
                    logger.warning(f"No player data found for {steam_id}")
                    return None
                
                player_name = player_data["steamAccount"]["name"]
                matches = player_data.get("matches", [])
                
                match = matches[0] if matches else None
                players = match.get("players", []) if match else []
                player_match = players[0] if players else None
                
                return {
                    "player_name": player_name,
                    "match": match,
                    "player_match": player_match
                }
            except Exception as e:
                logger.error(f"Error fetching Stratz data: {e}")
                return None

    async def raw_query(self, steam_id: int) -> str:
        """Return raw API response for debugging."""
        query = """
        query($steamId: Long!) {
          player(steamAccountId: $steamId) {
            steamAccount {
              id
              name
            }
          }
        }
        """
        variables = {"steamId": steam_id}
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.URL,
                    json={"query": query, "variables": variables},
                    headers=self.headers,
                    timeout=15.0
                )
                return f"Status: {response.status_code}\nHeaders: {dict(response.headers)}\nBody: {response.text[:1000]}"
            except Exception as e:
                return f"Error: {e}"
