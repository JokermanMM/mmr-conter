import httpx
import asyncio

async def main():
    url = "https://api.opendota.com/api/heroes"
    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        heroes = r.json()
        # Find Spirit Breaker
        sb = next((h for h in heroes if h["localized_name"] == "Spirit Breaker"), None)
        print("Spirit Breaker data:", sb)
        
        # Check standard URL
        if sb:
            sys_name = sb["name"].replace("npc_dota_hero_", "")
            url = f"https://api.opendota.com/apps/dota2/images/dota_react/heroes/{sys_name}.png"
            print("Trying React URL:", url)
            r2 = await client.head(url)
            print("Status React:", r2.status_code)
            
            url2 = f"https://api.opendota.com/apps/dota2/images/heroes/{sys_name}_full.png"
            print("Trying Legacy URL:", url2)
            r3 = await client.head(url2)
            print("Status Legacy:", r3.status_code)

if __name__ == "__main__":
    asyncio.run(main())
