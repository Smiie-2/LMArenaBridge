"""
reCAPTCHA and browser challenge handling for LMArenaBridge.

Handles:
- reCAPTCHA v3 token minting via Chrome (Playwright) and Camoufox
- reCAPTCHA v3 token caching and refresh
- Camoufox anonymous user signup (Turnstile)
- Finding Chrome/Edge executable
- Provisional user ID injection into browser context
- LMArena auth cookie recovery from localStorage

Cross-module globals (_m().RECAPTCHA_TOKEN, _m().RECAPTCHA_EXPIRY, _m().SUPABASE_ANON_KEY) are
accessed via _m() late-import of main so test patches remain effective.
"""

import asyncio
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

_CONSECUTIVE_RECAPTCHA_FAILURES = 0
_RECAPTCHA_CIRCUIT_BREAKER_UNTIL = 0.0


def _m():
    """Late import of main module so tests can patch main.X and it is reflected here."""
    from . import main
    return main


def extract_recaptcha_params_from_text(text: str) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(text, str) or not text:
        return None, None

    discovered_sitekey: Optional[str] = None
    discovered_action: Optional[str] = None

    # 1) Prefer direct matches from execute(sitekey,{action:"..."}) when present.
    if "execute" in text and "action" in text:
        patterns = [
            r'grecaptcha\.enterprise\.execute\(\s*["\'](?P<sitekey>[0-9A-Za-z_-]{8,200})["\']\s*,\s*\{\s*(?:action|["\']action["\'])\s*:\s*["\'](?P<action>[^"\']{1,80})["\']',
            r'grecaptcha\.execute\(\s*["\'](?P<sitekey>[0-9A-Za-z_-]{8,200})["\']\s*,\s*\{\s*(?:action|["\']action["\'])\s*:\s*["\'](?P<action>[^"\']{1,80})["\']',
            # Fallback for minified code that aliases grecaptcha to another identifier.
            r'\.execute\(\s*["\'](?P<sitekey>6[0-9A-Za-z_-]{8,200})["\']\s*,\s*\{\s*(?:action|["\']action["\'])\s*:\s*["\'](?P<action>[^"\']{1,80})["\']',
        ]
        for pattern in patterns:
            try:
                match = re.search(pattern, text)
            except re.error:
                continue
            if not match:
                continue
            sitekey = str(match.group("sitekey") or "").strip()
            action = str(match.group("action") or "").strip()
            if sitekey and action:
                return sitekey, action

    # 2) Discover sitekey from the enterprise.js/api.js render URL (common in HTML/JS chunks).
    # Example: https://www.google.com/recaptcha/enterprise.js?render=SITEKEY
    sitekey_patterns = [
        r'recaptcha/(?:enterprise|api)\.js\?render=(?P<sitekey>[0-9A-Za-z_-]{8,200})',
        r'(?:enterprise|api)\.js\?render=(?P<sitekey>[0-9A-Za-z_-]{8,200})',
    ]
    for pattern in sitekey_patterns:
        try:
            match = re.search(pattern, text)
        except re.error:
            continue
        if not match:
            continue
        sitekey = str(match.group("sitekey") or "").strip()
        if sitekey:
            discovered_sitekey = sitekey
            break

    # 3) Discover action from headers/constants in client-side code.
    if "recaptcha" in text.lower() or "X-Recaptcha-Action" in text or "x-recaptcha-action" in text:
        action_patterns = [
            r'X-Recaptcha-Action["\']\s*[:=]\s*["\'](?P<action>[^"\']{1,80})["\']',
            r'X-Recaptcha-Action["\']\s*,\s*["\'](?P<action>[^"\']{1,80})["\']',
            r'x-recaptcha-action["\']\s*[:=]\s*["\'](?P<action>[^"\']{1,80})["\']',
        ]
        for pattern in action_patterns:
            try:
                match = re.search(pattern, text)
            except re.error:
                continue
            if not match:
                continue
            action = str(match.group("action") or "").strip()
            if action:
                discovered_action = action
                break

    return discovered_sitekey, discovered_action


def get_recaptcha_settings(config: Optional[dict] = None) -> tuple[str, str]:
    cfg = config or _m().get_config()
    sitekey = str((cfg or {}).get("recaptcha_sitekey") or "").strip()
    action = str((cfg or {}).get("recaptcha_action") or "").strip()
    if not sitekey:
        sitekey = _m().RECAPTCHA_SITEKEY
    if not action:
        action = _m().RECAPTCHA_ACTION
    return sitekey, action


async def _mint_recaptcha_v3_token_in_page(
    page,
    *,
    sitekey: str,
    action: str,
    grecaptcha_timeout_ms: int = 60000,
    grecaptcha_poll_ms: int = 250,
    outer_timeout_seconds: float = 70.0,
) -> str:
    """
    Best-effort reCAPTCHA v3 token minting inside an existing page.

    LMArena currently requires a `recaptchaToken` (action: "sign_up") for anonymous signup.
    """
    sitekey = str(sitekey or "").strip()
    action = str(action or "").strip()
    if not sitekey:
        return ""
    if not action:
        action = "sign_up"

    mint_js = """async ({ sitekey, action, timeoutMs, pollMs }) => {
      // LM_BRIDGE_MINT_RECAPTCHA_V3
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const w = (window.wrappedJSObject || window);
      const key = String(sitekey || '');
      const act = String(action || 'sign_up');
      const limit = Math.max(1000, Math.min(Number(timeoutMs || 60000), 180000));
      const poll = Math.max(50, Math.min(Number(pollMs || 250), 2000));
      const start = Date.now();

      const pickG = () => {
        const ent = w?.grecaptcha?.enterprise;
        if (ent && typeof ent.execute === 'function' && typeof ent.ready === 'function') return ent;
        const g = w?.grecaptcha;
        if (g && typeof g.execute === 'function' && typeof g.ready === 'function') return g;
        return null;
      };

      const inject = () => {
        try {
          if (w.__LM_BRIDGE_RECAPTCHA_INJECTED) return;
          w.__LM_BRIDGE_RECAPTCHA_INJECTED = true;
          const h = w.document?.head;
          if (!h) return;
          const urls = [
            'https://www.google.com/recaptcha/enterprise.js?render=' + encodeURIComponent(key),
            'https://www.google.com/recaptcha/api.js?render=' + encodeURIComponent(key),
          ];
          for (const u of urls) {
            const s = w.document.createElement('script');
            s.src = u;
            s.async = true;
            s.defer = true;
            h.appendChild(s);
          }
        } catch (e) { console.error('LM Bridge: reCAPTCHA v3 script injection failed', e); }
      };

      let injected = false;
      while ((Date.now() - start) < limit) {
        const g = pickG();
        if (g) {
          try {
            // g.ready can hang; guard with a short timeout.
            await Promise.race([
              new Promise((resolve) => { try { g.ready(resolve); } catch (e) { console.error('LM Bridge: reCAPTCHA v3 ready callback failed', e); resolve(true); } }),
              sleep(5000),
            ]);
          } catch (e) { console.error('LM Bridge: reCAPTCHA v3 ready wait failed', e); }
          try {
            // Firefox Xray wrappers: build params in the page compartment.
            const params = new w.Object();
            params.action = act;
            const tok = await g.execute(key, params);
            return String(tok || '');
          } catch (e) {
            console.error('LM Bridge: reCAPTCHA v3 execute failed', e);
            return '';
          }
        }
        if (!injected) { injected = true; inject(); }
        await sleep(poll);
      }
      return '';
    }"""

    try:
        tok = await asyncio.wait_for(
            page.evaluate(
                mint_js,
                {
                    "sitekey": sitekey,
                    "action": action,
                    "timeoutMs": int(grecaptcha_timeout_ms),
                    "pollMs": int(grecaptcha_poll_ms),
                },
            ),
            timeout=float(outer_timeout_seconds),
        )
    except asyncio.TimeoutError:
        _m().debug_print("reCAPTCHA v3 mint timed out in page.")
        tok = ""
    except Exception as e:
        _m().debug_print(f"Unexpected error minting reCAPTCHA v3 token in page: {type(e).__name__}: {e}")
        tok = ""
    return str(tok or "").strip()


async def _camoufox_proxy_signup_anonymous_user(
    page,
    *,
    turnstile_token: str,
    provisional_user_id: str,
    recaptcha_sitekey: str,
    recaptcha_action: str = "sign_up",
) -> Optional[dict]:
    """
    Perform LMArena anonymous signup using the same flow as the site JS:
    POST /nextjs-api/sign-up with {turnstileToken, recaptchaToken, provisionalUserId}.
    """
    turnstile_token = str(turnstile_token or "").strip()
    provisional_user_id = str(provisional_user_id or "").strip()
    recaptcha_sitekey = str(recaptcha_sitekey or "").strip()
    recaptcha_action = str(recaptcha_action or "").strip() or "sign_up"

    if not turnstile_token or not provisional_user_id:
        return None

    recaptcha_token = await _mint_recaptcha_v3_token_in_page(
        page,
        sitekey=recaptcha_sitekey,
        action=recaptcha_action,
    )
    if not recaptcha_token:
        _m().debug_print("⚠️ Camoufox proxy: reCAPTCHA mint failed for anonymous signup.")
        return None

    sign_up_js = """async ({ turnstileToken, recaptchaToken, provisionalUserId }) => {
      // LM_BRIDGE_ANON_SIGNUP
      const w = (window.wrappedJSObject || window);
      const opts = new w.Object();
      opts.method = 'POST';
      opts.credentials = 'include';
      // Match site behavior: let the browser set Content-Type for string bodies (text/plain;charset=UTF-8).
      opts.body = JSON.stringify({
        turnstileToken: String(turnstileToken || ''),
        recaptchaToken: String(recaptchaToken || ''),
        provisionalUserId: String(provisionalUserId || ''),
      });
      const res = await w.fetch('/nextjs-api/sign-up', opts);
      let text = '';
      try { text = await res.text(); } catch (e) { text = ''; }
      return { status: Number(res.status || 0), ok: !!res.ok, body: String(text || '') };
    }"""

    try:
        resp = await asyncio.wait_for(
            page.evaluate(
                sign_up_js,
                {
                    "turnstileToken": turnstile_token,
                    "recaptchaToken": recaptcha_token,
                    "provisionalUserId": provisional_user_id,
                },
            ),
            timeout=20.0,
        )
    except Exception as e:
        _m().debug_print(f"Unexpected error during anonymous signup evaluate: {type(e).__name__}: {e}")
        resp = None
    return resp if isinstance(resp, dict) else None


async def _set_provisional_user_id_in_browser(page, context, *, provisional_user_id: str) -> None:
    """
    Best-effort: keep the provisional user id consistent across cookies and storage.

    LMArena uses `provisional_user_id` to mint/restore anonymous sessions. If multiple storages disagree (e.g. a stale
    localStorage value vs a rotated cookie), /nextjs-api/sign-up can fail with confusing errors like "User already exists".
    """
    provisional_user_id = str(provisional_user_id or "").strip()
    if not provisional_user_id:
        return

    try:
        if context is not None:
            # Keep cookie variants in sync:
            # - Some sessions store `provisional_user_id` as a domain cookie on `.lmarena.ai`
            # - Others store it as a host-only cookie on `lmarena.ai` (via `url`)
            # If the two disagree, upstream can reject /nextjs-api/sign-up with confusing errors.
            await context.add_cookies(_m()._provisional_user_id_cookie_specs(provisional_user_id))
    except Exception as e:
        _m().debug_print(f"Failed to set provisional_user_id cookies in browser context: {type(e).__name__}: {e}")

    try:
        await page.evaluate(
            """(pid) => {
              const w = (window.wrappedJSObject || window);
              try { w.localStorage.setItem('provisional_user_id', String(pid || '')); } catch (e) {}
              return true;
            }""",
            provisional_user_id,
        )
    except Exception as e:
        _m().debug_print(f"Failed to set provisional_user_id in localStorage: {type(e).__name__}: {e}")


async def _maybe_inject_arena_auth_cookie_from_localstorage(page, context) -> Optional[str]:
    """
    Best-effort: recover a missing `arena-auth-prod-v1` cookie from browser storage.

    Some auth flows keep the Supabase session JSON in localStorage. If the cookie is missing but the session is still
    present, we can encode it into the `base64-<json>` cookie format and inject it.
    """
    if page is None or context is None:
        return None

    try:
        store = await page.evaluate(
            """() => {
              const w = (window.wrappedJSObject || window);
              try {
                const ls = w.localStorage;
                if (!ls) return {};
                const out = {};
                for (let i = 0; i < ls.length; i++) {
                  const k = ls.key(i);
                  if (!k) continue;
                  const key = String(k);
                  if (!(key.includes('auth') || key.includes('sb-') || key.includes('supabase') || key.includes('session'))) continue;
                  out[key] = String(ls.getItem(key) || '');
                }
                return out;
              } catch (e) {
                return {};
              }
            }"""
    )
    except Exception:
        return None

    if not isinstance(store, dict):
        return None

    for _, raw in list(store.items()):
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            cookie = _m().maybe_build_arena_auth_cookie_from_signup_response_body(text)
        except Exception:
            cookie = None
        if not cookie:
            continue
        try:
            if _m().is_arena_auth_token_expired(cookie, skew_seconds=0):
                continue
        except Exception:
            pass

        try:
            try:
                page_url = str(getattr(page, "url", "") or "")
            except Exception:
                page_url = ""
            await context.add_cookies(_m()._arena_auth_cookie_specs(cookie, page_url=page_url))
            _m()._capture_ephemeral_arena_auth_token_from_cookies([{"name": "arena-auth-prod-v1", "value": cookie}])
            _m().debug_print("🦊 Camoufox proxy: injected arena-auth cookie from localStorage session.")
            return cookie
        except Exception:
            continue

    return None


def find_chrome_executable() -> Optional[str]:
    configured = str(os.environ.get("CHROME_PATH") or "").strip()
    if configured and Path(configured).exists():
        return configured

    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Microsoft"
        / "Edge"
        / "Application"
        / "msedge.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Microsoft"
        / "Edge"
        / "Application"
        / "msedge.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    for name in ("google-chrome", "chrome", "chromium", "chromium-browser", "msedge"):
        resolved = shutil.which(name)
        if resolved:
            return resolved

    return None


async def get_recaptcha_v3_token_with_chrome(config: dict) -> Optional[str]:
    # Deprecated: FlareSolverr handles Cloudflare, and we no longer load browsers.
    return None


async def get_recaptcha_v3_token() -> Optional[str]:
    """
    Mint a real reCAPTCHA Enterprise v3 token using Camoufox (anti-detection browser).

    Camoufox is an anti-detection Firefox fork that evades both Cloudflare and
    Google reCAPTCHA Enterprise detection. We inject FlareSolverr's cookies to
    bypass Cloudflare, then navigate to arena.ai and execute grecaptcha.enterprise.execute().
    """
    global _CONSECUTIVE_RECAPTCHA_FAILURES, _RECAPTCHA_CIRCUIT_BREAKER_UNTIL
    
    if time.time() < _RECAPTCHA_CIRCUIT_BREAKER_UNTIL:
        _m().debug_print("⚠️ reCAPTCHA circuit breaker active. Skipping token minting.")
        return None

    from camoufox.async_api import AsyncCamoufox

    cfg = _m().get_config()
    sitekey = str(cfg.get("recaptcha_sitekey", "") or _m().RECAPTCHA_SITEKEY or "").strip()
    action = str(cfg.get("recaptcha_action", "") or _m().RECAPTCHA_ACTION or "sign_up").strip()

    if not sitekey:
        _m().debug_print("⚠️ No reCAPTCHA sitekey configured. Returning None.")
        return None

    _m().debug_print(f"🔐 Minting reCAPTCHA v3 token via Camoufox (sitekey={sitekey[:20]}..., action={action})")

    token = None
    try:
        # Build cookie list for Playwright context
        from .flaresolverr_client import flaresolverr_client as _fsc
        fs_cookies = _fsc.get_cookies()
        pw_cookies = []
        
        # Merge config cookies + FlareSolverr cookies (FlareSolverr wins)
        all_cookies = {}
        for k in ("cf_clearance", "provisional_user_id", "__cf_bm", "user_country_code"):
            val = str(cfg.get(k, "") or "").strip()
            if val:
                all_cookies[k] = val
        all_cookies.update({k: v for k, v in fs_cookies.items() if v})
        
        # Add arena-auth-prod-v1 if available
        auth_tokens = cfg.get("auth_tokens", [])
        if auth_tokens and isinstance(auth_tokens, list) and auth_tokens[0]:
            all_cookies["arena-auth-prod-v1"] = str(auth_tokens[0])
        
        for cname, cval in all_cookies.items():
            pw_cookies.append({
                "name": cname,
                "value": cval,
                "domain": ".arena.ai",
                "path": "/",
                "secure": True,
                "httpOnly": False,
            })
        
        _m().debug_print(f"🍪 Injecting {len(pw_cookies)} cookies into Camoufox: {[c['name'] for c in pw_cookies]}")
        
        # Use FlareSolverr's User-Agent to ensure Cloudflare/Google consistency
        ua = cfg.get("user_agent")
        async with AsyncCamoufox(
            headless=True, 
            main_world_eval=True
        ) as browser:
            # bypass_csp is CRITICAL for Strategy 3 (fetch+eval)
            page = await browser.new_page(bypass_csp=True, user_agent=ua)
            
            # Listen for console logs - vital for diagnosing reCAPTCHA errors
            page.on("console", lambda msg: _m().debug_print(f"  [Camoufox Console] {msg.type.upper()}: {msg.text}"))
            page.on("requestfailed", lambda req: _m().debug_print(f"  [Camoufox Net] FAILED: {req.url} ({req.failure.error_text if req.failure else 'Unknown error'})"))

            # Inject cookies BEFORE navigating (add_cookies is on the context, not browser)
            if pw_cookies:
                await page.context.add_cookies(pw_cookies)
            
            # Navigate to arena.ai
            _m().debug_print("🌐 Navigating Camoufox to arena.ai...")
            try:
                await page.goto("https://arena.ai/?mode=direct", wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                _m().debug_print(f"⚠️ Page navigation issue (may still work): {e}")

            # Wait for Cloudflare to pass (if challenged)
            for i in range(6):
                try:
                    title = await page.title()
                except Exception:
                    title = ""
                if "Just a moment" not in title:
                    break
                _m().debug_print(f"  ⏳ Cloudflare challenge... (attempt {i+1}/6)")
                await asyncio.sleep(5)

            # Strategy 3 (PROVEN): fetch the stub and eval it
            # This is the most reliable way to inject into the main world in Camoufox
            script_url = f"https://www.google.com/recaptcha/enterprise.js?render={sitekey}"
            _m().debug_print(f"📜 Injecting reCAPTCHA Enterprise script (Strategy 3)...")
            
            try:
                inject_js = f"""
                    (async () => {{
                        try {{
                            const script = document.createElement('script');
                            script.src = '{script_url}';
                            script.async = true;
                            script.defer = true;
                            script.id = 'recaptcha-injected';
                            document.head.appendChild(script);
                            return 'injected_ok';
                        }} catch (e) {{
                            return 'error_' + e.message;
                        }}
                    }})()
                """
                init_res = await page.evaluate(inject_js)
                _m().debug_print(f"  📄 Injection result: {{init_res}}")
            except Exception as e:
                _m().debug_print(f"  ❌ Injection failed: {{e}}")

            # Wait for grecaptcha.enterprise to become available
            ready = False
            for attempt in range(15): # Give it 15 seconds
                ready = await page.evaluate(
                    "typeof (window.wrappedJSObject || window).grecaptcha !== 'undefined' && typeof (window.wrappedJSObject || window).grecaptcha.enterprise !== 'undefined'"
                )
                if ready:
                    break
                # If we've waited 5 seconds and grecaptcha is there but not enterprise, try to wait more
                if attempt == 5:
                    partial = await page.evaluate("typeof (window.wrappedJSObject || window).grecaptcha")
                    _m().debug_print(f"  ⏳ Waiting for enterprise... (grecaptcha={partial})")
                await asyncio.sleep(1)
            
            if not ready:
                _m().debug_print("❌ grecaptcha.enterprise not available after injection.")
                return None

            _m().debug_print("✅ grecaptcha.enterprise available. Executing...")

            # Execute reCAPTCHA with a robust timeout and fallback
            js_code = f"""
            new Promise((resolve) => {{
                // Fallback timeout to prevent permanent hang
                const timer = setTimeout(() => resolve("TOKEN_EXECUTION_TIMEOUT"), 30000);
                const w = window.wrappedJSObject || window;
                
                const runExecute = async () => {{
                    try {{
                        const params = new w.Object();
                        params.action = '{action}';
                        const token = await w.grecaptcha.enterprise.execute('{sitekey}', params);
                        clearTimeout(timer);
                        resolve(token);
                    }} catch (e) {{
                        clearTimeout(timer);
                        resolve("EXECUTE_ERROR: " + (e.message || String(e)));
                    }}
                }};

                // Try ready() first, but if it doesn't fire in 5s, try direct execution
                let readyFired = false;
                w.grecaptcha.enterprise.ready(() => {{
                    readyFired = true;
                    runExecute();
                }});

                setTimeout(() => {{
                    if (!readyFired) {{
                        console.log("reCAPTCHA ready() callback hung, attempting direct execute...");
                        runExecute();
                    }}
                }}, 5000);
            }})
            """
            try:
                result = await page.evaluate(js_code)
            except Exception as e:
                _m().debug_print(f"❌ reCAPTCHA evaluate error: {e}")
                return None

            if result and isinstance(result, str) and len(result) > 50:
                _m().debug_print(f"✅ Got reCAPTCHA v3 token: {result[:30]}...")
                _m().RECAPTCHA_TOKEN = result
                _m().RECAPTCHA_EXPIRY = datetime.now(timezone.utc) + timedelta(seconds=110)
                _CONSECUTIVE_RECAPTCHA_FAILURES = 0  # Reset on success
                return result
            else:
                _m().debug_print(f"❌ Failed to get valid token. Result: {result}")
                _CONSECUTIVE_RECAPTCHA_FAILURES += 1
                if _CONSECUTIVE_RECAPTCHA_FAILURES >= 3:
                    _m().debug_print("🛑 reCAPTCHA failed 3 times in a row. Tripping circuit breaker for 5 minutes.")
                    _RECAPTCHA_CIRCUIT_BREAKER_UNTIL = time.time() + 300
                return None

    except Exception as e:
        _m().debug_print(f"❌ reCAPTCHA Camoufox minting error: {e}")
        import traceback
        _m().debug_print(traceback.format_exc())
        _CONSECUTIVE_RECAPTCHA_FAILURES += 1
        if _CONSECUTIVE_RECAPTCHA_FAILURES >= 3:
            _m().debug_print("🛑 reCAPTCHA failed 3 times in a row. Tripping circuit breaker for 5 minutes.")
            _RECAPTCHA_CIRCUIT_BREAKER_UNTIL = time.time() + 300
        return None


async def refresh_recaptcha_token(force_new: bool = False):
    """Checks if the global reCAPTCHA token is expired and refreshes it if necessary."""
    
    current_time = datetime.now(timezone.utc)
    if force_new:
        _m().RECAPTCHA_TOKEN = None
        _m().RECAPTCHA_EXPIRY = current_time - timedelta(days=365)
    # Unit tests should never launch real browser automation. Tests that need a token patch
    # `refresh_recaptcha_token` / `get_recaptcha_v3_token` explicitly.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return get_cached_recaptcha_token() or None
    # Check if token is expired (set a refresh margin of 10 seconds)
    if _m().RECAPTCHA_TOKEN is None or current_time > _m().RECAPTCHA_EXPIRY - timedelta(seconds=10):
        _m().debug_print("🔄 Recaptcha token expired or missing. Refreshing...")
        new_token = await get_recaptcha_v3_token()
        if new_token:
            _m().RECAPTCHA_TOKEN = new_token
            # reCAPTCHA v3 tokens typically last 120 seconds (2 minutes)
            _m().RECAPTCHA_EXPIRY = current_time + timedelta(seconds=120)
            _m().debug_print(f"✅ Recaptcha token refreshed, expires at {_m().RECAPTCHA_EXPIRY.isoformat()}")
            return new_token
        else:
            _m().debug_print("❌ Failed to refresh recaptcha token.")
            # Set a short retry delay if refresh fails
            _m().RECAPTCHA_EXPIRY = current_time + timedelta(seconds=10)
            return None
    
    return _m().RECAPTCHA_TOKEN


def get_cached_recaptcha_token() -> str:
    """Return the current reCAPTCHA v3 token if it's still valid, without refreshing."""
    token = _m().RECAPTCHA_TOKEN
    if not token:
        return ""
    current_time = datetime.now(timezone.utc)
    if current_time > _m().RECAPTCHA_EXPIRY - timedelta(seconds=10):
        return ""
    return str(token)