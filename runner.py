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

MHR_URL          = "https://myhockeyrankings.com/team-info/3748/2025/roster"
SCOUTING_NEWS_URL = "https://www.thescoutingnews.com"
HOCKEYDB_URL      = "https://www.hockeydb.com"
TWITTER_URL       = "https://x.com/search?q=Michael+DiPalma+hockey&src=typed_query"
INSTAGRAM_URL     = "https://www.instagram.com/explore/tags/hockeyprospects/"
EP_PROFILE_URL    = "https://www.eliteprospects.com/player/956156/michael-dipalma"
EP_SEARCH_URL     = "https://www.eliteprospects.com/search/player?q=Michael+DiPalma"
BING_SEARCH_URL   = "https://www.bing.com/search?q=Michael+DiPalma+hockey+eliteprospects"
DDG_SEARCH_URL    = "https://duckduckgo.com/?q=Michael+DiPalma+hockey+eliteprospects"
PLAYER_NAME       = "Michael DiPalma"

# Traffic mix — weighted toward EP app + MHR, natural spread across all sources
# EPApp ~20%, MHR ~20%, Direct ~15%, EP search ~10%, Bing ~10%, DDG ~8%,
# Twitter ~7%, Instagram ~5%, ScoutingNews ~3%, HockeyDB ~2%
TRAFFIC_SOURCES = (
    ["EPApp"]       * 4 +   # ~20% — EP mobile app (iOS/Android deep-link)
    ["MHR"]         * 4 +   # ~20% — myhockeyrankings.com roster → EP
    ["Direct"]      * 3 +   # ~15% — direct URL visit
    ["EP"]          * 2 +   # ~10% — eliteprospects.com internal search
    ["Bing"]        * 2 +   # ~10% — Bing search → EP
    ["DDG"]         * 2 +   # ~8%  — DuckDuckGo search → EP
    ["Twitter"]     * 1 +   # ~7%  — X/Twitter → EP
    ["Instagram"]   * 1 +   # ~5%  — Instagram → EP
    ["ScoutingNews"]* 1 +   # ~3%  — thescoutingnews.com → EP
    ["HockeyDB"]    * 1     # ~2%  — hockeydb.com → EP
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

# Desktop user-agents
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# Mobile user-agents (iOS Safari + Android Chrome)
MOBILE_USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
]

# EP app user-agents — WKWebView on iOS / WebView on Android
EP_APP_USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/21C66",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/20G75",
    "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/21C66",
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36",
]

# Mobile viewport sizes
MOBILE_VIEWPORTS = [
    {"width": 390, "height": 844},   # iPhone 14
    {"width": 375, "height": 812},   # iPhone 12 mini
    {"width": 414, "height": 896},   # iPhone 11
    {"width": 360, "height": 800},   # Android common
    {"width": 412, "height": 915},   # Pixel 6
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


async def _scrape_view_count(page, label: str = "profile") -> tuple[str | None, str]:
    """
    Try every known strategy to extract the profile view count.
    Returns (count_str_or_None, debug_snippet_for_notion).
    """
    try:
        try:
            await page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        await page.wait_for_timeout(2_000)
    except Exception:
        pass

    # Strategy 0: Next.js / app state extraction
    try:
        count_next = await page.evaluate("""
            () => {
                try {
                    const nd = window.__NEXT_DATA__;
                    if (nd) {
                        const str = JSON.stringify(nd);
                        const keys = [
                            '"views":', '"viewCount":', '"profileViews":',
                            '"analyticsCount":', '"pageViews":', '"totalViews":',
                            '"visitCount":', '"visits":',
                        ];
                        for (const key of keys) {
                            const idx = str.indexOf(key);
                            if (idx >= 0) {
                                const m = str.slice(idx + key.length).match(/^(\\d{4,6})/);
                                if (m) {
                                    const n = parseInt(m[1]);
                                    if (n >= 1000 && n <= 500000) return String(n);
                                }
                            }
                        }
                    }
                    for (const script of document.querySelectorAll('script[type="application/json"]')) {
                        try {
                            const d = JSON.parse(script.textContent);
                            const s = JSON.stringify(d);
                            const m = s.match(/"(?:views?|viewCount|profileViews|analyticsCount)":(\\d{4,6})/i);
                            if (m) {
                                const n = parseInt(m[1]);
                                if (n >= 1000 && n <= 500000) return String(n);
                            }
                        } catch(e) {}
                    }
                } catch(e) {}
                return null;
            }
        """)
        if count_next and str(count_next).isdigit():
            n = int(count_next)
            if 1_000 <= n <= 500_000:
                print(f"[Runner] [{label}] View count via Next.js state: {n}")
                return str(n), ""
    except Exception as e:
        print(f"[Runner] [{label}] Next.js query error: {e}")

    # Strategy A: JS DOM tree walker
    try:
        count_js = await page.evaluate("""
            () => {
                function clean(s) { return s.replace(/[,. ]/g, ''); }
                function inRange(n) { return n >= 1000 && n <= 500000; }

                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null
                );
                const nodes = [];
                let node;
                while ((node = walker.nextNode())) nodes.push(node);

                for (let i = 0; i < nodes.length; i++) {
                    if (!/profile analytics|profile views|total views/i.test(nodes[i].textContent.trim())) continue;
                    for (let d = -8; d <= 8; d++) {
                        if (d === 0) continue;
                        const idx = i + d;
                        if (idx < 0 || idx >= nodes.length) continue;
                        const candidate = clean(nodes[idx].textContent.trim());
                        if (/^\\d{4,6}$/.test(candidate) && inRange(parseInt(candidate))) {
                            return candidate;
                        }
                    }
                    let el = nodes[i].parentElement;
                    for (let up = 0; up < 6 && el; up++, el = el.parentElement) {
                        const ms = (el.textContent || '').match(/(\\d[\\d,]{3,6})/g) || [];
                        for (const c of ms) {
                            const n = parseInt(clean(c));
                            if (inRange(n)) return String(n);
                        }
                    }
                }
                for (const link of document.querySelectorAll('[href*="analytics"]')) {
                    let container = link.parentElement;
                    for (let up = 0; up < 4 && container; up++, container = container.parentElement) {
                        const ms = (container.textContent || '').match(/(\\d[\\d,]{3,6})/g) || [];
                        for (const c of ms) {
                            const n = parseInt(clean(c));
                            if (inRange(n)) return String(n);
                        }
                    }
                }
                return null;
            }
        """)
        if count_js:
            raw = str(count_js).replace(",", "").replace(" ", "")
            if raw.isdigit() and 1_000 <= int(raw) <= 500_000:
                print(f"[Runner] [{label}] View count via DOM walker: {raw}")
                return raw, ""
    except Exception as e:
        print(f"[Runner] [{label}] DOM walker error: {e}")

    # Strategy B: raw HTML regex
    try:
        html = await page.content()
        for m in re.finditer(
            r'(\d[\d,]{3,6})[^<]{0,150}analytics|analytics[^<]{0,150}(\d[\d,]{3,6})|views?["\s:]+(\d[\d,]{3,6})',
            html, re.IGNORECASE,
        ):
            raw = (m.group(1) or m.group(2) or m.group(3) or "").replace(",", "")
            if raw.isdigit() and 1_000 <= int(raw) <= 500_000:
                print(f"[Runner] [{label}] View count via raw HTML: {raw}")
                return raw, ""
    except Exception as e:
        print(f"[Runner] [{label}] HTML regex error: {e}")

    # Strategy C: inner_text regex
    try:
        page_text = await page.inner_text("body")
        for pattern in [
            r'(\d[\d ,]{2,7}\d)\s+profile\s+analytics',
            r'profile\s+analytics\s+(\d[\d ,]{2,7}\d)',
            r'(\d[\d ,]{2,7}\d)\s+(?:profile\s+)?views',
            r'views?[^\d]{0,30}(\d[\d ,]{2,7}\d)',
        ]:
            for m in re.finditer(pattern, page_text, re.IGNORECASE):
                raw = m.group(1).replace(" ", "").replace(",", "")
                if raw.isdigit() and 1_000 <= int(raw) <= 500_000:
                    print(f"[Runner] [{label}] View count via text regex: {raw}")
                    return raw, ""
    except Exception as e:
        print(f"[Runner] [{label}] Text regex error: {e}")

    # All strategies failed — build a debug snippet for Notion
    debug = f"[{label}] no count found. URL={page.url}. "
    try:
        page_text = await page.inner_text("body")
        idx = page_text.lower().find("profile analytics")
        if idx < 0:
            idx = page_text.lower().find("analytics")
        if idx < 0:
            idx = page_text.lower().find("views")
        if idx >= 0:
            snippet = page_text[max(0, idx - 80):idx + 80].replace("\n", " ⏎ ").strip()
            debug += f"Near marker: '{snippet}'. "
        else:
            debug += f"No 'analytics'/'views' marker on page. First 200 chars: '{page_text[:200].replace(chr(10), ' ⏎ ')}'."
    except Exception as e:
        debug += f"Could not read page text: {e}. "
    print(f"[Runner] [{label}] DEBUG: {debug}")
    return None, debug


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
        "EPApp":        "EP app → EP",
        "MHR":          "MHR roster → EP",
        "Direct":       "Direct → EP",
        "EP":           "EP search → EP",
        "Bing":         "Bing → EP",
        "DDG":          "DuckDuckGo → EP",
        "Twitter":      "Twitter/X → EP",
        "Instagram":    "Instagram → EP",
        "ScoutingNews": "ScoutingNews → EP",
        "HockeyDB":     "HockeyDB → EP",
        "Google":       "Google → EP",
    }
    source_label = source_labels.get(traffic_source, "Direct → EP")

    # EP app and social sources use mobile UA + viewport
    is_mobile = traffic_source in ("EPApp", "Twitter", "Instagram")
    is_ep_app = traffic_source == "EPApp"

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

    if is_ep_app:
        ua = random.choice(EP_APP_USER_AGENTS)
    elif is_mobile:
        ua = random.choice(MOBILE_USER_AGENTS)
    else:
        ua = random.choice(USER_AGENTS)

    if is_mobile:
        viewport = random.choice(MOBILE_VIEWPORTS)
    else:
        viewport = {
            "width":  random.choice([1280, 1366, 1440, 1920]),
            "height": random.choice([768, 800, 900, 1080]),
        }

    locale   = profile["locale"]      if is_intl else "en-US"
    timezone = profile["timezone_id"] if is_intl else "America/New_York"

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
            viewport=viewport,
            locale=locale,
            timezone_id=timezone,
            java_script_enabled=True,
            is_mobile=is_mobile,
            has_touch=is_mobile,
        )

        # Make navigator.webdriver undetectable
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await context.new_page()

        try:
            if traffic_source == "EPApp":
                # ── Path A: EP app deep-link → profile ───────────────────────
                # Simulates opening the player profile directly from the EP app.
                # Uses a mobile/app UA + touch viewport set above.
                print(f"[Runner] Opening EP app (mobile deep-link)…")
                await page.goto(EP_PROFILE_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(2_000, 4_000))
                ep_page = page
                data["profile_found"] = True
                data["ep_url"] = ep_page.url
                print(f"[Runner] Landed via EP app: {ep_page.url}")

            elif traffic_source == "Twitter":
                # ── Path B: Twitter/X → EP ───────────────────────────────────
                print(f"[Runner] Loading Twitter/X…")
                await page.goto(TWITTER_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(3_000, 5_000))
                await _slow_scroll(page, steps=random.randint(2, 3))
                await page.goto(EP_PROFILE_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(2_000, 3_500))
                ep_page = page
                data["profile_found"] = True
                data["ep_url"] = ep_page.url
                print(f"[Runner] Landed on EP via Twitter: {ep_page.url}")

            elif traffic_source == "Instagram":
                # ── Path C: Instagram → EP ───────────────────────────────────
                print(f"[Runner] Loading Instagram…")
                await page.goto(INSTAGRAM_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(3_000, 5_000))
                await _slow_scroll(page, steps=random.randint(2, 3))
                await page.goto(EP_PROFILE_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(2_000, 3_500))
                ep_page = page
                data["profile_found"] = True
                data["ep_url"] = ep_page.url
                print(f"[Runner] Landed on EP via Instagram: {ep_page.url}")

            elif traffic_source == "HockeyDB":
                # ── Path D: HockeyDB → EP ────────────────────────────────────
                print(f"[Runner] Loading HockeyDB…")
                await page.goto(HOCKEYDB_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(2_000, 4_000))
                await _slow_scroll(page, steps=random.randint(2, 3))
                await page.goto(EP_PROFILE_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(2_000, 3_500))
                ep_page = page
                data["profile_found"] = True
                data["ep_url"] = ep_page.url
                print(f"[Runner] Landed on EP via HockeyDB: {ep_page.url}")

            elif traffic_source == "ScoutingNews":
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

            elif traffic_source == "Bing":
                # ── Path D: Bing search → EP ─────────────────────────────────
                print(f"[Runner] Searching Bing…")
                await page.goto(BING_SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(2_000, 4_000))
                await _slow_scroll(page, steps=2)
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
                print(f"[Runner] Landed on EP via Bing: {ep_page.url}")

            elif traffic_source == "DDG":
                # ── Path E: DuckDuckGo search → EP ──────────────────────────
                print(f"[Runner] Searching DuckDuckGo…")
                await page.goto(DDG_SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(2_000, 4_000))
                await _slow_scroll(page, steps=2)
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
                print(f"[Runner] Landed on EP via DuckDuckGo: {ep_page.url}")

            elif traffic_source == "Google":
                # ── Path F: Google search → EP ───────────────────────────────
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

            # ── Step 4: Dwell + optional deep session ────────────────────────
            dwell_s = random.randint(20, 60)
            print(f"[Runner] Dwelling {dwell_s}s…")
            half = dwell_s // 2
            await ep_page.wait_for_timeout(half * 1_000)
            await _slow_scroll(ep_page, steps=random.randint(2, 5))
            await ep_page.wait_for_timeout((dwell_s - half) * 1_000)

            # 30% of runs do a deeper session: click team or league page,
            # browse briefly, then return to the player profile.
            # This looks like a scout doing real research.
            if random.random() < 0.30:
                try:
                    deep_link = await ep_page.query_selector(
                        "a[href*='/team/'], a[href*='/league/'], a[href*='/organization/']"
                    )
                    if deep_link:
                        deep_href = await deep_link.get_attribute("href")
                        print(f"[Runner] Deep session — clicking: {deep_href}")
                        await deep_link.scroll_into_view_if_needed()
                        await ep_page.wait_for_timeout(random.randint(400, 900))
                        await deep_link.click()
                        await ep_page.wait_for_load_state("domcontentloaded", timeout=20_000)
                        await ep_page.wait_for_timeout(random.randint(4_000, 8_000))
                        await _slow_scroll(ep_page, steps=random.randint(2, 4))
                        # Navigate back to player profile
                        await ep_page.goto(EP_PROFILE_URL, wait_until="domcontentloaded", timeout=60_000)
                        await ep_page.wait_for_timeout(random.randint(2_000, 3_000))
                        print(f"[Runner] Deep session complete — back on profile.")
                except Exception as deep_err:
                    print(f"[Runner] Deep session skipped: {deep_err}")

            # ── Verify we're on the profile page ────────────────────────────
            if "michael-dipalma" not in ep_page.url:
                print(f"[Runner] Not on profile page (at {ep_page.url}), navigating directly.")
                await ep_page.goto(EP_PROFILE_URL, wait_until="domcontentloaded", timeout=60_000)
                await ep_page.wait_for_timeout(3_000)
                data["ep_url"] = ep_page.url

            # ── Step 5: Scrape view count (first attempt — on profile page) ─
            view_count, debug_profile = await _scrape_view_count(ep_page, label="profile")
            if view_count:
                data["view_count"] = view_count

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
            debug_analytics = ""
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
                    await ep_page.wait_for_timeout(5_000)
                    data["analytics_opened"] = True
                    print(f"[Runner] Analytics tab clicked. Now at: {ep_page.url}")

                    # ── Step 7b: Detect paywall ──────────────────────────────
                    body = await ep_page.inner_text("body")
                    if any(kw in body.lower() for kw in PAYWALL_KEYWORDS):
                        data["blocked"] = True
                        data["notes"] += "Paywall detected. "
                        print("[Runner] Paywall detected.")
                    else:
                        data["notes"] += "Analytics loaded (no paywall). "

                    # ── Step 8: SECOND scrape attempt — on the analytics page
                    # The view count almost certainly lives here, not on the
                    # main profile page. This is the critical fix.
                    if not view_count:
                        view_count2, debug_analytics = await _scrape_view_count(
                            ep_page, label="analytics"
                        )
                        if view_count2:
                            view_count = view_count2
                            data["view_count"] = view_count2
                            print(f"[Runner] View count captured on analytics page: {view_count2}")
                else:
                    data["notes"] += "Profile Analytics tab not found. "
                    print("[Runner] Analytics tab not found.")
            except Exception as e:
                data["notes"] += f"Analytics error: {e}. "

            # ── Step 9: If still no count, write debug info to Notes ────────
            # This makes the debug snippet visible in Notion so we can finally
            # see what EP is actually serving without needing Railway logs.
            if not view_count:
                combined_debug = ""
                if debug_profile:
                    combined_debug += f"PROFILE: {debug_profile} "
                if debug_analytics:
                    combined_debug += f"ANALYTICS: {debug_analytics} "
                if combined_debug:
                    # Cap at 1500 chars so Notion's rich_text doesn't truncate badly
                    data["notes"] += f"[DEBUG] {combined_debug[:1500]} "

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
