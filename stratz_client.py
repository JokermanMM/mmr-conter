import httpx
import logging

logger = logging.getLogger(__name__)

class StratzClient:
    URL = "https://api.stratz.com/graphql"
    
    def __init__(self, api_token: str = None):
        self.headers = {"Content-Type": "application/json"}
        if api_token:
            self.headers["Authorization"] = f"Bearer {api_token}"

    async def get_latest_match(self, steam_id: int):
        query = """
        query($steamId: Long!) {
          player(id: $steamId) {
            steamAccount {
              id
              name
            }
            matches(request: { take: 1 }) {
              id
              endDateTime
              players(steamAccountId: $steamId) {
                afterMmr
                isVictory
                numKills
                numDeaths
                numAssists
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
                    logger.error(f"GraphQL Errors: {data['errors']}")
                    return None
                
                player_data = data.get("data", {}).get("player")
                if not player_data:
                    return None
                
                matches = player_data.get("matches", [])
                if not matches:
                    return {
                        "player_name": player_data["steamAccount"]["name"], 
                        "match": None, 
                        "player_match": None
                    }
                
                match = matches[0]
                players = match.get("players", [])
                player_match = players[0] if players else None
                
                if not player_match:
                    return {
                        "player_name": player_data["steamAccount"]["name"], 
                        "match": match, 
                        "player_match": None
                    }
                
                return {
                    "player_name": player_data["steamAccount"]["name"],
                    "match": match,
                    "player_match": player_match
                }
            except Exception as e:
                logger.error(f"Error fetching Stratz data: {e}")
                return None
