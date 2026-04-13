"""
daily_sms.py
Sends a 7 AM ET daily text summary of yesterday's EP monitoring results.
"""

import os
from datetime import datetime, timedelta
import pytz
import requests
from twilio.rest import Client

ET = pytz.timezone("America/New_York")

NOTION_TOKEN     = os.environ["NOTION_TOKEN"].strip()
DATABASE_ID      = os.environ["NOTION_DATABASE_ID"].strip()
TWILIO_SID       = os.environ["TWILIO_ACCOUNT_SID"].strip()
TWILIO_TOKEN     = os.environ["TWILIO_AUTH_TOKEN"].strip()
TWILIO_FROM      = os.environ["TWILIO_FROM_NUMBER"].strip()
TWILIO_TO        = os.environ["TWILIO_TO_NUMBER"].strip()

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def get_yesterday_rows() -> list:
    yesterday = (datetime.now(ET) - timedelta(days=1)).strftime("%Y-%m-%d")
    payload = {
        "filter": {"property": "Date", "date": {"equals": yesterday}},
        "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
        "page_size": 100,
    }
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{DATABASE_ID}/query",
        headers=NOTION_HEADERS,
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def _prop_text(row, col):
    rt = row.get("properties", {}).get(col, {}).get("rich_text", [])
    return rt[0].get("text", {}).get("content", "").strip() if rt else ""


def _prop_select(row, col):
    sel = row.get("properties", {}).get(col, {}).get("select")
    return sel.get("name", "") if sel else ""


def build_summary(rows: list) -> str:
    yesterday = (datetime.now(ET) - timedelta(days=1)).strftime("%b %d")

    total = success = 0
    intl_countries = []
    view_counts = []

    for row in rows:
        run_type = _prop_select(row, "Run Type")
        result   = _prop_select(row, "Result")

        if run_type in ("Summary", "Alert"):
            continue

        total += 1
        if result == "Success":
            success += 1

        location = _prop_text(row, "Search Location")
        if location not in ("US", ""):
            intl_countries.append(location)

        vc = _prop_text(row, "Site Visit Count")
        if vc.isdigit() and 1000 <= int(vc) <= 500000:
            view_counts.append(int(vc))

    # View count delta
    if len(view_counts) >= 2:
        total_delta = view_counts[-1] - view_counts[0]
    elif view_counts:
        total_delta = 0
    else:
        total_delta = None

    latest_views = view_counts[-1] if view_counts else None

    # Organic visitors = total view increase minus our successful runs
    if total_delta is not None and total_delta > success:
        organic = total_delta - success
        organic_str = f"Organic visitors: +{organic}"
    else:
        organic_str = "Organic visitors: 0"

    # Country breakdown
    country_counts = {}
    for c in intl_countries:
        country_counts[c] = country_counts.get(c, 0) + 1
    intl_str = ", ".join(f"{c} x{n}" for c, n in country_counts.items()) if country_counts else "None"

    lines = [
        f"EP Monitor - {yesterday}",
        f"Views: {latest_views:,}" if latest_views else "Views: N/A",
        f"New views: +{total_delta}" if total_delta is not None else "New views: N/A",
        f"{organic_str}",
        f"Our runs: {success}/{total} success",
        f"Intl: {intl_str}",
    ]
    return "\n".join(lines)


def send_sms(body: str) -> None:
    client = Client(TWILIO_SID, TWILIO_TOKEN)
    client.messages.create(body=body, from_=TWILIO_FROM, to=TWILIO_TO)
    print(f"[SMS] Sent:\n{body}")


def main():
    print("[SMS] Building daily summary...")
    rows = get_yesterday_rows()
    msg = build_summary(rows)
    send_sms(msg)


if __name__ == "__main__":
    main()
