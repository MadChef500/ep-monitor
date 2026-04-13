"""
daily_sms.py
Sends a 7 AM ET daily text summary of yesterday's EP monitoring results.
"""

import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import pytz
import requests

ET = pytz.timezone("America/New_York")

NOTION_TOKEN     = os.environ["NOTION_TOKEN"].strip()
DATABASE_ID      = os.environ["NOTION_DATABASE_ID"].strip()

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

    # Pull latest season stats from most recent successful run notes
    stats_lines = []
    for row in reversed(rows):
        if _prop_select(row, "Result") == "Success":
            notes = _prop_text(row, "Notes")
            if "Stats:" in notes:
                # Parse stats lines out of notes
                for part in notes.split(" | "):
                    if "GP" in part and "PTS" in part:
                        stats_lines.append(part.strip())
                break

    # Country list one per line
    country_lines = []
    for c, n in country_counts.items():
        country_lines.append(f"  {c} x{n}")

    lines = ["EP Monitor"]
    lines.append(f"{yesterday}")
    lines.append("")
    lines.append(f"Views: {latest_views:,}" if latest_views else "Views: N/A")
    if total_delta is None:
        lines.append("New views: N/A")
    elif total_delta < 0:
        lines.append("New views: N/A")
    else:
        lines.append(f"New views: +{total_delta}")
    lines.append(organic_str)
    lines.append("")
    lines.append(f"Our runs: {success}/{total} success")
    if country_lines:
        lines.append("")
        lines.extend(country_lines)

    if stats_lines:
        lines.append("")
        lines.append("2025-26 Stats:")
        lines.extend(stats_lines)

    return "\n".join(lines)


def send_sms(body: str) -> None:
    """Send via T-Mobile email-to-SMS gateway — free, no registration needed."""
    gmail_user = os.environ["GMAIL_ADDRESS"].strip()
    gmail_pass = os.environ["GMAIL_APP_PASSWORD"].strip()
    to_sms     = "2675466472@tmomail.net"

    msg = MIMEText(body)
    msg["From"]    = gmail_user
    msg["To"]      = to_sms
    msg["Subject"] = ""

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, to_sms, msg.as_string())
    print(f"[SMS] Sent via T-Mobile gateway:\n{body}")


def main():
    try:
        print("[SMS] Building daily summary...")
        rows = get_yesterday_rows()
        msg = build_summary(rows)
        send_sms(msg)
    except Exception as e:
        print(f"[SMS] ERROR: {e}")


if __name__ == "__main__":
    main()
