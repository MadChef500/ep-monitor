"""
notion_logger.py
Handles all Notion API interactions for the EP Monitor.
"""

import os
import re
import requests
from datetime import datetime, date
import pytz

NOTION_TOKEN = os.environ["NOTION_TOKEN"].strip()
DATABASE_ID = os.environ["NOTION_DATABASE_ID"].strip()

ET = pytz.timezone("America/New_York")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def _text(value: str) -> dict:
    return {"rich_text": [{"text": {"content": str(value)}}]}


def get_last_view_count() -> int | None:
    """Return the most recent logged view count, or None if no prior runs."""
    payload = {
        "sorts": [{"timestamp": "created_time", "direction": "descending"}],
        "page_size": 10,
    }
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{DATABASE_ID}/query",
        headers=HEADERS,
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    for row in resp.json().get("results", []):
        rt = _run_type(row)
        if rt in ("Summary", "Alert"):
            continue
        raw = (
            row.get("properties", {})
            .get("Site Visit Count", {})
            .get("rich_text", [{}])
        )
        if raw and raw[0].get("text", {}).get("content", "").strip().isdigit():
            return int(raw[0]["text"]["content"].strip())
    return None


def log_run(data: dict) -> dict:
    """Write one monitoring run as a row in the Notion database."""
    now = datetime.now(ET)

    # Calculate delta vs last logged count
    view_count_str = str(data.get("view_count", "N/A"))
    delta_str = "N/A"
    if view_count_str.isdigit():
        last = get_last_view_count()
        if last is not None:
            delta = int(view_count_str) - last
            delta_str = f"+{delta}" if delta >= 0 else str(delta)
        else:
            delta_str = "first run"

    properties = {
        "Search Phrase":        {"title": [{"text": {"content": data.get("search_phrase", "MHR roster → EP")}}]},
        "Date":                 {"date": {"start": now.strftime("%Y-%m-%d")}},
        "Time":                 _text(now.strftime("%I:%M %p ET")),
        "Traffic Source":       _text(data.get("traffic_source", "MHR roster → EP")),
        "Search Engine":        _text(data.get("search_engine", "Direct")),
        "Search Location":      _text(data.get("search_location", "US")),
        "Profile Found":        {"checkbox": bool(data.get("profile_found", False))},
        "EliteProspects URL":   {"url": data.get("ep_url") or None},
        "Profile Analytics Opened": {"checkbox": bool(data.get("analytics_opened", False))},
        "Blocked/Paywall":      {"checkbox": bool(data.get("blocked", False))},
        "Site Visit Count":     _text(view_count_str),
        "View Count Change":    _text(delta_str),
        "Session Duration":     {"number": int(data.get("session_duration", 0))},
        "Run Type":             {"select": {"name": data.get("run_type", "US")}},
        "Result":               {"select": {"name": data.get("result", "Unknown")}},
        "Notes":                _text(data.get("notes", "")),
    }

    payload = {
        "parent": {"database_id": DATABASE_ID},
        "properties": properties,
    }

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=HEADERS,
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_today_runs() -> list:
    """Return all rows logged today."""
    today = date.today().strftime("%Y-%m-%d")
    payload = {
        "filter": {
            "property": "Date",
            "date": {"equals": today},
        }
    }
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{DATABASE_ID}/query",
        headers=HEADERS,
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def _run_type(row: dict) -> str:
    return (
        row.get("properties", {})
        .get("Run Type", {})
        .get("select", {})
        .get("name", "")
    )


def _result(row: dict) -> str:
    return (
        row.get("properties", {})
        .get("Result", {})
        .get("select", {})
        .get("name", "")
    )


def count_today_runs() -> dict:
    """Return {total, non_us, success} counts for today."""
    rows = get_today_runs()
    total = non_us = success = 0
    for row in rows:
        rt = _run_type(row)
        if rt in ("Summary", "Alert"):
            continue
        total += 1
        if rt == "Non-US":
            non_us += 1
        if _result(row) == "Success":
            success += 1
    return {"total": total, "non_us": non_us, "success": success}


def log_summary(message: str) -> dict:
    """Write an informational/summary row."""
    return log_run({
        "search_phrase": message[:200],
        "run_type": "Summary",
        "result": "Info",
        "notes": message,
    })


def log_alert(message: str) -> dict:
    """Write an alert row."""
    return log_run({
        "search_phrase": message[:200],
        "run_type": "Alert",
        "result": "Alert",
        "notes": message,
    })
