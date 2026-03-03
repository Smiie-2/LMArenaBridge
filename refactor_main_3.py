import re

def strip_main_part3():
    with open("src/main.py", "r", encoding="utf-8") as f:
        content = f.read()

    # The block inside main.py that handles the fallback for streaming. Let's use regex to match
    # if stream_context is None and use_browser_transports:
    #   browser_fetch_attempts = 5
    #   ...
    #   if stream_context is None:
    #       client = await...
    
    # Actually, we want to replace ALL fetching with fetch_lmarena_stream_via_flaresolverr if possible.
    # However, that is quite intrusive to do via regex on 4700 lines.
    # Let's cleanly replace the Chrome and Camoufox functions.
    
    # 1. Strip the huge streaming browser transports block
    pattern_streaming = re.compile(
        r"if stream_context is None and use_browser_transports:(?:.*?)if stream_context is None:\n\s+client = await stack\.enter_async_context\(httpx\.AsyncClient\(\)\)",
        re.DOTALL
    )
    
    replacement_streaming = """
                            if stream_context is None and use_browser_transports:
                                debug_print("🌐 Using FlareSolverr fetch transport for streaming...")
                                stream_context = await fetch_lmarena_stream_via_flaresolverr(
                                    http_method=http_method,
                                    url=url,
                                    payload=payload if isinstance(payload, dict) else {},
                                    auth_token=current_token,
                                    timeout_seconds=120,
                                )
                                transport_used = "flaresolverr" if stream_context else None
                                
                            if stream_context is None:
                                client = await stack.enter_async_context(httpx.AsyncClient())
"""
    
    content = pattern_streaming.sub(replacement_streaming, content)

    # 2. Strip the non-streaming fallback block
    pattern_non_streaming = re.compile(
        r"if use_chrome_fetch_for_model:.*?transport_used = \"(chrome|camoufox)\"",
        re.DOTALL
    )
    
    replacement_non_streaming = """if use_chrome_fetch_for_model:
                    try:
                        debug_print("🌐 Using FlareSolverr fetch transport for fallback...")
                        
                        response = await fetch_lmarena_stream_via_flaresolverr(
                            http_method=http_method,
                            url=url,
                            payload=payload if isinstance(payload, dict) else {},
                            auth_token=current_token,
                            timeout_seconds=120,
                        )
                        json_body = {}
                        if response:
                            text = await response.aread()
                            try:
                                json_body = json.loads(text)
                            except Exception:
                                pass
                            
                            response_obj = httpx.Response(
                                status_code=response.status_code,
                                headers=response.headers,
                                content=text,
                                request=httpx.Request(http_method, url)
                            )
                            return response_obj, json_body, "flaresolverr", recaptcha_token
                            
                    except Exception as e:
                        debug_print(f"⚠️ FlareSolverr fallback error: {e}")
"""
    content = pattern_non_streaming.sub(replacement_non_streaming, content)

    # In case there are multiple matches
    
    with open("src/main.py", "w", encoding="utf-8") as f:
        f.write(content)

strip_main_part3()
