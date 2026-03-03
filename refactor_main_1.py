import os
import re

def refactor_main():
    main_path = "src/main.py"
    with open(main_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    # Remove proxy endpoints (lines 1934 to 2070 approximately)
    # the endpoints are: /proxy/tasks, /proxy/result/{task_id}, /proxy/ping, /proxy/stream/{task_id}
    # It starts at: proxy_pending_tasks: Dict...
    # Ends right before: @app.post("/v1/chat/completions")
    
    # Let's match from proxy_pending_tasks up to @app.post("/v1/chat/completions")
    pattern = re.compile(r"proxy_pending_tasks: Dict\[str, asyncio\.Future\] = \{\}.*?(?=@app\.post\(\"/v1/chat/completions\"\))", re.DOTALL)
    content = pattern.sub("", content)
    
    # Remove proxy imports
    content = re.sub(r"from \.proxy_manager import proxy_manager\n", "", content)
    
    # Remove proxy startup/shutdown
    content = re.sub(r"\s+await proxy_manager\.start\(\)", "", content)
    content = re.sub(r"\s+proxy_manager\.shutdown\(\)", "", content)
    
    # Remove camoufox startup
    content = re.sub(r"\s+# === \[Browser.*?(?=async def get_models)", "", content, flags=re.DOTALL)
    
    # Remove transport imports related to camoufox/proxy
    old_imports = """from .transport import (
    BrowserFetchStreamResponse,
    _camoufox_proxy_signup_anonymous_user,
    _get_userscript_proxy_queue,
    _userscript_proxy_is_active,
    _userscript_proxy_check_secret,
    _cleanup_userscript_proxy_jobs,
    _mark_userscript_proxy_inactive,
    _finalize_userscript_proxy_job,
    _normalize_userscript_proxy_url,
    fetch_lmarena_stream_via_userscript_proxy,
    fetch_lmarena_stream_via_chrome,
    fetch_lmarena_stream_via_camoufox,
    fetch_via_proxy_queue,
    push_proxy_chunk,
    camoufox_proxy_worker,
)"""
    new_imports = """from .transport import (
    BrowserFetchStreamResponse,
    fetch_lmarena_stream_via_flaresolverr,
)"""
    
    content = content.replace(old_imports, new_imports)
    
    with open(main_path, "w", encoding="utf-8") as f:
        f.write(content)
        
refactor_main()
