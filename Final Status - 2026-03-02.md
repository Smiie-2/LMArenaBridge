# Final Status - 2026-03-02

### 🛠️ What Has Been Changed

1. **Replaced `httpx` with `curl_cffi`**
   * Modified `src/main.py` (`make_request_with_retry`) to use `curl_cffi.AsyncSession` instead of standard `httpx` for non-streaming requests.
   * Modified `src/auth.py` (token refresh loop) to use `curl_cffi.AsyncSession`.
   * Modified `src/transport.py` (general requests) to use `curl_cffi.AsyncSession`.
2. **Standardized TLS Impersonation and User-Agents**
   * Initially attempted to use `impersonate="firefox117"` to match Camoufox, but learned it was unsupported by the library.
   * Upgraded all `curl_cffi` instances across the codebase to use `impersonate="firefox133"`.
   * Updated `config.json` to hardcode a matching Firefox 133 User-Agent header (`Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0`).
3. **Tested Live API Endpoints**
   * Restarted the bridge server and executed multiple `curl` commands to test the `/api/v1/models` and `/api/v1/chat/completions` endpoints natively to verify whether the 403 Forbidden errors were resolved.

---

### 🔍 Key Findings

1. **`curl_cffi` Version Limitations**
   * During server initialization, the bridge crashed with `Impersonating firefox117 is not supported`. Upon inspecting the `curl_cffi` source code, I found that the natively supported Firefox fingerprint versions are strictly constrained to `133`, `135`, and `144`.
2. **TLS Fingerprint Conflicts**
   * The core reason for the "recaptcha validation failed" error is a mismatch between the TLS fingerprints being sent by the headless scripts (Python `httpx` / Cloudscraper) and the browser (Camoufox). Arena.ai detects the discrepancy between the underlying TLS hello and the HTTP User-Agent.
3. **The "Bypass" Bug (Crucial Discovery)**
   * Despite adding `curl_cffi` to `make_request_with_retry`, **the `403 Forbidden` error still occurs.**
   * **Why?** I traced the server logs and discovered a logic flaw in `src/main.py` (around line 4187). For non-streaming requests, the server invokes `fetch_lmarena_stream_via_flaresolverr()` *first*. This function explicitly relies on standard `httpx` (lacking TLS fingerprinting) to make the request via the FlareSolverr proxy. 
   * As a result, our highly-secure `curl_cffi` logic is being skipped entirely, resulting in Cloudflare/Arena catching the raw `httpx` fingerprint and denying the request.

---

### 📊 Current Status

* **Server State:** Stable. The Uvicorn server boots up successfully, Camoufox successfully mints standard reCAPTCHA v3 tokens natively without hanging, and API endpoints are responsive. 
* **API State:** **Failing.** When sending a prompt to `/api/v1/chat/completions` using a valid model (e.g., `gpt-5-chat`), the underlying request to Arena.ai hits a `403 Forbidden: {"error":"recaptcha validation failed"}`.
* **Next Action Required:** We must refactor how `main.py` and `transport.py` route non-streaming API requests. We need to disable the `httpx` FlareSolverr fallback or refactor FlareSolverr to route via `curl_cffi` so that **all** requests leaving the bridge uniformly reflect the exact same Firefox 133 TLS network fingerprint.
