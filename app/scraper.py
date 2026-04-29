import asyncio
import urllib.parse
from datetime import datetime, timezone
from typing import Callable, Awaitable

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

SCROLL_TIMES = 50
MAX_POSTS = 5000

SEARCH_TERMS = [
    "交友", "單身", "戀愛", "交往", "脫單", "曖昧", "告白",
    "喜歡你", "在一起", "分手", "失戀", "暗戀", "曖昧期",
    "聊天", "找人聊", "找人聊天", "陪我聊", "沒人聊",
    "交友軟體", "配對", "Tinder", "tinder", "CMB", "Bumble", "bumble",
    "Soga", "soga", "Grass", "grass", "Omi", "omi",
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


def is_within_hours(dt_str: str | None, hours: int) -> bool:
    if not dt_str:
        return False
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() <= hours * 3600
    except Exception:
        return False


def has_chinese(text: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in (text or ""))


def time_bucket(dt_str):
    if not dt_str:
        return "24小時內"
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        if hours <= 1:
            return "1小時內"
        if hours <= 3:
            return "3小時內"
        if hours <= 6:
            return "6小時內"
        if hours <= 12:
            return "12小時內"
        return "24小時內"
    except Exception:
        return "24小時內"


# Callback type: async function that receives a progress message string
ProgressCallback = Callable[[str], Awaitable[None]]


async def scrape(
    username: str,
    password: str,
    mode: str = "keyword",
    on_progress: ProgressCallback | None = None,
    totp: str = "",
) -> dict:
    """
    Scrape Threads posts. Returns result dict with posts.
    on_progress is called with status messages for SSE streaming.
    """

    async def progress(msg: str):
        if on_progress:
            await on_progress(msg)

    all_posts: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-TW",
            timezone_id="Asia/Taipei",
        )
        stealth = Stealth()
        await stealth.apply_stealth_async(context)
        page = await context.new_page()

        # Login
        await progress("登入中...")
        await page.goto("https://www.threads.com/login", timeout=20000)
        await page.wait_for_timeout(3000)

        # Take screenshot for debugging
        await progress(f"登入頁面載入完成，目前 URL: {page.url}")

        await page.fill(
            'input[autocomplete="username"], input[name="username"], input[type="text"]',
            username,
        )
        await page.wait_for_timeout(800)
        await page.fill(
            'input[autocomplete="current-password"], input[name="password"], input[type="password"]',
            password,
        )
        await page.wait_for_timeout(800)
        await page.keyboard.press("Enter")
        await progress("已送出登入，等待回應...")

        # Wait a bit for page to respond (2FA prompt or redirect)
        await page.wait_for_timeout(5000)

        # Check if 2FA is required
        body_text = await page.evaluate("() => document.body?.innerText || ''")
        is_2fa = "雙重驗證" in body_text or "驗證碼" in body_text or "two-factor" in body_text.lower() or "verification" in body_text.lower()

        if is_2fa:
            if not totp:
                await browser.close()
                raise RuntimeError("需要雙重驗證碼（2FA），請在表單中填入驗證應用程式的 6 位數碼後重試")

            await progress("偵測到雙重驗證頁面，嘗試輸入驗證碼...")

            # Dump all inputs for debugging
            input_info = await page.evaluate("""
                () => Array.from(document.querySelectorAll('input')).map(el => ({
                    type: el.type, name: el.name, id: el.id, placeholder: el.placeholder,
                    ariaLabel: el.getAttribute('aria-label'), visible: el.offsetParent !== null
                }))
            """)
            await progress(f"頁面上的 input 欄位: {input_info}")

            # Try multiple strategies to find the 2FA input
            two_fa_input = None
            selectors = [
                'input[aria-label*="驗證"]',
                'input[aria-label*="碼"]',
                'input[aria-label*="code"]',
                'input[name*="verificationCode"]',
                'input[name*="approvals_code"]',
                'input[type="tel"]',
                'input[type="number"]',
            ]
            for sel in selectors:
                two_fa_input = await page.query_selector(sel)
                if two_fa_input:
                    await progress(f"找到 2FA input: {sel}")
                    break

            if not two_fa_input:
                # Last resort: find any visible text input that's NOT username/password
                two_fa_input = await page.evaluate_handle("""
                    () => {
                        const inputs = Array.from(document.querySelectorAll('input'));
                        return inputs.find(el =>
                            el.offsetParent !== null &&
                            el.type !== 'hidden' &&
                            el.type !== 'password' &&
                            el.autocomplete !== 'username'
                        ) || null;
                    }
                """)
                if two_fa_input:
                    await progress("使用備用方式找到 input")

            if two_fa_input:
                await two_fa_input.fill(totp)
                await page.wait_for_timeout(500)

                # Try clicking submit button
                submit_btn = await page.query_selector('button:has-text("提交")')
                if not submit_btn:
                    submit_btn = await page.query_selector('button:has-text("Submit")')
                if not submit_btn:
                    submit_btn = await page.query_selector('button[type="submit"]')

                if submit_btn:
                    await progress("點擊提交按鈕...")
                    await submit_btn.click()
                else:
                    await progress("找不到提交按鈕，嘗試按 Enter...")
                    await page.keyboard.press("Enter")

                await progress("已提交驗證碼，等待中...")
                await page.wait_for_timeout(5000)
            else:
                await progress("警告：找不到 2FA 輸入欄位，嘗試繼續...")

        # Wait up to 30s for URL to change away from login
        login_success = False
        for i in range(15):
            await page.wait_for_timeout(2000)
            current_url = page.url
            await progress(f"等待中...（{(i+1)*2}秒）URL: {current_url}")

            # Check for error messages on page
            error_text = await page.evaluate("""
                () => {
                    const el = document.querySelector('[data-testid="login-error-message"]')
                        || document.querySelector('[class*="error"]')
                        || document.querySelector('[role="alert"]');
                    return el ? el.innerText : null;
                }
            """)
            if error_text:
                await browser.close()
                raise RuntimeError(f"登入失敗：{error_text}")

            if "login" not in current_url:
                login_success = True
                break

        if not login_success:
            # One more check: maybe it's a challenge page
            body_text = await page.evaluate("() => document.body?.innerText?.substring(0, 500) || ''")
            await browser.close()
            raise RuntimeError(
                f"登入逾時（30秒），可能被 Threads 擋住或需要驗證。\n"
                f"目前 URL: {page.url}\n"
                f"頁面內容: {body_text[:200]}"
            )

        await progress("登入成功！")

        # Wait for posts
        await progress("載入首頁文章中...")
        try:
            await page.wait_for_selector(
                "div[data-pressable-container]", timeout=15000
            )
        except Exception:
            pass
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

        # 1. Feed scrolling
        no_new_count = 0
        for i in range(SCROLL_TIMES):
            try:
                await page.evaluate(
                    "document.body && window.scrollTo(0, document.body.scrollHeight)"
                )
                await page.wait_for_timeout(1500)
                new = collect(await page.evaluate(EXTRACT_JS))
            except Exception:
                continue
            await progress(
                f"滾動第 {i+1}/{SCROLL_TIMES} 次，新增 {new} 篇（累計 {len(all_posts)} 篇）"
            )
            if new == 0:
                no_new_count += 1
                if no_new_count >= 6:
                    await progress("連續無新文章，提早結束滾動")
                    break
            else:
                no_new_count = 0

        # 2. Keyword search (keyword mode only)
        if mode == "keyword":
            await progress(f"開始關鍵字搜尋（{len(SEARCH_TERMS)} 個詞）...")
            for idx, term in enumerate(SEARCH_TERMS):
                url = f"https://www.threads.com/search?q={urllib.parse.quote(term)}&serp_type=recent"
                try:
                    await page.goto(url, timeout=15000)
                    await page.wait_for_timeout(2000)
                    for _ in range(10):
                        await page.evaluate(
                            "document.body && window.scrollTo(0, document.body.scrollHeight)"
                        )
                        await page.wait_for_timeout(1000)
                    new = collect(await page.evaluate(EXTRACT_JS))
                    await progress(
                        f"[{idx+1}/{len(SEARCH_TERMS)}] 「{term}」新增 {new} 篇（累計 {len(all_posts)} 篇）"
                    )
                except Exception as e:
                    await progress(f"[{term}] 搜尋錯誤：{e}")

        await browser.close()

    # Process results
    await progress(f"處理結果中...（共 {len(all_posts)} 篇原始資料）")

    # Deduplicate
    seen_urls_set: set[str] = set()
    seen_texts: set[str] = set()
    unique: list[dict] = []
    for post in all_posts:
        key = post.get("url") or post["text"]
        if key not in seen_urls_set and post["text"] not in seen_texts:
            seen_urls_set.add(key)
            seen_texts.add(post["text"])
            unique.append(post)

    # Filter: 24h, Chinese only, sort by likes
    recent = [
        post
        for post in unique
        if is_within_hours(post.get("datetime"), 24) and has_chinese(post.get("text", ""))
    ]
    recent.sort(key=lambda x: x["likes"], reverse=True)
    top = recent[:MAX_POSTS]

    for post in top:
        post["time_bucket"] = time_bucket(post.get("datetime"))

    result = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(top),
        "posts": top,
    }

    await progress(f"完成！共 {len(top)} 篇文章")
    return result
