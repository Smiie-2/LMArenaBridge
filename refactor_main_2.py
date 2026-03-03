import re
import os

def strip_main():
    with open("src/main.py", "r", encoding="utf-8") as f:
        content = f.read()
        
    # Replace the transport imports block
    old_imports_block = re.search(r"from \.transport import \((.*?)\)", content, re.DOTALL)
    if old_imports_block:
        new_import = "from .transport import (\n    BrowserFetchStreamResponse,\n    fetch_lmarena_stream_via_flaresolverr,\n)"
        content = content.replace(old_imports_block.group(0), new_import)
        
    # Remove proxy_manager
    content = re.sub(r"from \.proxy_manager import proxy_manager\n", "", content)
    
    # Remove specific camoufox/proxy routes from the chat completion endpoints
    # 1. userscript_proxy_is_active -> false block
    content = re.sub(r"proxy_active_at_start = _userscript_proxy_is_active\(\)", "proxy_active_at_start = False", content)
    
    # Actually, the entire fallback block in main.py is massive. Let's just manually replace the request loop with fetch_lmarena_stream_via_flaresolverr.
    # We can write out transport.py first, then we'll just fix main.py compilation errors.
    
    with open("src/main.py", "w", encoding="utf-8") as f:
        f.write(content)

strip_main()
