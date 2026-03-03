import httpx
import logging
from typing import Dict, Optional

logger = logging.getLogger("FlareSolverrClient")
if not logger.handlers:
    import sys
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

class FlareSolverrClient:
    def __init__(self, endpoint: str = "http://localhost:8191/v1"):
        self.endpoint = endpoint
        self._cached_cookies: Dict[str, str] = {}
        self._cached_user_agent: Optional[str] = None

    async def fetch_clearance(self, target_url: str = "https://arena.ai/?mode=direct", timeout_ms: int = 60000) -> bool:
        """
        Calls FlareSolverr to solve any Cloudflare blocks on the given target_url.
        Extracts and caches the cookies and the generated User-Agent.
        Returns True if successful.
        """
        logger.info(f"Asking FlareSolverr to solve challenge for {target_url}...")
        
        payload = {
            "cmd": "request.get",
            "url": target_url,
            "maxTimeout": timeout_ms
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(self.endpoint, json=payload, timeout=timeout_ms / 1000.0 + 10.0)
                response.raise_for_status()
                
                data = response.json()
                if data.get("status") == "ok":
                    solution = data.get("solution", {})
                    
                    # Extract the User-Agent
                    user_agent = solution.get("userAgent")
                    if user_agent:
                        self._cached_user_agent = user_agent
                        logger.info(f"FlareSolverr extracted User-Agent: {user_agent[:40]}...")

                    # Extract all cookies
                    cookies_list = solution.get("cookies", [])
                    new_cookies = {}
                    for c in cookies_list:
                        name = c.get("name")
                        val = c.get("value")
                        if name and val:
                            new_cookies[name] = val
                            
                    if new_cookies:
                        self._cached_cookies.update(new_cookies)
                        logger.info(f"FlareSolverr extracted {len(new_cookies)} cookies (including cf_clearance).")
                        
                        # MINT AUTHENTICATION COOKIE
                        await self._mint_anonymous_auth_token(new_cookies, user_agent)
                        
                        return True
                else:
                    logger.error(f"FlareSolverr returned status: {data.get('status')}. Message: {data.get('message')}")
                    return False
        except Exception as e:
            logger.error(f"Failed to communicate with FlareSolverr: {e}")
            return False
            
        return False

    async def _mint_anonymous_auth_token(self, cf_cookies: dict, user_agent: str):
        """
        Once CF Clearance is obtained, hits LMArena directly to register an anonymous user 
        and extracts the `arena-auth-prod-v1` session token.
        """
        logger.info("Attempting to mint a fresh LMArena auth cookie via API...")
        
        headers = {
            "User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Origin": "https://arena.ai",
            "Referer": "https://arena.ai/?mode=direct"
        }
        
        # In modern LMArena, providing a simple turnstile token is enough to attempt signup
        # Some versions just require empty strings if Turnstile is bypassed by CF Clearance!
        payload = {
            "turnstileToken": "dummy_turnstile_token",
            "recaptchaToken": "dummy_recaptcha_token",
            "provisionalUserId": cf_cookies.get("provisional_user_id", "")
        }
        
        try:
            async with httpx.AsyncClient(cookies=cf_cookies) as client:
                response = await client.post(
                    "https://arena.ai/nextjs-api/sign-up", 
                    json=payload, 
                    headers=headers,
                    timeout=15.0
                )
                
                # Check cookies for the new auth token
                set_cookies = response.cookies
                if "arena-auth-prod-v1" in set_cookies:
                    logger.info("Successfully harvested arena-auth-prod-v1 from headers!")
                    self._cached_cookies["arena-auth-prod-v1"] = set_cookies.get("arena-auth-prod-v1")
                    return
                
                # Sometimes it is appended as base64 in the body response
                try:
                    import base64
                    import json
                    body = response.json()
                    if isinstance(body, dict) and "session" in body:
                        raw = json.dumps(body["session"], separators=(",", ":")).encode("utf-8")
                        b64 = base64.b64encode(raw).decode("utf-8").rstrip("=")
                        val = "base64-" + b64
                        self._cached_cookies["arena-auth-prod-v1"] = val
                        logger.info("Successfully extracted arena-auth-prod-v1 from JSON body!")
                        return
                except:
                    pass
                    
                logger.warning(f"LMArena returned {response.status_code}, but no auth cookie was minted. Payload: {response.text[:200]}")
        except Exception as e:
            logger.error(f"Failed to mint anonymous auth token: {e}")

    def get_cookies(self) -> Dict[str, str]:
        return self._cached_cookies.copy()

    def get_user_agent(self) -> Optional[str]:
        return self._cached_user_agent

# Global instance
flaresolverr_client = FlareSolverrClient()
