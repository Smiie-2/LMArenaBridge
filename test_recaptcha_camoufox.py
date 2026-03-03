import asyncio
import os
from camoufox.async_api import AsyncCamoufox

async def main():
    sitekey = "6Led_uYrAAAAAKjxDIF5VLEB0P1WzJclP4Q6yEps"
    print(f"Testing Camoufox reCAPTCHA injection with sitekey {sitekey}")
    
    async with AsyncCamoufox(headless=True, main_world_eval=True) as browser:
        page = await browser.new_page(bypass_csp=True)
        page.on("console", lambda msg: print(f"CONSOLE: {msg.text}"))
        
        print("Navigating to arena.ai...")
        await page.goto("https://arena.ai/?mode=direct", wait_until="domcontentloaded")
        await asyncio.sleep(8)
        
        print("Injecting script via page.add_init_script or evaluate...")
        # Strategy A: Evaluate a script tag
        script_url = f"https://www.google.com/recaptcha/enterprise.js?render={sitekey}"
        res = await page.evaluate(f"""
            (async () => {{
                try {{
                    const s = document.createElement('script');
                    s.src = '{script_url}';
                    document.head.appendChild(s);
                    return 'ok';
                }} catch (e) {{ return e.message; }}
            }})()
        """)
        print(f"Inject Result: {res}")
        
        print("Waiting 10s for grecaptcha...")
        for i in range(10):
            has_g = await page.evaluate("typeof window.grecaptcha !== 'undefined'")
            has_wrapped_g = await page.evaluate("typeof window.wrappedJSObject?.grecaptcha !== 'undefined'")
            has_ent = await page.evaluate("typeof window.grecaptcha?.enterprise !== 'undefined'")
            print(f"Wait {i}... grecaptcha: {has_g}, wrapped: {has_wrapped_g}, enterprise: {has_ent}")
            if has_ent or (has_wrapped_g and await page.evaluate("typeof window.wrappedJSObject.grecaptcha.enterprise !== 'undefined'")):
                print("FOUND IT! Executing...")
                token = await page.evaluate(f"""
                    (async () => {{
                        return new Promise((resolve, reject) => {{
                            const w = window.wrappedJSObject || window;
                            w.grecaptcha.enterprise.ready(async () => {{
                                try {{
                                    const params = new w.Object();
                                    params.action = 'chat_submit';
                                    const t = await w.grecaptcha.enterprise.execute('{sitekey}', params);
                                    resolve(t);
                                }} catch (e) {{
                                    reject(e.message || String(e));
                                }}
                            }});
                        }});
                    }})()
                """)
                print(f"Token: {{token[:30]}}...")
                break
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
