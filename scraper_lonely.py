import asyncio
import json
import os
import threading
import webbrowser
import http.server
from datetime import datetime, timezone
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

OUTPUT_FILE  = os.path.join(os.path.dirname(__file__), "data_lonely.json")
COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies.json")
MAX_POSTS    = 5000
SCROLL_PER_TERM = 15  # 每個關鍵字滾動次數（越多越有機會撈到歷史高讚）

SEARCH_TERMS = [
    # 孤單寂寞
    "孤單", "寂寞", "孤獨", "一個人", "沒有人",
    "沒人", "孤零零", "孤僻", "空虛", "落寞",
    "冷清", "失落", "難過", "傷心", "心碎",
    # 交友相關
    "交友", "找朋友", "認識朋友", "找人聊天", "聊天",
    "交男友", "交女友", "找對象", "單身", "脫單",
    "戀愛", "喜歡一個人", "暗戀", "告白", "失戀",
    "分手", "復合", "前任", "曖昧", "被拒絕",
    # 情感需求
    "需要陪伴", "陪我", "想被愛", "渴望愛",
    "沒有人愛我", "沒人關心", "沒人在意",
    "想談戀愛", "想有人陪", "一個人好孤單",
    # 夜晚情緒
    "深夜", "失眠", "睡不著", "夜深了", "凌晨",
    "一個人的夜晚", "夜晚好孤單", "深夜心情",
]


EXTRACT_JS = """
() => {
    function parseNum(s) {
        if (!s) return 0;
        s = s.trim().replace(/,/g, '');
        if (/^\\d+\\.?\\d*K$/i.test(s)) return Math.round(parseFloat(s) * 1000);
        if (/^\\d+\\.?\\d*萬$/.test(s)) return Math.round(parseFloat(s) * 10000);
        if (/^\\d+$/.test(s)) return parseInt(s);
        return 0;
    }

    function getBtnCount(el, label) {
        const btn = el.querySelector('[aria-label="' + label + '"]');
        if (!btn) return 0;
        return parseNum((btn.parentElement?.innerText || '').trim());
    }

    const results = [];
    const articles = document.querySelectorAll('div[data-pressable-container]');
    articles.forEach((el) => {
        const link = el.querySelector('a[href*="/post/"]');
        const timeEl = el.querySelector('time');
        const datetime = timeEl ? timeEl.getAttribute('datetime') : null;
        const time_text = timeEl ? timeEl.innerText?.trim() : null;

        const usernameEl = el.querySelector('a[href^="/@"]') || el.querySelector('a[href^="/"]');
        let username = null;
        if (usernameEl) {
            const href = usernameEl.getAttribute('href') || '';
            const m = href.match(/^\\/(@?[\\w.]+)/);
            if (m) username = m[1].replace('@', '');
        }

        const likes   = getBtnCount(el, '讚');
        const replies = getBtnCount(el, '回覆');
        const reposts = getBtnCount(el, '轉發');

        const skipPatterns = [
            /^@?[a-zA-Z0-9_.]+$/,
            /^Translate$/i,
            /^\\d+\\.?\\d*[KkM萬]?$/,
            /^\\d{1,3}(,\\d{3})+$/,
            /^\\/$/,
        ];
        const lines = (el.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
        const content = lines.filter((l, idx) => {
            if (idx === 0 && /^[a-zA-Z0-9_.]+$/.test(l)) return false;
            return !skipPatterns.some(p => p.test(l));
        }).join('\\n').trim();

        if (content && content.length > 10) {
            results.push({
                text: content.substring(0, 400),
                url: link ? 'https://www.threads.com' + link.getAttribute('href') : null,
                username,
                time_text,
                datetime,
                likes,
                replies,
                reposts,
            });
        }
    });
    return results;
}
"""


async def login(page):
    username = os.getenv("THREADS_USERNAME")
    password = os.getenv("THREADS_PASSWORD")
    print("🔐 登入中...")
    await page.goto("https://www.threads.com/login", timeout=20000)
    await page.wait_for_timeout(2000)
    await page.fill('input[autocomplete="username"], input[name="username"], input[type="text"]', username)
    await page.wait_for_timeout(500)
    await page.fill('input[autocomplete="current-password"], input[name="password"], input[type="password"]', password)
    await page.wait_for_timeout(500)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(5000)
    print("✅ 登入完成")


async def scrape():
    all_posts: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-TW",
            timezone_id="Asia/Taipei",
        )
        page = await context.new_page()

        if os.path.exists(COOKIES_FILE):
            with open(COOKIES_FILE) as f:
                cookies = json.load(f)
            await context.add_cookies(cookies)
            print("🍪 使用已儲存的 cookies")
        else:
            await login(page)
            cookies = await context.cookies()
            with open(COOKIES_FILE, "w") as f:
                json.dump(cookies, f)
            print("🍪 Cookies 已儲存")

        # 先確認登入狀態
        await page.goto("https://www.threads.com/", timeout=20000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(2000)

        if "login" in page.url:
            print("⚠️  Cookie 已過期，重新登入...")
            os.remove(COOKIES_FILE)
            await login(page)
            cookies = await context.cookies()
            with open(COOKIES_FILE, "w") as f:
                json.dump(cookies, f)

        seen_urls: set[str] = set()
        seen_texts: set[str] = set()

        def collect(items):
            count = 0
            for item in items:
                key = item.get("url") or item["text"]
                if key not in seen_urls and item["text"] not in seen_texts:
                    seen_urls.add(key)
                    seen_texts.add(item["text"])
                    all_posts.append(item)
                    count += 1
            return count

        import urllib.parse
        print(f"\n🔍 開始爬取（{len(SEARCH_TERMS)} 個關鍵字，每個滾動 {SCROLL_PER_TERM} 次）...\n")

        for term in SEARCH_TERMS:
            url = f"https://www.threads.com/search?q={urllib.parse.quote(term)}&serp_type=default"
            try:
                await page.goto(url, timeout=15000)
                await page.wait_for_timeout(2000)
                no_new = 0
                for i in range(SCROLL_PER_TERM):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(1200)
                    new = collect(await page.evaluate(EXTRACT_JS))
                    if new == 0:
                        no_new += 1
                        if no_new >= 3:
                            break
                    else:
                        no_new = 0
                total_new = len([p for p in all_posts])
                print(f"  [{term}] 累計 {len(all_posts)} 篇")
            except Exception as e:
                print(f"  [{term}] 錯誤：{e}")

        await browser.close()

    print(f"\n📊 共抓到 {len(all_posts)} 篇，進行排序...")

    def has_chinese(text: str) -> bool:
        return any('\u4e00' <= c <= '\u9fff' for c in (text or ''))

    # 只保留有中文的文章，依按讚數排序（不限時間）
    filtered = [p for p in all_posts if has_chinese(p.get("text", ""))]
    filtered.sort(key=lambda x: x["likes"], reverse=True)
    top = filtered[:MAX_POSTS]

    result = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(top),
        "posts": top,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完成！共 {len(top)} 篇（依讚數排序）")
    for post in top[:5]:
        print(f"  ❤️ {post['likes']:>6}  @{post.get('username','?')}")
        print(f"  {post['text'][:60]}...")
        print()


_scraping = False

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        global _scraping
        if self.path == "/api/scrape":
            if _scraping:
                self._json(202, {"status": "running"})
                return
            def run():
                global _scraping
                _scraping = True
                try:
                    asyncio.run(scrape())
                finally:
                    _scraping = False
            threading.Thread(target=run, daemon=True).start()
            self._json(200, {"status": "started"})
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path == "/api/status":
            self._json(200, {"scraping": _scraping})
        elif self.path == "/" or self.path == "":
            self.path = "/index_lonely.html"
            super().do_GET()
        else:
            super().do_GET()

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def serve_and_open(port=8766):
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    http.server.HTTPServer.allow_reuse_address = True
    server = http.server.HTTPServer(("", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    webbrowser.open(f"http://localhost:{port}/index_lonely.html")
    print(f"🌐 已開啟瀏覽器 http://localhost:{port}/index_lonely.html（按 Ctrl+C 關閉）")
    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    asyncio.run(scrape())
    os.system("lsof -ti:8766 | xargs kill -9 2>/dev/null")
    import time; time.sleep(1)
    serve_and_open()
