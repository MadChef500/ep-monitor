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
SCOUTING_NEWS_URL = "https://www.thescoutingnews.com"
EP_PROFILE_URL = "https://www.eliteprospects.com/player/956156/michael-dipalma"
EP_SEARCH_URL = "https://www.eliteprospects.com/search/player?q=Michael+DiPalma"
GOOGLE_SEARCH_URL = "https://www.google.com/search?q=Michael+DiPalma+hockey+eliteprospects"
PLAYER_NAME = "Michael DiPalma"

# Traffic sources — Google removed (blocks automated browsers, causes crashes)
# MHR ~50%, EP internal ~20%, Direct ~25%, ScoutingNews ~5%
TRAFFIC_SOURCES = (
    ["MHR"] * 10 +          # ~50% — arrives as referral from MHR
    ["EP"] * 4 +             # ~20% — eliteprospects.com internal search
    ["ScoutingNews"] * 1 +   # ~5%  — thescoutingnews.com referral
    ["Direct"] * 5           # ~25% — goes straight to EP profile URL
)

# Browser locale/timezone profiles per country
COUNTRY_PROFILES = {
    "Canada":    {"locale": "en-CA", "timezone_id": "America/Toronto",   "lang": "en-CA,en;q=0.9"},
    "UK":        {"locale": "en-GB", "timezone_id": "Europe/London",     "lang": "en-GB,en;q=0.9"},
    "Russia":    {"locale": "ru-RU", "timezone_id": "Europe/Moscow",     "lang": "ru-RU,ru;q=0.9,en;q=0.8"},
    "Sweden":    {"locale": "sv-SE", "timezone_id": "Europe/Stockholm",  "lang": "sv-SE,sv;q=0.9,en;q=0.8"},
    "Finland":   {"locale": "fi-FI", "timezone_id": "Europe/Helsinki",   "lang": "fi-FI,fi;q=0.9,en;q=0.8"},
    "Czech":     {"locale": "cs-CZ", "timezone_id": "Europe/Prague",     "lang": "cs-CZ,cs;q=0.9,en;q=0.8"},
    "Turkey":    {"locale": "tr-TR", "timezone_id": "Europe/Istanbul",   "lang": "tr-TR,tr;q=0.9,en;q=0.8"},
}

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

    profile = COUNTRY_PROFILES.get(run_type, None)
    is_intl = profile is not None
    location_label = run_type if is_intl else "US"

    traffic_source = random.choice(TRAFFIC_SOURCES)
    source_labels = {
        "MHR":          "MHR roster → EP",
        "EP":           "EP search → EP",
        "Google":       "Google → EP",
        "ScoutingNews": "ScoutingNews → EP",
        "Direct":       "Direct → EP",
    }
    source_label = source_labels.get(traffic_source, "Direct → EP")

    data = {
        "traffic_source":        source_label,
        "search_phrase":         source_label,
        "search_engine":         "Direct",
        "search_location":       location_label,
        "profile_found":         False,
        "ep_url":                "",
        "analytics_opened":      False,
        "blocked":               False,
        "view_count":            "N/A",
        "season_stats":          [],   # list of {team, league, gp, g, a, pts}
        "session_duration":      0,
        "run_type":              "Non-US" if is_intl else "US",
        "result":                "Failed",
        "notes":                 f"Country: {run_type}. " if is_intl else "",
    }

    ua = random.choice(USER_AGENTS)
    locale     = profile["locale"]      if is_intl else "en-US"
    timezone   = profile["timezone_id"] if is_intl else "America/New_York"

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
            locale=locale,
            timezone_id=timezone,
            java_script_enabled=True,
        )

        # Make navigator.webdriver undetectable
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await context.new_page()

        try:
            if traffic_source == "ScoutingNews":
                # ── Path A: ScoutingNews → EP ────────────────────────────────
                print(f"[Runner] Loading ScoutingNews…")
                await page.goto(SCOUTING_NEWS_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(2_000, 4_000))
                await _slow_scroll(page, steps=random.randint(2, 4))
                await page.goto(EP_PROFILE_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(2_000, 3_500))
                ep_page = page
                data["profile_found"] = True
                data["ep_url"] = ep_page.url
                print(f"[Runner] Landed on EP via ScoutingNews: {ep_page.url}")

            elif traffic_source == "Direct":
                # ── Path B: Direct → EP ──────────────────────────────────────
                print(f"[Runner] Loading EP directly…")
                await page.goto(EP_PROFILE_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(2_000, 3_500))
                ep_page = page
                data["profile_found"] = True
                data["ep_url"] = ep_page.url
                print(f"[Runner] Landed on EP directly: {ep_page.url}")

            elif traffic_source == "EP":
                # ── Path C: EP internal search → profile ─────────────────────
                print(f"[Runner] Searching EP internally…")
                await page.goto(EP_SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(2_000, 3_500))
                await _slow_scroll(page, steps=2)
                # Click first result that matches Michael DiPalma
                link = await page.query_selector(f"a[href*='michael-dipalma']")
                if link:
                    await link.scroll_into_view_if_needed()
                    await page.wait_for_timeout(random.randint(500, 1_000))
                    await link.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=30_000)
                else:
                    await page.goto(EP_PROFILE_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(2_000, 3_500))
                ep_page = page
                data["profile_found"] = True
                data["ep_url"] = ep_page.url
                print(f"[Runner] Landed on EP via EP search: {ep_page.url}")

            elif traffic_source == "Google":
                # ── Path D: Google search → EP ───────────────────────────────
                print(f"[Runner] Searching Google…")
                await page.goto(GOOGLE_SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(2_000, 4_000))
                await _slow_scroll(page, steps=2)
                # Click EP result in search results
                link = await page.query_selector("a[href*='eliteprospects.com/player/956156']")
                if not link:
                    link = await page.query_selector("a[href*='michael-dipalma']")
                if link:
                    await link.scroll_into_view_if_needed()
                    await page.wait_for_timeout(random.randint(500, 1_200))
                    await link.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=30_000)
                else:
                    await page.goto(EP_PROFILE_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(2_000, 3_500))
                ep_page = page
                data["profile_found"] = True
                data["ep_url"] = ep_page.url
                print(f"[Runner] Landed on EP via Google: {ep_page.url}")

            else:  # MHR
                # ── Path E: MHR roster → EP ──────────────────────────────────
                print(f"[Runner] Loading MHR roster…")
                await page.goto(MHR_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(1_500, 3_000))
                await _slow_scroll(page, steps=3)

                # ── Step 2: Find Michael DiPalma and click his EP link ───────
                print(f"[Runner] Searching for {PLAYER_NAME}…")
                ep_link_el = await page.query_selector(
                    f"tr:has-text('{PLAYER_NAME}') a[href*='eliteprospects']"
                )
                if not ep_link_el:
                    ep_link_el = await page.query_selector(
                        f"tr:has-text('{PLAYER_NAME}') a:has-text('EP')"
                    )
                if not ep_link_el:
                    name_el = await page.query_selector(f"text={PLAYER_NAME}")
                    if name_el:
                        await name_el.scroll_into_view_if_needed()
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

                # ── Step 3: Click EP link ────────────────────────────────────
                try:
                    async with context.expect_page(timeout=10_000) as new_page_info:
                        await ep_link_el.click()
                    ep_page = await new_page_info.value
                    await ep_page.wait_for_load_state("domcontentloaded", timeout=30_000)
                except Exception:
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

            # ── Step 5: Scrape view count — try multiple strategies ─────────
            try:
                # Wait for network to settle so JS-rendered content appears
                try:
                    await ep_page.wait_for_load_state("networkidle", timeout=10_000)
                except PWTimeout:
                    pass

                page_text = await ep_page.inner_text("body")

                # Debug: log what's near key markers so we can see EP's current layout
                for marker in ["profile analytics", "views", "page views"]:
                    idx = page_text.lower().find(marker)
                    if idx >= 0:
                        snippet = page_text[max(0, idx-80):idx+50].replace("\n", "↵")
                        print(f"[Runner] Near '{marker}': ...{snippet}...")
                        break

                # Strategy 1: regex — number before "PROFILE ANALYTICS"
                match = re.search(
                    r'\b(\d[\d ,]{1,6}\d)\s*\n?\s*profile\s*analytics',
                    page_text, re.IGNORECASE
                )
                # Strategy 2: regex — number followed by "views" or "page views"
                if not match:
                    match = re.search(
                        r'\b(\d[\d ,]{1,6}\d)\s*\n?\s*(?:page\s*)?views?\b',
                        page_text, re.IGNORECASE
                    )
                # Strategy 3: try any element that might contain the count
                if not match:
                    for sel in [
                        ".ep-icon-eye + span",
                        "[class*='views'] [class*='count']",
                        "[class*='view-count']",
                        "[class*='profile-views']",
                        "span.total-views",
                        "[data-testid*='view']",
                        "[aria-label*='views']",
                    ]:
                        el = await ep_page.query_selector(sel)
                        if el:
                            txt = (await el.inner_text()).strip()
                            m = re.search(r'\d[\d ,]{2,6}\d', txt)
                            if m:
                                match = m
                                print(f"[Runner] View count from selector {sel}: {m.group(0)}")
                                break

                if match:
                    raw = match.group(1) if match.lastindex else match.group(0)
                    raw = raw.replace(" ", "").replace(",", "")
                    if raw.isdigit() and 1000 <= int(raw) <= 999999:
                        data["view_count"] = raw
                        print(f"[Runner] View count: {data['view_count']}")
                    else:
                        print(f"[Runner] View count out of range: {raw}")
                else:
                    print("[Runner] View count not found by any strategy.")
            except Exception as e:
                data["notes"] += f"View count error: {e}. "
                print(f"[Runner] View count exception: {e}")

            # ── Step 6: Scrape current season stats ─────────────────────────
            try:
                page_text = await ep_page.inner_text("body")
                current_year = datetime.now(ET).year
                # EP season format: "2025-26" or "2026-27"
                season_label = f"{current_year - 1}-{str(current_year)[2:]}" \
                    if datetime.now(ET).month >= 9 \
                    else f"{current_year - 1}-{str(current_year)[2:]}"

                stats = []
                # Find all rows for the current season using regex on page text
                # Each row looks like: "Team Name  League  GP  G  A  PTS ..."
                season_pattern = re.compile(
                    rf'{re.escape(season_label)}.*?(\n.*?)(?=\n\d{{4}}-|\Z)',
                    re.DOTALL | re.IGNORECASE
                )
                season_block = season_pattern.search(page_text)
                if season_block:
                    block = season_block.group(0)
                    # Extract individual stat lines: team, gp, g, a, pts
                    row_pattern = re.compile(
                        r'(.+?)\s{2,}(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)',
                    )
                    for m in row_pattern.finditer(block):
                        team = m.group(1).strip()
                        league = m.group(2).strip()
                        gp, g, a, pts = m.group(3), m.group(4), m.group(5), m.group(6)
                        if any(skip in team.lower() for skip in ["playoffs", "postseason"]):
                            continue
                        stats.append({
                            "team": team, "league": league,
                            "gp": gp, "g": g, "a": a, "pts": pts
                        })
                        print(f"[Runner] Stats: {team} {league} {gp}GP {g}G {a}A {pts}PTS")

                data["season_stats"] = stats
            except Exception as e:
                data["notes"] += f"Stats error: {e}. "

            # ── Step 7: Click Profile Analytics tab ─────────────────────────
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
