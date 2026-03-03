"""
Cloudscraper-based Cloudflare bypass client for LMArenaBridge.

Replaces FlareSolverr (Docker) with a pure-Python solution.
Cloudscraper handles Cloudflare JS challenges and Turnstile natively,
extracting cookies needed for authenticated API requests.
"""

import json
import logging
import time
from typing import Dict, Optional

import cloudscraper

logger = logging.getLogger("CloudscraperClient")
if not logger.handlers:
    import sys
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class CloudscraperClient:
    """Cloudflare bypass via cloudscraper — no Docker, no browser."""

    def __init__(self, captcha_config: Optional[dict] = None):
        self._cached_cookies: Dict[str, str] = {}
        self._cached_user_agent: Optional[str] = None
        self._last_fetch_time: float = 0.0
        self._captcha_config = captcha_config
        self._scraper: Optional[cloudscraper.CloudScraper] = None

    def _get_scraper(self) -> cloudscraper.CloudScraper:
        """Create or return cached scraper instance."""
        if self._scraper is None:
            kwargs = {
                "browser": "chrome",
            }
            if self._captcha_config:
                kwargs["captcha"] = self._captcha_config
            self._scraper = cloudscraper.create_scraper(**kwargs)
        return self._scraper

    def _reset_scraper(self):
        """Force a fresh scraper on next call."""
        self._scraper = None

    async def fetch_clearance(
        self,
        target_url: str = "https://arena.ai/?mode=direct",
        timeout_ms: int = 60000,
    ) -> bool:
        """
        Navigate to target_url via cloudscraper, solving any Cloudflare
        challenges. Extracts and caches cookies + User-Agent.

        Returns True if the page was fetched successfully (HTTP 200).
        """
        logger.info(f"Fetching {target_url} via cloudscraper...")

        try:
            scraper = self._get_scraper()
            response = scraper.get(target_url, timeout=timeout_ms / 1000.0)

            if response.status_code == 200:
                # Extract cookies
                new_cookies = dict(scraper.cookies)
                if new_cookies:
                    self._cached_cookies.update(new_cookies)
                    logger.info(
                        f"Extracted {len(new_cookies)} cookies: {list(new_cookies.keys())}"
                    )

                # Extract User-Agent from scraper headers
                ua = scraper.headers.get("User-Agent")
                if ua:
                    self._cached_user_agent = ua

                self._last_fetch_time = time.time()

                # Attempt to mint auth token from the page/session
                await self._mint_anonymous_auth_token(response)

                return True
            else:
                logger.warning(
                    f"Got HTTP {response.status_code} from {target_url}"
                )
                return False

        except cloudscraper.exceptions.CloudflareChallengeError as e:
            logger.error(f"Cloudflare challenge unsolved: {e}")
            self._reset_scraper()
            return False
        except Exception as e:
            logger.error(f"Failed to fetch via cloudscraper: {e}")
            self._reset_scraper()
            return False

    async def _mint_anonymous_auth_token(self, page_response) -> None:
        """
        After successfully loading arena.ai, attempt to trigger the
        anonymous signup flow to obtain an arena-auth-prod-v1 cookie.

        The signup requires a POST to /nextjs-api/sign-up with:
        - turnstileToken (from Cloudflare Turnstile — already solved)
        - recaptchaToken (we send empty, may or may not be accepted)
        - provisionalUserId (from cookie)
        """
        provisional_user_id = self._cached_cookies.get("provisional_user_id", "")
        if not provisional_user_id:
            logger.info("No provisional_user_id cookie — skipping auth mint.")
            return

        # If we already have an auth token, skip
        if self._cached_cookies.get("arena-auth-prod-v1"):
            logger.info("Already have arena-auth-prod-v1 — skipping mint.")
            return

        logger.info("Attempting anonymous signup via cloudscraper session...")

        scraper = self._get_scraper()

        payload = {
            "turnstileToken": "",
            "recaptchaToken": "",
            "provisionalUserId": provisional_user_id,
        }

        try:
            response = scraper.post(
                "https://arena.ai/nextjs-api/sign-up",
                data=json.dumps(payload),
                headers={
                    "Content-Type": "text/plain;charset=UTF-8",
                    "Origin": "https://arena.ai",
                    "Referer": "https://arena.ai/?mode=direct",
                },
                timeout=15.0,
            )

            logger.info(f"Signup response: HTTP {response.status_code}")

            # Check Set-Cookie for auth token
            new_cookies = dict(scraper.cookies)
            if "arena-auth-prod-v1" in new_cookies:
                self._cached_cookies["arena-auth-prod-v1"] = new_cookies[
                    "arena-auth-prod-v1"
                ]
                logger.info("✅ Got arena-auth-prod-v1 from signup cookies!")
                return

            # Try to extract from response body
            try:
                import base64

                body = response.json()
                if isinstance(body, dict) and "session" in body:
                    raw = json.dumps(
                        body["session"], separators=(",", ":")
                    ).encode("utf-8")
                    b64 = base64.b64encode(raw).decode("utf-8").rstrip("=")
                    val = "base64-" + b64
                    self._cached_cookies["arena-auth-prod-v1"] = val
                    logger.info(
                        "✅ Got arena-auth-prod-v1 from signup response body!"
                    )
                    return
            except Exception:
                pass

            logger.warning(
                f"Signup returned {response.status_code} but no auth cookie. "
                f"Body preview: {response.text[:200]}"
            )

        except Exception as e:
            logger.error(f"Signup request failed: {e}")

    def get_cookies(self) -> Dict[str, str]:
        """Return a copy of all cached cookies."""
        return self._cached_cookies.copy()

    def get_user_agent(self) -> Optional[str]:
        """Return the User-Agent used by the scraper."""
        return self._cached_user_agent

    def get_last_fetch_time(self) -> float:
        """Unix timestamp of the last successful fetch."""
        return self._last_fetch_time


# Global instance
cloudscraper_client = CloudscraperClient()
