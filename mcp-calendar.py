"""
MCP Calendar Server
====================
FastMCP server exposing calendar data as tools for AI assistants.

Environment variables
---------------------
ICS_URLS        (required) space-separated .ics URLs
TZ              (optional) IANA timezone, default "Europe/Nicosia"
CACHE_MS        (optional) in-memory cache TTL ms, default 60000
OVERRIDE_NOW    (optional) ISO datetime to override "now" for testing

Run
---
  pip install fastmcp
  ICS_URLS="https://..." python mcp_calendar.py

  # or via mcp CLI:
  ICS_URLS="https://..." fastmcp run mcp_calendar.py

Tools
-----
  get_now          → current / next / next-overlapping event + minutesUntilNext
  get_day          → all events for today or a given date
  get_free_slots   → free time slots for today or a given date
"""

from __future__ import annotations

import os
import time
from datetime import datetime, date, timedelta, timezone
from typing import Any, Optional

import pytz
from fastmcp import FastMCP

try:
    from icscal.calendar_loader import get_events_for_day
    from icscal.windows_zones import configure as _wz_configure
except ImportError:
    from calendar_loader import get_events_for_day          # type: ignore
    from windows_zones import configure as _wz_configure    # type: ignore

# ---------------------------------------------------------------------------
# Module-level setup
# ---------------------------------------------------------------------------
_wz_configure(file_cache=False)

mcp = FastMCP(
    name="calendar",
    instructions=(
        "Provides access to the user's calendar. "
        "Use get_now to find what is happening right now and what comes next. "
        "Use get_day to see all events on a specific date. "
        "Use get_free_slots to find open time on a specific date."
    ),
)

# ---------------------------------------------------------------------------
# In-memory cache (shared across tool calls in the same process)
# ---------------------------------------------------------------------------
_cache: dict[str, Any] = {}
_CACHE_TTL_MS_DEFAULT = 60_000


def _cache_get(key: str, ttl_ms: int) -> Optional[list]:
    entry = _cache.get(key)
    if entry and (time.time() * 1000 - entry["ts_ms"]) < ttl_ms:
        return entry["data"]
    return None


def _cache_set(key: str, data: list) -> None:
    _cache[key] = {"ts_ms": int(time.time() * 1000), "data": data}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _resolve_tz() -> pytz.BaseTzInfo:
    name = os.environ.get("TZ", "Europe/Nicosia").strip() or "Europe/Nicosia"
    try:
        return pytz.timezone(name)
    except pytz.exceptions.UnknownTimeZoneError:
        return pytz.timezone("Europe/Nicosia")


def _resolve_now(override: Optional[str] = None) -> datetime:
    """Return aware UTC datetime from override string or OVERRIDE_NOW env var or real now."""
    raw = override or os.environ.get("OVERRIDE_NOW", "")
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _resolve_date(date_str: Optional[str], tz: pytz.BaseTzInfo,
                  now_utc: datetime) -> date:
    """Parse ISO date string, or return today in local tz."""
    if date_str:
        try:
            return date.fromisoformat(date_str)
        except ValueError:
            pass
    return now_utc.astimezone(tz).date()


def _ics_urls() -> list[str]:
    raw = os.environ.get("ICS_URLS", "").strip()
    if not raw:
        raise ValueError("ICS_URLS environment variable is not set")
    return raw.split()


def _cache_ms() -> int:
    try:
        return int(os.environ.get("CACHE_MS", str(_CACHE_TTL_MS_DEFAULT)))
    except (ValueError, TypeError):
        return _CACHE_TTL_MS_DEFAULT


def _fetch_events(target: date, tz: pytz.BaseTzInfo,
                  now_utc: datetime) -> list[dict]:
    """Fetch (or return cached) events for a given date."""
    urls = _ics_urls()
    tz_name = tz.zone
    key = f"{','.join(sorted(urls))}|{tz_name}|{target.isoformat()}"
    ttl = _cache_ms()

    cached = _cache_get(key, ttl)
    if cached is not None:
        return cached

    events = get_events_for_day(
        calendar_urls=urls,
        user_timezone=tz_name,
        target_date=target,
        now_override=now_utc,
    )
    _cache_set(key, events)
    return events


def _fmt(ev: dict, tz: pytz.BaseTzInfo) -> dict:
    """Convert a library event to a clean dict with local times."""
    start_local = datetime.fromisoformat(ev["start_iso"]).astimezone(tz)
    end_local   = datetime.fromisoformat(ev["end_iso"]).astimezone(tz)
    return {
        "uid":        ev["uid"],
        "title":      ev["summary"],
        "start":      start_local.isoformat(),
        "end":        end_local.isoformat(),
        "location":   ev.get("location"),
        "organizer":  ev.get("organizer"),
    }


def _minutes_until(now_utc: datetime, ev: Optional[dict]) -> Optional[int]:
    if ev is None:
        return None
    start_utc = datetime.fromisoformat(ev["start_iso"])
    return max(0, int((start_utc - now_utc).total_seconds() / 60))


# ---------------------------------------------------------------------------
# Tool 1 — get_now
# ---------------------------------------------------------------------------

@mcp.tool()
def get_now(
    override_now: Optional[str] = None,
) -> dict:
    """
    Return what is happening RIGHT NOW and what comes next.

    Returns current event, next non-overlapping event, next overlapping event,
    and how many minutes until the next event starts.

    Args:
        override_now: Optional ISO datetime to use as "now" (for testing),
                      e.g. "2026-02-09T10:00:00+02:00". Defaults to real time.
    """
    tz      = _resolve_tz()
    now_utc = _resolve_now(override_now)
    today   = now_utc.astimezone(tz).date()
    events  = _fetch_events(today, tz, now_utc)

    current_ev  = next((e for e in events if e["is_current"]),          None)
    next_ev     = next((e for e in events if e["is_next"]),             None)
    next_ov_ev  = next((e for e in events if e["is_next_overlapping"]), None)

    return {
        "now":                now_utc.astimezone(tz).isoformat(),
        "current":            _fmt(current_ev,  tz) if current_ev  else None,
        "next":               _fmt(next_ev,     tz) if next_ev     else None,
        "nextOverlapping":    _fmt(next_ov_ev,  tz) if next_ov_ev  else None,
        "minutesUntilNext":   _minutes_until(now_utc, next_ev),
        "isOverlappingNow":   next_ov_ev is not None,
    }


# ---------------------------------------------------------------------------
# Tool 2 — get_day
# ---------------------------------------------------------------------------

@mcp.tool()
def get_day(
    date_str: Optional[str] = None,
    override_now: Optional[str] = None,
) -> dict:
    """
    Return all events for a given day.

    Args:
        date_str:     ISO date "YYYY-MM-DD". Defaults to today.
        override_now: Optional ISO datetime to use as "now" (affects is_current /
                      is_next flags). Defaults to real time.
    """
    tz      = _resolve_tz()
    now_utc = _resolve_now(override_now)
    target  = _resolve_date(date_str, tz, now_utc)
    events  = _fetch_events(target, tz, now_utc)

    window_start = tz.localize(datetime(target.year, target.month, target.day))
    window_end   = tz.normalize(window_start + timedelta(days=1))

    return {
        "date":   target.isoformat(),
        "tz":     tz.zone,
        "window": {
            "start": window_start.isoformat(),
            "end":   window_end.isoformat(),
        },
        "count":  len(events),
        "events": [_fmt(e, tz) for e in events],
    }


# ---------------------------------------------------------------------------
# Tool 3 — get_free_slots
# ---------------------------------------------------------------------------

@mcp.tool()
def get_free_slots(
    date_str:       Optional[str] = None,
    min_duration:   int = 30,
    day_start:      str = "09:00",
    day_end:        str = "18:00",
    override_now:   Optional[str] = None,
) -> dict:
    """
    Return free (unbooked) time slots for a given day.

    Args:
        date_str:      ISO date "YYYY-MM-DD". Defaults to today.
        min_duration:  Minimum slot length in minutes to include. Default 30.
        day_start:     Working day start time "HH:MM". Default "09:00".
        day_end:       Working day end time "HH:MM". Default "18:00".
        override_now:  Optional ISO datetime to use as "now". Defaults to real time.
    """
    tz      = _resolve_tz()
    now_utc = _resolve_now(override_now)
    target  = _resolve_date(date_str, tz, now_utc)
    events  = _fetch_events(target, tz, now_utc)

    # Parse day_start / day_end into aware datetimes on target date
    def _parse_time(t: str) -> datetime:
        h, m = (int(x) for x in t.split(":"))
        return tz.localize(datetime(target.year, target.month, target.day, h, m))

    try:
        work_start = _parse_time(day_start)
        work_end   = _parse_time(day_end)
    except (ValueError, AttributeError):
        work_start = _parse_time("09:00")
        work_end   = _parse_time("18:00")

    # Build a sorted list of (start_utc, end_utc) busy intervals, clipped to working hours
    work_start_utc = work_start.astimezone(timezone.utc)
    work_end_utc   = work_end.astimezone(timezone.utc)

    busy: list[tuple[datetime, datetime]] = []
    for ev in events:
        s = datetime.fromisoformat(ev["start_iso"])
        e = datetime.fromisoformat(ev["end_iso"])
        # Clip to working window
        s = max(s, work_start_utc)
        e = min(e, work_end_utc)
        if s < e:
            busy.append((s, e))

    # Merge overlapping busy intervals
    busy.sort(key=lambda x: x[0])
    merged: list[tuple[datetime, datetime]] = []
    for s, e in busy:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # Find free gaps between merged busy intervals
    slots = []
    cursor = work_start_utc

    for busy_start, busy_end in merged:
        if cursor < busy_start:
            duration_min = int((busy_start - cursor).total_seconds() / 60)
            if duration_min >= min_duration:
                slots.append({
                    "start":       cursor.astimezone(tz).isoformat(),
                    "end":         busy_start.astimezone(tz).isoformat(),
                    "duration_min": duration_min,
                })
        cursor = max(cursor, busy_end)

    # Gap after last busy interval until end of working day
    if cursor < work_end_utc:
        duration_min = int((work_end_utc - cursor).total_seconds() / 60)
        if duration_min >= min_duration:
            slots.append({
                "start":        cursor.astimezone(tz).isoformat(),
                "end":          work_end_utc.astimezone(tz).isoformat(),
                "duration_min": duration_min,
            })

    return {
        "date":         target.isoformat(),
        "tz":           tz.zone,
        "workingHours": {"start": day_start, "end": day_end},
        "minDuration":  min_duration,
        "freeSlots":    slots,
        "totalFreeMin": sum(s["duration_min"] for s in slots),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
