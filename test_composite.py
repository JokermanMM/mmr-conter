import asyncio
from main import generate_composite_image

async def test():
    hero = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/spirit_breaker.png"
    items = [
        "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/blink.png",
        "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/black_king_bar.png",
        "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/power_treads.png",
        "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/heart.png",
        None,
        None
    ]
    neutral = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/titan_sliver.png"
    
    out = await generate_composite_image(hero, 12, items, neutral)
    with open("test_out.png", "wb") as f:
        f.write(out.read())
    print("Test image saved to test_out.png")

asyncio.run(test())
