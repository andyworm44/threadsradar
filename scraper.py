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

OUTPUT_FILE  = os.path.join(os.path.dirname(__file__), "data.json")
COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies.json")
HOURS_LIMIT  = 12
MAX_POSTS    = 5000
SCROLL_TIMES = 200

# 關鍵字搜尋（交友 / 戀愛 / 單身相關）
SEARCH_TERMS = [
    # 交友 / 戀愛 / 單身
    "交友", "單身", "戀愛", "交往", "脫單", "曖昧", "告白",
    "喜歡你", "在一起", "分手", "失戀", "暗戀", "曖昧期",
    # 聊天 / 找人
    "聊天", "找人聊", "找人聊天", "陪我聊", "沒人聊",
    # 交友軟體
    "交友軟體", "配對", "Tinder", "tinder", "CMB", "Bumble", "bumble",
    "Soga", "soga", "Grass", "grass", "Omi", "omi",
    # 品牌 / 社群
    "柴犬", "Shosho", "shosho",
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


def is_within_hours(dt_str: str | None, hours: int = HOURS_LIMIT) -> bool:
    if not dt_str:
        return False
    try:
        dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        return (datetime.now(timezone.utc) - dt).total_seconds() <= hours * 3600
    except Exception:
        return False


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


async def scrape(mode="keyword"):
    all_posts: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-TW",
            timezone_id="Asia/Taipei",
        )
        page = await context.new_page()

        # 若有存 cookies 就直接用，否則讓使用者手動登入
        if os.path.exists(COOKIES_FILE):
            with open(COOKIES_FILE) as f:
                cookies = json.load(f)
            await context.add_cookies(cookies)
            print("🍪 使用已儲存的 cookies")

        print("📡 開啟 Threads 首頁...")
        await page.goto("https://www.threads.com/", timeout=20000)
        await page.wait_for_load_state("domcontentloaded")

        # 若尚未登入，等待使用者手動登入
        if "login" in page.url or not os.path.exists(COOKIES_FILE):
            if os.path.exists(COOKIES_FILE):
                print("⚠️  Cookie 已過期，請在瀏覽器手動登入...")
                os.remove(COOKIES_FILE)
            else:
                print("🔐 請在瀏覽器中手動登入 Threads，登入完成後程式會自動繼續...")
            # 等待跳轉離開登入頁
            await page.wait_for_url(lambda url: "login" not in url, timeout=300000)
            print("✅ 登入成功，儲存 cookies...")
            cookies = await context.cookies()
            with open(COOKIES_FILE, "w") as f:
                json.dump(cookies, f)
            print("🍪 Cookies 已儲存")
            await page.wait_for_timeout(2000)

        # 等文章出現
        try:
            await page.wait_for_selector("div[data-pressable-container]", timeout=15000)
            print("  ✅ 文章已載入")
        except Exception:
            print("  ⚠️  等待文章逾時，繼續嘗試...")
        await page.wait_for_timeout(2000)

        seen_urls: set[str] = set()

        def collect(items):
            count = 0
            for item in items:
                key = item.get("url") or item["text"]
                if key not in seen_urls:
                    seen_urls.add(key)
                    all_posts.append(item)
                    count += 1
            return count

        # 1. 首頁 feed
        print(f"📜 首頁 feed 滾動（{SCROLL_TIMES} 次）...")
        no_new_count = 0
        for i in range(SCROLL_TIMES):
            try:
                await page.evaluate("document.body && window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1500)
                new = collect(await page.evaluate(EXTRACT_JS))
            except Exception as e:
                print(f"  第 {i+1}/{SCROLL_TIMES} 次，跳過（{e}）")
                continue
            print(f"  第 {i+1}/{SCROLL_TIMES} 次，新增 {new} 篇（累計 {len(all_posts)} 篇）")
            if new == 0:
                no_new_count += 1
                if no_new_count >= 6:
                    print("  連續無新文章，提早結束")
                    break
            else:
                no_new_count = 0

        # 2. 關鍵字搜尋補充（僅 keyword 模式）
        if mode == "keyword":
            import urllib.parse
            print(f"\n🔍 關鍵字搜尋補充（{len(SEARCH_TERMS)} 個詞）...")
            for term in SEARCH_TERMS:
                url = f"https://www.threads.com/search?q={urllib.parse.quote(term)}&serp_type=recent"
                try:
                    await page.goto(url, timeout=15000)
                    await page.wait_for_timeout(2000)
                    for _ in range(10):
                        await page.evaluate("document.body && window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(1000)
                    new = collect(await page.evaluate(EXTRACT_JS))
                    print(f"  [{term}] 新增 {new} 篇（累計 {len(all_posts)} 篇）")
                except Exception as e:
                    print(f"  [{term}] 錯誤：{e}")
        else:
            print("\n⏭️  僅首頁模式，跳過關鍵字搜尋")

        await browser.close()

    print(f"\n📊 本次抓到 {len(all_posts)} 篇，合併舊資料並去重...")

    # 讀取現有資料，合併進來
    existing_posts = []
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
                existing_posts = existing_data.get("posts", [])
            print(f"  📂 讀取到舊資料 {len(existing_posts)} 篇")
        except Exception:
            pass

    # 新文章優先（可能有更新的互動數據），舊文章補充
    combined = all_posts + existing_posts

    # 去重
    seen_urls, seen_texts = set(), set()
    unique: list[dict] = []
    for post in combined:
        key = post.get("url") or post["text"]
        if key not in seen_urls and post["text"] not in seen_texts:
            seen_urls.add(key)
            seen_texts.add(post["text"])
            unique.append(post)

    def has_chinese(text: str) -> bool:
        return any('\u4e00' <= c <= '\u9fff' for c in (text or ''))

    # 只保留 24 小時內、有中文的文章，依按讚數排序
    recent = [post for post in unique if is_within_hours(post.get("datetime"), 24) and has_chinese(post.get("text", ""))]
    recent.sort(key=lambda x: x["likes"], reverse=True)
    top = recent[:MAX_POSTS]

    # 加上時間區間標籤
    def time_bucket(dt_str):
        if not dt_str:
            return "24小時內"
        try:
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            if hours <= 1:   return "1小時內"
            if hours <= 3:   return "3小時內"
            if hours <= 6:   return "6小時內"
            if hours <= 12:  return "12小時內"
            return "24小時內"
        except Exception:
            return "24小時內"

    for post in top:
        post["time_bucket"] = time_bucket(post.get("datetime"))

    result = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(top),
        "posts": top,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完成！24小時內文章 {len(top)} 篇（去重後共 {len(unique)} 篇）")
    for post in top[:5]:
        print(f"  [{post['time_bucket']}] 讚:{post['likes']:>5}  @{post.get('username','?')}")
        print(f"  {post['text'][:60]}...")
        print()

    # 自動 push 到 GitHub Pages
    auto_push()


def auto_push():
    """爬完後自動 commit & push data.json 到 GitHub"""
    import subprocess
    script_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        subprocess.run(["git", "add", "data.json"], cwd=script_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"update data {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
            cwd=script_dir, check=True, capture_output=True
        )
        subprocess.run(["git", "push"], cwd=script_dir, check=True, capture_output=True)
        print("📤 已自動推送到 GitHub Pages")
    except subprocess.CalledProcessError:
        print("⚠️  自動推送失敗（可能沒有新變更）")
    except FileNotFoundError:
        print("⚠️  找不到 git，跳過自動推送")


_scraping = False

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        global _scraping
        if self.path == "/api/scrape":
            if _scraping:
                self._json(202, {"status": "running"})
                return
            # 解析 mode 參數
            mode = "keyword"
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > 0:
                    body = json.loads(self.rfile.read(length))
                    mode = body.get("mode", "keyword")
            except Exception:
                pass
            def run(m):
                global _scraping
                _scraping = True
                try:
                    asyncio.run(scrape(mode=m))
                finally:
                    _scraping = False
            threading.Thread(target=lambda: run(mode), daemon=True).start()
            self._json(200, {"status": "started", "mode": mode})
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path == "/api/status":
            self._json(200, {"scraping": _scraping})
        else:
            super().do_GET()

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def serve_and_open(port=8765):
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    http.server.HTTPServer.allow_reuse_address = True
    server = http.server.HTTPServer(("", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    webbrowser.open(f"http://localhost:{port}/index.html")
    print(f"🌐 已開啟瀏覽器 http://localhost:{port}/index.html（按 Ctrl+C 關閉）")
    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    os.system("/usr/sbin/lsof -ti:8765 | xargs kill -9 2>/dev/null")
    import time; time.sleep(1)
    serve_and_open()
