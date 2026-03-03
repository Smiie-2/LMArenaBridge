import asyncio
from src.cloudscraper_client import cloudscraper_client

async def test():
    print("Testing cloudscraper_client.fetch_clearance()...")
    ok = await cloudscraper_client.fetch_clearance("https://arena.ai/?mode=direct", timeout_ms=30000)
    print(f"Success: {ok}")
    
    cookies = cloudscraper_client.get_cookies()
    print(f"Cookies ({len(cookies)}):")
    for k, v in cookies.items():
        print(f"  {k}: {v[:60]}..." if len(str(v)) > 60 else f"  {k}: {v}")
        
    ua = cloudscraper_client.get_user_agent()
    print(f"User-Agent: {ua[:80]}..." if ua and len(ua) > 80 else f"User-Agent: {ua}")
    
    # Check critical cookies
    has_provisional = "provisional_user_id" in cookies
    has_auth = "arena-auth-prod-v1" in cookies
    print("\nKey results:")
    print(f"  provisional_user_id: {'✅' if has_provisional else '❌'}")
    print(f"  arena-auth-prod-v1: {'✅' if has_auth else '❌ (expected — needs signup flow)'}")

if __name__ == "__main__":
    asyncio.run(test())
