import re

def inject_flaresolverr_loop():
    with open("src/main.py", "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Add the import for flaresolverr_client
    if "from .flaresolverr_client import flaresolverr_client" not in content:
        content = content.replace("from .auth import (", "from .flaresolverr_client import flaresolverr_client\nfrom .auth import (")

    # 2. Add the background worker loop function before the lifespan
    bg_worker_code = """
async def flaresolverr_cookie_worker():
    \"\"\"Background task to periodically fetch cookies from FlareSolverr and stash them in config.\"\"\"
    debug_print("🚀 Starting FlareSolverr background cookie worker...")
    while True:
        try:
            # Tell flaresolverr to go grab cookies
            success = await flaresolverr_client.fetch_clearance(target_url="https://lmarena.ai/?mode=direct", timeout_ms=60000)
            if success:
                cookies = flaresolverr_client.get_cookies()
                ua = flaresolverr_client.get_user_agent()
                
                # Load current config 
                cfg = get_config()
                updated = False
                
                if "cf_clearance" in cookies and cfg.get("cf_clearance") != cookies["cf_clearance"]:
                    cfg["cf_clearance"] = cookies["cf_clearance"]
                    updated = True
                
                if "provisional_user_id" in cookies and cfg.get("provisional_user_id") != cookies["provisional_user_id"]:
                    cfg["provisional_user_id"] = cookies["provisional_user_id"]
                    updated = True
                    
                if ua and cfg.get("user_agent") != ua:
                    cfg["user_agent"] = ua
                    updated = True
                    
                # If we got a new auth token!
                if "arena-auth-prod-v1" in cookies:
                    new_token = cookies["arena-auth-prod-v1"]
                    tokens = cfg.get("auth_tokens", [])
                    if new_token not in tokens:
                        tokens.insert(0, new_token)
                        cfg["auth_tokens"] = tokens
                        updated = True
                        
                if updated:
                    save_config(cfg)
                    debug_print("✅ FlareSolverr cookies saved to config!")
            else:
                debug_print("⚠️ FlareSolverr cookie fetch returned False.")
        except Exception as e:
            debug_print(f"❌ Error in flaresolverr_cookie_worker: {e}")
            
        # Run every 10 minutes to ensure cookies don't expire
        await asyncio.sleep(600)

@asynccontextmanager
"""
    content = content.replace("@asynccontextmanager\n", bg_worker_code)

    # 3. Add to lifespan
    lifespan_code = """
async def lifespan(app: FastAPI):
    # Start the flaresolverr background loop
    flaresolverr_task = asyncio.create_task(flaresolverr_cookie_worker())
    try:
        await startup_event()
    except Exception as e:
        debug_print(f"❌ Error during startup: {e}")
    yield
    try:
        flaresolverr_task.cancel()
    except Exception as e:
        pass
"""
    
    # Replace the existing lifespan
    pattern = re.compile(r"async def lifespan\(app: FastAPI\):.*?yield.*?debug_print\(f\"❌ Error during shutdown: \{e\}\"\)", re.DOTALL)
    content = pattern.sub(lifespan_code.strip(), content)
    
    with open("src/main.py", "w", encoding="utf-8") as f:
        f.write(content)

inject_flaresolverr_loop()
