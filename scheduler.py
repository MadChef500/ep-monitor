"""
scheduler.py
Builds a fresh randomized daily schedule and fires the monitoring runs.

Rules:
  - 8–14 runs/day at randomized times between 7:00 AM and 7:30 PM ET
  - At least 2 runs tagged "Non-US" each day
  - 8:00 PM ET  → end-of-day summary posted to Notion
  - 9:00 PM ET  → alert if fewer than 8 runs completed today
  - 9:30 PM ET  → catch-up Non-US run(s) if fewer than 2 non-US completed
"""

import asyncio
import random
from datetime import datetime

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from runner import run_check
from notion_logger import count_today_runs, log_summary, log_alert

ET = pytz.timezone("America/New_York")

# In-memory count (supplementary; Notion is the source of truth)
_today_counts = {"total": 0, "non_us": 0}

scheduler = BlockingScheduler(timezone=ET)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _run_sync(run_type: str = "US") -> None:
    """Synchronous wrapper so APScheduler can call the async runner."""
    now_str = datetime.now(ET).strftime("%I:%M %p ET")
    print(f"\n[Scheduler] Firing {run_type} run at {now_str}")
    result = asyncio.run(run_check(run_type))
    _today_counts["total"] += 1
    if run_type == "Non-US":
        _today_counts["non_us"] += 1
    print(f"[Scheduler] Done. In-memory today: {_today_counts}")


def _build_daily_schedule() -> list[dict]:
    """Return a list of {hour, minute, run_type} dicts for today."""
    num_runs = random.randint(8, 14)

    # Window: 7:00 AM–7:30 PM ET (750 minutes)
    window_start = 7 * 60        # 420
    window_end   = 19 * 60 + 30  # 1170

    minutes = sorted(random.sample(range(window_start, window_end), num_runs))

    # Ensure at least 2 non-US slots
    non_us_indices = set(random.sample(range(num_runs), min(2, num_runs)))

    runs = []
    for i, m in enumerate(minutes):
        runs.append({
            "hour":     m // 60,
            "minute":   m % 60,
            "run_type": "Non-US" if i in non_us_indices else "US",
        })
    return runs


# ── Daily job adder ───────────────────────────────────────────────────────────

def schedule_today() -> None:
    """Called at midnight (and once at startup) to build the day's run schedule."""
    global _today_counts
    _today_counts = {"total": 0, "non_us": 0}

    runs = _build_daily_schedule()
    now = datetime.now(ET)

    added = 0
    for run in runs:
        # Skip times already passed today
        if run["hour"] < now.hour or (run["hour"] == now.hour and run["minute"] <= now.minute):
            continue

        job_id = f"run_{run['hour']:02d}{run['minute']:02d}"
        scheduler.add_job(
            _run_sync,
            trigger=CronTrigger(
                hour=run["hour"],
                minute=run["minute"],
                timezone=ET,
            ),
            args=[run["run_type"]],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=300,
        )
        added += 1

    print(
        f"[Scheduler] Scheduled {added}/{len(runs)} runs for today "
        f"({sum(1 for r in runs if r['run_type'] == 'Non-US')} non-US)."
    )


# ── Fixed daily jobs ──────────────────────────────────────────────────────────

def end_of_day_summary() -> None:
    """8:00 PM ET — post today's summary to Notion."""
    try:
        counts = count_today_runs()
        msg = (
            f"End-of-day summary {datetime.now(ET).strftime('%Y-%m-%d')}: "
            f"{counts['total']} runs, {counts['success']} successful, "
            f"{counts['non_us']} non-US."
        )
        print(f"[Summary] {msg}")
        log_summary(msg)
    except Exception as e:
        print(f"[Summary] Error: {e}")


def behind_schedule_alert() -> None:
    """9:00 PM ET — alert if fewer than 8 runs completed."""
    try:
        counts = count_today_runs()
        if counts["total"] < 8:
            msg = (
                f"BEHIND SCHEDULE: only {counts['total']}/8 minimum runs "
                f"completed as of 9:00 PM ET."
            )
            print(f"[Alert] {msg}")
            log_alert(msg)
        else:
            print(f"[Alert Check] On schedule: {counts['total']} runs.")
    except Exception as e:
        print(f"[Alert] Error: {e}")


def non_us_catchup() -> None:
    """9:30 PM ET — run catch-up Non-US check(s) if needed."""
    try:
        counts = count_today_runs()
        needed = max(0, 2 - counts["non_us"])
        if needed > 0:
            msg = (
                f"Non-US catch-up: {counts['non_us']}/2 non-US runs completed. "
                f"Running {needed} catch-up run(s) now."
            )
            print(f"[Catch-up] {msg}")
            log_alert(msg)
            for _ in range(needed):
                _run_sync("Non-US")
        else:
            print(f"[Catch-up Check] Non-US OK: {counts['non_us']} runs.")
    except Exception as e:
        print(f"[Catch-up] Error: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("[Scheduler] Starting EP Monitor scheduler…")

    # Build today's schedule immediately on startup
    schedule_today()

    # Rebuild schedule every day at midnight
    scheduler.add_job(
        schedule_today,
        trigger=CronTrigger(hour=0, minute=0, timezone=ET),
        id="daily_reset",
        replace_existing=True,
    )

    # Fixed daily jobs
    scheduler.add_job(
        end_of_day_summary,
        trigger=CronTrigger(hour=20, minute=0, timezone=ET),
        id="summary",
        replace_existing=True,
    )
    scheduler.add_job(
        behind_schedule_alert,
        trigger=CronTrigger(hour=21, minute=0, timezone=ET),
        id="behind_alert",
        replace_existing=True,
    )
    scheduler.add_job(
        non_us_catchup,
        trigger=CronTrigger(hour=21, minute=30, timezone=ET),
        id="non_us_catchup",
        replace_existing=True,
    )

    print("[Scheduler] All jobs registered. Running…")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("[Scheduler] Stopped.")


if __name__ == "__main__":
    main()
