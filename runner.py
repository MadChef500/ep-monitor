"""
runner.py
One complete monitoring run: MHR roster → EP profile → log to Notion.
"""

import asyncio
import random
import re
import sys
import time
from datetime import datetime

import pytz
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from notion_logger import log_run

ET = pytz.timezone("America/New_York")

MHR_URL = "https://myhockeyrankings.com/team-info/3748/2025/roster"
PLAYER_NAME = "Michael DiPalma"

# Realistic desktop user-agents (rotated per run)
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

PAYWALL_KEYWORDS = [
    "subscribe", "premium", "upgrade", "unlock", "paywall",
    "sign up to view", "create a free account", "log in to view",
]


async def _slow_scroll(page, steps: int = 5) -> None:
    """Scroll the page slowly to simulate a real reader."""
    for _ in range(steps):
        await page.mouse.wheel(0, random.randint(200, 400))
        await page.wait_for_timeout(random.randint(300, 700))


async def run_check(run_type: str = "US") -> dict:
    """
    Execute one full monitoring run.

    run_type: "US" | "Non-US"
    Returns a dict with all logged fields.
    """
    now = datetime.now(ET)
    start_ts = time.time()

    data = {
        "traffic_source":        "MHR roster → EP",
        "search_phrase":         "MHR roster → EP",
        "search_engine":         "Direct",
        "search_location":       "Non-US" if run_type == "Non-US" else "US",
        "profile_found":         False,
        "ep_url":                "",
        "analytics_opened":      False,
        "blocked":               False,
        "view_count":            "N/A",
        "session_duration":      0,
        "run_type":              run_type,
        "result":                "Failed",
        "notes":                 "",
    }

    ua = random.choice(USER_AGENTS)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context = await browser.new_context(
            user_agent=ua,
            viewport={"width": random.choice([1280, 1366, 1440, 1920]),
                      "height": random.choice([768, 800, 900, 1080])},
            locale="en-US",
            timezone_id="America/New_York",
            java_script_enabled=True,
        )

        # Make navigator.webdriver undetectable
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await context.new_page()

        try:
            # ── Step 1: Load MHR roster ──────────────────────────────────────
            print(f"[Runner] Loading MHR roster…")
            await page.goto(MHR_URL, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(random.randint(1_500, 3_000))
            await _slow_scroll(page, steps=3)

            # ── Step 2: Find Michael DiPalma and click his EP link ───────────
            print(f"[Runner] Searching for {PLAYER_NAME}…")

            # Try row-level selector first
            ep_link_el = await page.query_selector(
                f"tr:has-text('{PLAYER_NAME}') a[href*='eliteprospects']"
            )
            if not ep_link_el:
                ep_link_el = await page.query_selector(
                    f"tr:has-text('{PLAYER_NAME}') a:has-text('EP')"
                )
            # Fallback: any EP link near the name on the page
            if not ep_link_el:
                name_el = await page.query_selector(f"text={PLAYER_NAME}")
                if name_el:
                    await name_el.scroll_into_view_if_needed()
                    # Walk up to find a sibling/parent EP link
                    ep_link_el = await page.evaluate_handle(
                        """el => {
                            const row = el.closest('tr') || el.parentElement;
                            return row ? row.querySelector('a[href*="eliteprospects"], a') : null;
                        }""",
                        name_el,
                    )
                    if ep_link_el:
                        href = await ep_link_el.get_attribute("href")
                        if not href or "eliteprospects" not in href:
                            ep_link_el = None

            if not ep_link_el:
                data["notes"] += "EP link not found on roster page. "
                data["result"] = "EP Link Not Found"
                return data

            await ep_link_el.scroll_into_view_if_needed()
            await page.wait_for_timeout(random.randint(500, 1_200))

            ep_href = await ep_link_el.get_attribute("href")
            data["ep_url"] = ep_href or ""
            data["profile_found"] = True
            print(f"[Runner] Found EP link: {ep_href}")

            # ── Step 3: Click EP link (preserves MHR referrer) ──────────────
            # EP links open in a new tab — catch that
            try:
                async with context.expect_page(timeout=10_000) as new_page_info:
                    await ep_link_el.click()
                ep_page = await new_page_info.value
                await ep_page.wait_for_load_state("domcontentloaded", timeout=30_000)
            except Exception:
                # Fallback: link navigated in same tab
                ep_page = page
                try:
                    async with page.expect_navigation(
                        wait_until="domcontentloaded", timeout=30_000
                    ):
                        pass
                except PWTimeout:
                    pass

            await ep_page.wait_for_timeout(random.randint(2_000, 4_000))
            data["ep_url"] = ep_page.url
            print(f"[Runner] Landed on EP: {ep_page.url}")

            # ── Step 4: Dwell 20–60 s ────────────────────────────────────────
            dwell_s = random.randint(20, 60)
            print(f"[Runner] Dwelling {dwell_s}s…")
            half = dwell_s // 2
            await ep_page.wait_for_timeout(half * 1_000)
            await _slow_scroll(ep_page, steps=random.randint(2, 5))
            await ep_page.wait_for_timeout((dwell_s - half) * 1_000)

            # ── Step 5: Scrape view count from main profile page ─────────────
            # The count (e.g. "4 618") is displayed next to the eye icon
            # on the main profile page, before clicking anything.
            try:
                # EP renders the count as text next to an eye/view icon
                # Try the element that wraps the eye icon + number
                count_el = await ep_page.query_selector(
                    ".ep-icon-eye + span, "
                    "[class*='views'] [class*='count'], "
                    "[class*='view-count'], "
                    "[class*='profile-views'], "
                    "span.total-views"
                )
                if count_el:
                    raw = await count_el.inner_text()
                    data["view_count"] = raw.strip().replace(",", "").replace(" ", "")
                    print(f"[Runner] View count (element): {data['view_count']}")
                else:
                    # Fallback: find the number sitting between the eye icon
                    # and the PROFILE ANALYTICS button via page text
                    page_text = await ep_page.inner_text("body")
                    # EP view count is a 4-5 digit number just before
                    # "PROFILE ANALYTICS". Space is used as thousands separator.
                    # e.g. "4 651\nPROFILE ANALYTICS"
                    match = re.search(
                        r'\b(\d[\d ]{1,5}\d)\s*\n?\s*profile\s*analytics',
                        page_text, re.IGNORECASE
                    )
                    if match:
                        raw = match.group(1).replace(" ", "").replace(",", "")
                        # Sanity check: real view counts are 4-6 digits
                        if 1000 <= int(raw) <= 999999:
                            data["view_count"] = raw
                            print(f"[Runner] View count (regex): {data['view_count']}")
                    else:
                        print("[Runner] View count not found.")
            except Exception as e:
                data["notes"] += f"View count error: {e}. "

            # ── Step 6: Click Profile Analytics tab ─────────────────────────
            try:
                analytics_tab = await ep_page.query_selector(
                    "a:has-text('Profile Analytics'), "
                    "button:has-text('Profile Analytics'), "
                    "[href*='analytics']"
                )
                if analytics_tab:
                    await analytics_tab.scroll_into_view_if_needed()
                    await ep_page.wait_for_timeout(random.randint(600, 1_200))
                    await analytics_tab.click()
                    await ep_page.wait_for_timeout(4_000)
                    data["analytics_opened"] = True
                    print("[Runner] Analytics tab clicked.")

                    # ── Step 7: Detect paywall ───────────────────────────────
                    body = await ep_page.inner_text("body")
                    if any(kw in body.lower() for kw in PAYWALL_KEYWORDS):
                        data["blocked"] = True
                        data["notes"] += "Paywall detected. "
                        print("[Runner] Paywall detected.")
                    else:
                        data["notes"] += "Analytics loaded (no paywall). "
                else:
                    data["notes"] += "Profile Analytics tab not found. "
                    print("[Runner] Analytics tab not found.")
            except Exception as e:
                data["notes"] += f"Analytics error: {e}. "

            data["result"] = "Success"

        except Exception as e:
            data["notes"] += f"Run error: {e}. "
            data["result"] = "Error"
            print(f"[Runner] Error: {e}")

        finally:
            data["session_duration"] = round(time.time() - start_ts)
            await browser.close()

    # ── Log to Notion ────────────────────────────────────────────────────────
    try:
        log_run(data)
        print(f"[Runner] Logged to Notion. Result={data['result']}")
    except Exception as e:
        print(f"[Runner] Notion log failed: {e}")
        data["notes"] += f"Notion log failed: {e}. "

    return data


if __name__ == "__main__":
    run_type = sys.argv[1] if len(sys.argv) > 1 else "US"
    result = asyncio.run(run_check(run_type))
    print("\n── Run Result ──")
    for k, v in result.items():
        print(f"  {k}: {v}")
