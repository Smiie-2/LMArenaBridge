import asyncio
from src.main import get_config
from src.cloudscraper_client import cloudscraper_client
from src.recaptcha import get_recaptcha_v3_token

async def main():
    print("Initializing config...")
    get_config()
    
    print("Fetching Cloudflare cookies using Cloudscraper...")
    success = await cloudscraper_client.fetch_clearance("https://arena.ai/?mode=direct")
    print(f"Cloudscraper fetch success: {{success}}")
    
    print("Attempting to mint reCAPTCHA token using Camoufox...")
    token = await get_recaptcha_v3_token()
    
    if token:
        print(f"\nSUCCESS! Got token: {token[:40]}...")
    else:
        print("\nFAILURE! Did not get a token.")

if __name__ == "__main__":
    asyncio.run(main())
