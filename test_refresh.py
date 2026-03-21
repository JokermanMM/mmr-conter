import httpx
import asyncio

async def main():
    steam_id = 299539763
    url = f"https://api.opendota.com/api/players/{steam_id}/refresh"
    async with httpx.AsyncClient() as client:
        r = await client.post(url)
        print("Refresh status:", r.status_code)
        print("Body:", r.text)

if __name__ == "__main__":
    asyncio.run(main())
