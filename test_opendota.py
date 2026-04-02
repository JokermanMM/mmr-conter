import asyncio
import httpx

async def fetch():
    async with httpx.AsyncClient() as c:
        r = await c.get('https://api.opendota.com/api/constants/items')
        data = r.json()
        print("Data size:", len(data))
        blink = data['blink']
        print(blink.get('id'), blink.get('dname'), blink.get('img'), blink.get('icon_name'))

asyncio.run(fetch())
