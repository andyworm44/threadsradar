"""
手動登入 Threads，儲存 cookies 供 scraper 使用
執行後會開啟瀏覽器，你手動登入完成後按 Enter，cookies 就會自動存好
"""
import asyncio
import json
import os
from playwright.async_api import async_playwright

COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies.json")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-TW",
            timezone_id="Asia/Taipei",
        )
        page = await context.new_page()
        await page.goto("https://www.threads.com/login", timeout=20000)

        print("請在瀏覽器中手動登入 Threads，登入完成後程式會自動繼續...")
        await page.wait_for_url(lambda url: "login" not in url, timeout=300000)
        print("✅ 登入成功，儲存 cookies...")

        cookies = await context.cookies()
        with open(COOKIES_FILE, "w") as f:
            json.dump(cookies, f)

        print(f"✅ Cookies 已儲存（{len(cookies)} 個）")
        print("現在可以執行 python3 scraper.py 了")
        await browser.close()

asyncio.run(main())
