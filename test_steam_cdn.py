import httpx
import asyncio

async def main():
    names = ["spirit_breaker", "drow_ranger", "anti_mage"]
    async with httpx.AsyncClient() as client:
        for name in names:
            url = f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{name}.png"
            r = await client.head(url)
            print(f"{name}: {r.status_code}")

if __name__ == "__main__":
    asyncio.run(main())
