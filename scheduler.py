"""
scheduler.py
Builds a fresh randomized daily schedule and fires the monitoring runs.

Rules:
  - 7–12 runs/day at randomized times between 7:00 AM and 7:30 PM ET
  - 3–4 international runs/day from rotating countries (Canada weighted)
  - 8:00 PM ET  → end-of-day summary posted to Notion
  - 9:00 PM ET  → alert if fewer than 8 runs completed today
  - 9:30 PM ET  → catch-up international run(s) if fewer than 1 completed
"""

import subprocess
import sys

# Install Playwright Chromium before anything else — Railway (Railpack)
# overrides Procfile/nixpacks start commands, so this is the only
# reliable place to ensure the browser exists at runtime.
print("[Setup] Installing Playwright Chromium...", flush=True)
subprocess.run(
    [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
    check=True,
)
print("[Setup] Chromium ready.", flush=True)

import asyncio
import random
from datetime import datetime

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from runner import run_check
from notion_logger import count_today_runs, log_summary, log_alert
from daily_sms import main as send_daily_sms

ET = pytz.timezone("America/New_York")

# In-memory count (supplementary; Notion is the source of truth)
_today_counts = {"total": 0, "non_us": 0}

scheduler = BlockingScheduler(timezone=ET)


# ── Helpers ──────────────────────────────────────────────────────────────────

# Countries to rotate through — Canada weighted 2x so it appears most often
INTL_COUNTRIES = ["Canada", "Canada", "UK", "Russia", "Sweden", "Finland", "Czech", "Turkey"]


def _run_sync(run_type: str = "US") -> None:
    """Synchronous wrapper so APScheduler can call the async runner."""
    now_str = datetime.now(ET).strftime("%I:%M %p ET")
    print(f"\n[Scheduler] Firing {run_type} run at {now_str}")
    result = asyncio.run(run_check(run_type))
    _today_counts["total"] += 1
    if run_type != "US":
        _today_counts["non_us"] += 1
    print(f"[Scheduler] Done. In-memory today: {_today_counts}")


def _build_daily_schedule() -> list[dict]:
    """Return a list of {hour, minute, run_type} dicts for today."""
    num_runs = random.randint(7, 12)

    # Window: 7:00 AM–7:30 PM ET (750 minutes)
    window_start = 7 * 60        # 420
    window_end   = 19 * 60 + 30  # 1170

    minutes = sorted(random.sample(range(window_start, window_end), num_runs))

    # Exactly 1 international slot per day
    num_intl = 1
    intl_indices = set(random.sample(range(num_runs), min(num_intl, num_runs)))
    intl_pool = INTL_COUNTRIES.copy()
    random.shuffle(intl_pool)

    intl_iter = iter(intl_pool)
    runs = []
    for i, m in enumerate(minutes):
        if i in intl_indices:
            country = next(intl_iter, random.choice(INTL_COUNTRIES))
            run_type = country
        else:
            run_type = "US"
        runs.append({"hour": m // 60, "minute": m % 60, "run_type": run_type})
    return runs


# ── Daily job adder ───────────────────────────────────────────────────────────

def schedule_today() -> None:
    """Called at midnight (and once at startup) to build the day's run schedule."""
    global _today_counts
    _today_counts = {"total": 0, "non_us": 0}

    # Remove all leftover run jobs from previous day(s) before adding new ones
    for job in scheduler.get_jobs():
        if job.id.startswith("run_"):
            scheduler.remove_job(job.id)

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

    intl_count = sum(1 for r in runs if r["run_type"] != "US")
    print(
        f"[Scheduler] Scheduled {added}/{len(runs)} runs for today "
        f"({intl_count} international)."
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
    """9:30 PM ET — run catch-up international check(s) if needed."""
    try:
        counts = count_today_runs()
        needed = max(0, 1 - counts["non_us"])
        if needed > 0:
            msg = (
                f"Non-US catch-up: {counts['non_us']}/1 international runs completed. "
                f"Running {needed} catch-up run(s) now."
            )
            print(f"[Catch-up] {msg}")
            log_alert(msg)
            for _ in range(needed):
                _run_sync(random.choice(INTL_COUNTRIES))
        else:
            print(f"[Catch-up Check] International OK: {counts['non_us']} runs.")
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

    # Fixed daily jobs — misfire_grace_time gives them a 1-hour window so they
    # still fire even if the service was briefly down at the exact trigger time
    scheduler.add_job(
        send_daily_sms,
        trigger=CronTrigger(hour=7, minute=0, timezone=ET),
        id="daily_sms",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        end_of_day_summary,
        trigger=CronTrigger(hour=20, minute=0, timezone=ET),
        id="summary",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        behind_schedule_alert,
        trigger=CronTrigger(hour=21, minute=0, timezone=ET),
        id="behind_alert",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        non_us_catchup,
        trigger=CronTrigger(hour=21, minute=30, timezone=ET),
        id="non_us_catchup",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # ── Startup SMS recovery ─────────────────────────────────────────────────
    # misfire_grace_time only fires missed triggers when the scheduler was
    # ALREADY running. If Railway restarted the process at/after 7 AM (e.g.
    # for Playwright install ~2 min), APScheduler schedules 7 AM for TOMORROW
    # instead. Catch that by firing the SMS immediately on startup if we land
    # inside the 7 AM hour.
    _now = datetime.now(ET)
    if 7 <= _now.hour < 8:
        print(f"[Scheduler] Startup inside 7 AM window ({_now.strftime('%I:%M %p ET')}) — firing SMS now.")
        try:
            send_daily_sms()
        except Exception as sms_err:
            print(f"[Scheduler] Startup SMS error: {sms_err}")

    print("[Scheduler] All jobs registered. Running…")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("[Scheduler] Stopped.")
    except Exception as e:
        print(f"[Scheduler] CRASHED: {e}")
        raise


if __name__ == "__main__":
    import time as _time
    while True:
        try:
            main()
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            print(f"[Scheduler] Restarting after crash: {e}")
            _time.sleep(30)
            # Re-install Chromium in case it was the cause
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                check=False,
            )
