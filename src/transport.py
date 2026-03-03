"""
Transport layer for LMArenaBridge.

Contains the stream response classes, arena origin/cookie utilities, and the HTTPX 
fallback which leverages cookies harvested by FlareSolverr.
"""

import asyncio
import json
import httpx
from typing import Optional

def _m():
    """Late import of main module so tests can patch main.X and it is reflected here."""
    from . import main
    return main


class BrowserFetchStreamResponse:
    def __init__(
        self,
        status_code: int,
        headers: Optional[dict],
        text: str = "",
        method: str = "POST",
        url: str = "",
        lines_queue: Optional[asyncio.Queue] = None,
        done_event: Optional[asyncio.Event] = None,
    ):
        self.status_code = int(status_code or 0)
        self.headers = headers or {}
        self._text = text or ""
        self._method = str(method or "POST")
        self._url = str(url or "")
        self._lines_queue = lines_queue
        self._done_event = done_event

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def aclose(self) -> None:
        return None

    @property
    def text(self) -> str:
        return self._text

    async def aiter_lines(self):
        if self._lines_queue is not None:
            # Streaming mode
            while True:
                if self._done_event and self._done_event.is_set() and self._lines_queue.empty():
                     break
                try:
                    line = await asyncio.wait_for(self._lines_queue.get(), timeout=1.0)
                    if line is None:
                        break
                    yield line
                except asyncio.TimeoutError:
                    continue
        else:
            # Buffered mode
            for line in self._text.splitlines():
                yield line

    async def aread(self) -> bytes:
        if self._lines_queue is not None:
            collected = []
            async for line in self.aiter_lines():
                collected.append(line)
            self._text = "\n".join(collected)
            self._lines_queue = None
            self._done_event = None
        return self._text.encode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code == 0 or self.status_code >= 400:
            request = httpx.Request(self._method, self._url or "https://lmarena.ai/")
            response = httpx.Response(self.status_code or 502, request=request, content=self._text.encode("utf-8"))
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=request, response=response)


async def fetch_lmarena_stream_via_flaresolverr(
    http_method: str,
    url: str,
    payload: dict,
    auth_token: str,
    timeout_seconds: float = 120.0,
    max_recaptcha_attempts: int = 3,
) -> Optional[BrowserFetchStreamResponse]:
    """
    Fetch from LMArena using curl_cffi with Chrome TLS fingerprint impersonation.
    Cloudflare blocks standard httpx/requests due to TLS fingerprint detection.
    """
    cfg = _m().get_config()
    
    # Extract FlareSolverr harvested cookies & data
    user_agent = _m().normalize_user_agent_value(cfg.get("user_agent")) or _m().DEFAULT_USER_AGENT
    cf_clearance = str(cfg.get("cf_clearance", ""))
    provisional_user_id = str(cfg.get("provisional_user_id", ""))
    
    headers = {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Content-Type": "text/plain;charset=UTF-8",
        "Origin": _m().LMARENA_ORIGIN,
        "Referer": f"{_m().LMARENA_ORIGIN}/?mode=direct",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    
    cookies = {}
    if cf_clearance:
        cookies["cf_clearance"] = cf_clearance
    if auth_token:
        cookies["arena-auth-prod-v1"] = auth_token
    if provisional_user_id:
        cookies["provisional_user_id"] = provisional_user_id

    # Inject FlareSolverr cookies — these are FRESH from the latest challenge solve
    # and MUST override stale config cookies (especially cf_clearance which is
    # tied to the User-Agent and browser session that solved the Cloudflare challenge)
    from .flaresolverr_client import flaresolverr_client as _fsc
    fs_cookies = _fsc.get_cookies()
    for name, val in fs_cookies.items():
        if val:
            cookies[name] = val  # Override stale config values

    _m().debug_print(f"🍪 Cookies being sent: {list(cookies.keys())}")
    _m().debug_print(f"🍪 arena-auth-prod-v1 present: {'arena-auth-prod-v1' in cookies}, len={len(str(cookies.get('arena-auth-prod-v1', '')))}")
    _m().debug_print(f"🍪 cf_clearance present: {'cf_clearance' in cookies}, len={len(str(cookies.get('cf_clearance', '')))}")
    _m().debug_print(f"📋 Headers Origin: {headers.get('Origin')}, Referer: {headers.get('Referer')}")
    _m().debug_print(f"🔒 Using curl_cffi with Chrome TLS fingerprint impersonation")

    lines_queue = asyncio.Queue()
    done_event = asyncio.Event()

    try:
        from curl_cffi.requests import AsyncSession
        
        async with AsyncSession(impersonate="firefox133") as session:
            body = json.dumps(payload) if payload else None
            
            response = await session.request(
                http_method,
                url,
                headers=headers,
                cookies=cookies,
                data=body,
                timeout=timeout_seconds,
            )
            
            status_code = response.status_code
            resp_headers = dict(response.headers) if response.headers else {}
            resp_text = response.text or ""
            
            _m().debug_print(f"📥 curl_cffi response: status={status_code}, body_len={len(resp_text)}")
            
            # Split response into lines and put them in the queue
            if resp_text:
                for line in resp_text.split("\n"):
                    if line.strip():
                        await lines_queue.put(line)
            
            done_event.set()
            
            return BrowserFetchStreamResponse(
                status_code=status_code,
                headers=resp_headers,
                text=resp_text,
                method=http_method,
                url=url,
                lines_queue=lines_queue,
                done_event=done_event
            )
            
    except Exception as e:
        _m().debug_print(f"⚠️ fetch_lmarena_stream_via_flaresolverr failed: {e}")
        import traceback
        _m().debug_print(traceback.format_exc())
        return None
