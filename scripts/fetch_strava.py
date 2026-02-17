#!/usr/bin/env python3
"""Fetch recent Strava activities incrementally and maintain local cache + markdown files."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
DATA_DIR = ROOT_DIR / "data"
CACHE_PATH = DATA_DIR / "activities_cache.json"
CURRENT_WEEK_PATH = DATA_DIR / "current_week.md"
WEEKLY_DIR = DATA_DIR / "weekly"

STRAVA_TOKEN_URL = "https://www.strava.com/api/v3/oauth/token"
STRAVA_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
BACKFILL_DAYS = 14
METERS_PER_MILE = 1609.34


# ---------------------------------------------------------------------------
# Strava Auth
# ---------------------------------------------------------------------------
def update_env_token(env_path: Path, new_token: str) -> None:
    """Replace the STRAVA_REFRESH_TOKEN value in the .env file."""
    lines = env_path.read_text().splitlines()
    updated = []
    for line in lines:
        if line.startswith("STRAVA_REFRESH_TOKEN="):
            updated.append(f"STRAVA_REFRESH_TOKEN={new_token}")
        else:
            updated.append(line)
    env_path.write_text("\n".join(updated) + "\n")


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """Exchange the refresh token for a fresh access token."""
    resp = requests.post(
        STRAVA_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        timeout=15,
    )

    if resp.status_code != 200:
        print("ERROR: Failed to refresh Strava access token.", file=sys.stderr)
        print(f"  Status: {resp.status_code}", file=sys.stderr)
        print(f"  Response: {resp.text}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    new_refresh = data.get("refresh_token", refresh_token)
    if new_refresh != refresh_token:
        update_env_token(ENV_PATH, new_refresh)

    return data["access_token"]


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------
def load_cache() -> dict:
    """Load the activity cache. Returns {activity_id_str: activity_dict}."""
    if CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text())
            if isinstance(data, list):
                # Migrate from list to dict keyed by ID
                return {str(a["id"]): a for a in data}
            return data
        except (json.JSONDecodeError, KeyError):
            return {}
    return {}


def save_cache(cache: dict) -> None:
    """Write the activity cache to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2) + "\n")


def get_most_recent_timestamp(cache: dict) -> Optional[int]:
    """Return the epoch timestamp of the most recent cached activity, or None."""
    if not cache:
        return None
    most_recent = max(cache.values(), key=lambda a: a["start_date_local"])
    dt = datetime.fromisoformat(most_recent["start_date_local"].replace("Z", "+00:00"))
    return int(dt.timestamp())


# ---------------------------------------------------------------------------
# Strava API
# ---------------------------------------------------------------------------
def fetch_activities_after(access_token: str, after_epoch: int) -> list[dict]:
    """Fetch all activities after a given epoch timestamp, paginating as needed."""
    all_activities = []
    page = 1
    while True:
        resp = requests.get(
            STRAVA_ACTIVITIES_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"after": after_epoch, "per_page": 100, "page": page},
            timeout=15,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        all_activities.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return all_activities


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def format_duration(seconds: int) -> str:
    """Format seconds as H:MM:SS or M:SS."""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_duration_hm(seconds: int) -> str:
    """Format seconds as Xh YYm."""
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def format_pace(moving_time_sec: int, distance_mi: float) -> str:
    """Format run pace as M:SS/mi."""
    if distance_mi == 0:
        return "—"
    pace_min = (moving_time_sec / 60) / distance_mi
    whole_min = int(pace_min)
    frac_sec = int(round((pace_min - whole_min) * 60))
    if frac_sec == 60:
        whole_min += 1
        frac_sec = 0
    return f"{whole_min}:{frac_sec:02d}/mi"


def format_speed(distance_mi: float, moving_time_sec: int) -> str:
    """Format ride speed as X.X mph."""
    if moving_time_sec == 0:
        return "—"
    mph = distance_mi / (moving_time_sec / 3600)
    return f"{mph:.1f} mph"


def is_run(activity: dict) -> bool:
    return activity.get("type", "").lower() in ("run", "virtualrun")


def is_ride(activity: dict) -> bool:
    return activity.get("type", "").lower() in ("ride", "virtualride")


def pace_or_speed(activity: dict) -> str:
    """Return pace for runs, speed for rides, or — for others."""
    dist_mi = activity.get("distance", 0) / METERS_PER_MILE
    moving = activity.get("moving_time", 0)
    if is_run(activity):
        return format_pace(moving, dist_mi)
    if is_ride(activity):
        return format_speed(dist_mi, moving)
    return "—"


def activity_type_short(activity: dict) -> str:
    """Short label for the activity type."""
    t = activity.get("type", "Other")
    mapping = {
        "Run": "Run",
        "VirtualRun": "Run",
        "Ride": "Ride",
        "VirtualRide": "Ride",
        "Swim": "Swim",
        "Walk": "Walk",
        "Hike": "Hike",
        "Yoga": "Yoga",
        "WeightTraining": "Wts",
        "Workout": "Wrkt",
    }
    return mapping.get(t, t[:4])


# ---------------------------------------------------------------------------
# Week helpers
# ---------------------------------------------------------------------------
def week_monday(dt) -> datetime:
    """Return the Monday (as a date) of the ISO week containing dt."""
    if isinstance(dt, datetime):
        d = dt.date()
    else:
        d = dt
    return d - timedelta(days=d.weekday())


def iso_week_label(monday_date) -> str:
    """Return YYYY-WNN string for a Monday date."""
    iso = monday_date.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def week_date_range_str(monday_date) -> str:
    """Return 'Mon Mon DD - Sun Mon DD' for display."""
    sunday = monday_date + timedelta(days=6)
    return f"Mon {monday_date.strftime('%b %d')} - Sun {sunday.strftime('%b %d')}"


def activities_for_week(cache: dict, monday_date) -> list[dict]:
    """Get all cached activities that fall within a given Mon-Sun week."""
    sunday = monday_date + timedelta(days=6)
    results = []
    for a in cache.values():
        dt = datetime.fromisoformat(a["start_date_local"].replace("Z", "+00:00"))
        d = dt.date()
        if monday_date <= d <= sunday:
            results.append(a)
    results.sort(key=lambda a: a["start_date_local"])
    return results


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------
def build_activity_row(a: dict) -> str:
    """Build a markdown table row for one activity."""
    dt = datetime.fromisoformat(a["start_date_local"].replace("Z", "+00:00"))
    date_str = dt.strftime("%b %d")
    act_type = activity_type_short(a)
    name = a.get("name", "") or ""
    dist_mi = a.get("distance", 0) / METERS_PER_MILE
    dur = format_duration(a.get("moving_time", 0))
    ps = pace_or_speed(a)

    hr = ""
    if a.get("has_heartrate") and a.get("average_heartrate"):
        hr = str(int(a["average_heartrate"]))

    dist_str = f"{dist_mi:.1f} mi" if dist_mi > 0.05 else "—"

    return f"| {date_str} | {act_type} | {name} | {dist_str} | {dur} | {ps} | {hr} |"


def build_week_totals(activities: list[dict], monday_date) -> str:
    """Build the totals section for a week."""
    runs = [a for a in activities if is_run(a)]
    rides = [a for a in activities if is_ride(a)]

    run_miles = sum(a["distance"] / METERS_PER_MILE for a in runs)
    ride_miles = sum(a["distance"] / METERS_PER_MILE for a in rides)
    run_time = sum(a.get("moving_time", 0) for a in runs)
    ride_time = sum(a.get("moving_time", 0) for a in rides)
    total_time = sum(a.get("moving_time", 0) for a in activities)

    run_pace = format_pace(run_time, run_miles) if run_miles > 0 else "—"

    # Active days
    active_days = set()
    for a in activities:
        dt = datetime.fromisoformat(a["start_date_local"].replace("Z", "+00:00"))
        active_days.add(dt.date())

    now = datetime.now().date()
    sunday = monday_date + timedelta(days=6)
    days_so_far = (min(now, sunday) - monday_date).days + 1
    rest_days = days_so_far - len(active_days)

    is_current = monday_date <= now <= sunday

    lines = []
    lines.append(f"- Running: {run_miles:.1f} mi | {len(runs)} {'run' if len(runs) == 1 else 'runs'} | avg pace {run_pace}")
    lines.append(f"- Cycling: {ride_miles:.1f} mi | {len(rides)} {'ride' if len(rides) == 1 else 'rides'} | {format_duration_hm(ride_time)}")
    lines.append(f"- Strength: (see status.md)")
    if is_current:
        lines.append(f"- Rest days so far: {rest_days}/{days_so_far}")
    else:
        lines.append(f"- Rest days: {rest_days}")
    lines.append(f"- Total training hours: {format_duration_hm(total_time)}")

    return "\n".join(lines)


def write_current_week(cache: dict) -> None:
    """Write data/current_week.md for the current Mon-Sun week."""
    now = datetime.now()
    monday = week_monday(now)
    activities = activities_for_week(cache, monday)

    sunday = monday + timedelta(days=6)
    header = f"# Current Week ({week_date_range_str(monday)})"
    updated = f"*Last updated: {now.strftime('%Y-%m-%d %H:%M')}*"

    lines = [header, updated, "", "## Totals"]
    lines.append(build_week_totals(activities, monday))

    lines.append("")
    lines.append("## Activities")
    lines.append("| Date | Type | Name | Dist | Time | Pace/Speed | Avg HR |")
    lines.append("|------|------|------|------|------|------------|--------|")

    if activities:
        for a in activities:
            lines.append(build_activity_row(a))
    else:
        lines.append("| — | — | No activities yet | — | — | — | — |")

    lines.append("")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CURRENT_WEEK_PATH.write_text("\n".join(lines) + "\n")


def generate_weekly_summary(cache: dict, monday_date) -> None:
    """Generate data/weekly/YYYY-WNN.md from cached activities for a past week."""
    label = iso_week_label(monday_date)
    week_path = WEEKLY_DIR / f"{label}.md"

    if week_path.exists():
        return  # Already generated

    activities = activities_for_week(cache, monday_date)

    sunday = monday_date + timedelta(days=6)
    iso_wk = monday_date.isocalendar()[1]
    header = f"# Week {iso_wk:02d} ({week_date_range_str(monday_date)})"

    lines = [header, "", "## Totals"]
    lines.append(build_week_totals(activities, monday_date))

    lines.append("")
    lines.append("## Activities")
    lines.append("| Date | Type | Name | Dist | Time | Pace/Speed | Avg HR |")
    lines.append("|------|------|------|------|------|------------|--------|")

    if activities:
        for a in activities:
            lines.append(build_activity_row(a))
    else:
        lines.append("| — | — | No activities | — | — | — | — |")

    # Placeholder sections for Claude to fill in
    lines.append("")
    lines.append("## Body & Injury Notes")
    lines.append("- (to be filled in)")
    lines.append("")
    lines.append("## Training Notes")
    lines.append("- (to be filled in)")
    lines.append("")

    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    week_path.write_text("\n".join(lines) + "\n")


def check_weekly_rollover(cache: dict) -> Optional[str]:
    """If we're in a new week and last week's summary doesn't exist, generate it.

    Returns the label of the generated week, or None.
    """
    now = datetime.now()
    this_monday = week_monday(now)
    last_monday = this_monday - timedelta(days=7)

    label = iso_week_label(last_monday)
    week_path = WEEKLY_DIR / f"{label}.md"

    if not week_path.exists():
        generate_weekly_summary(cache, last_monday)
        return label

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Strava activities incrementally")
    parser.add_argument("--force", action="store_true", help="Re-fetch all activities (ignore cache)")
    args = parser.parse_args()

    # Load credentials
    load_dotenv(ENV_PATH)
    client_id = os.getenv("STRAVA_CLIENT_ID")
    client_secret = os.getenv("STRAVA_CLIENT_SECRET")
    refresh_token = os.getenv("STRAVA_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        print("ERROR: Missing Strava credentials in .env", file=sys.stderr)
        print(f"  Expected .env at: {ENV_PATH}", file=sys.stderr)
        sys.exit(1)

    # Load cache
    cache = load_cache()

    if args.force:
        cache = {}

    # Determine fetch window
    most_recent = get_most_recent_timestamp(cache)
    if most_recent is None:
        # First run or force: backfill last 14 days
        after_epoch = int((datetime.now() - timedelta(days=BACKFILL_DAYS)).timestamp())
    else:
        after_epoch = most_recent

    # Authenticate and fetch
    access_token = refresh_access_token(client_id, client_secret, refresh_token)
    new_activities = fetch_activities_after(access_token, after_epoch)

    # Merge into cache (deduplicate by ID)
    added = 0
    for a in new_activities:
        aid = str(a["id"])
        if aid not in cache:
            cache[aid] = a
            added += 1

    # Save cache
    save_cache(cache)

    # Check weekly rollover (auto-generate last week's summary if needed)
    rollover_label = check_weekly_rollover(cache)

    # Write current week markdown
    write_current_week(cache)

    # Status output
    parts = []
    if added > 0:
        parts.append(f"Fetched {added} new {'activity' if added == 1 else 'activities'}.")
    else:
        parts.append("No new activities.")
    parts.append("Current week file updated.")
    if rollover_label:
        parts.append(f"Generated weekly summary: {rollover_label}.")

    print(" ".join(parts))


if __name__ == "__main__":
    main()
