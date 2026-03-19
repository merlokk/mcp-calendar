"""
MCP Calendar Server
====================
FastMCP server exposing calendar data as tools for AI assistants.

Environment variables
---------------------
ICS_URLS        (required) space-separated .ics URLs
CLOCKIFY_API_KEY (optional) Clockify API key for get_clockify_tasks/get_clockify_free_slots
CLOCKIFY_BASE_URL (optional) Clockify API base URL, default https://api.clockify.me/api
CLOCKIFY_WORKSPACE_ID (optional) override workspace id for Clockify
CLOCKIFY_USER_ID (optional) override user id for Clockify
CLOCKIFY_EMPLOYEES_FILE (optional) path to employees JSON file for employee tasks
TZ              (optional) IANA timezone, default "Europe/Nicosia"
CACHE_MS        (optional) in-memory cache TTL ms, default 60000
OVERRIDE_NOW    (optional) ISO datetime to override "now" for testing
MCP_LOG_FILE_ENABLED (optional) enable JSONL file logging next to mcp_calendar.py

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
  get_clockify_tasks -> Clockify time entries for today or a given date
  get_clockify_free_slots -> free slots from Clockify time entries
  get_clockify_employee_tasks -> Clockify employee tasks for today or a given date

"""

from __future__ import annotations

import os
import json
import time
import inspect
from datetime import datetime, date, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import pytz
from fastmcp import FastMCP

try:
    from icscal.calendar_loader import get_events_for_day
    from icscal.windows_zones import configure as _wz_configure
except ImportError:
    from calendar_loader import get_events_for_day          # type: ignore
    from windows_zones import configure as _wz_configure    # type: ignore

try:
    from clockifycal.loader import get_events_for_day as get_clockify_events_for_day
    from clockifycal.loader import get_free_slots_for_day as get_clockify_free_slots_for_day
    from clockifycal.loader import get_employee_events_for_day as get_clockify_employee_events_for_day
except ImportError:
    get_clockify_events_for_day = None  # type: ignore[assignment]
    get_clockify_free_slots_for_day = None  # type: ignore[assignment]
    get_clockify_employee_events_for_day = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Module-level setup
# ---------------------------------------------------------------------------
_wz_configure(file_cache=False)

mcp = FastMCP(
    name="calendar",
    instructions=(
        "Server purpose: read calendar events from ICS sources and read occupied time from Clockify. "
        "Primary workflow is day-based: pick one target date and call tools for that date. "
        "Use get_server_overview to get full tool descriptions and required parameters."
    ),
)

# ---------------------------------------------------------------------------
# In-memory cache (shared across tool calls in the same process)
# ---------------------------------------------------------------------------
_cache: dict[str, Any] = {}
_CACHE_TTL_MS_DEFAULT = 60_000
_LOG_FILE_ENABLED_ENV = "MCP_LOG_FILE_ENABLED"
_LOG_FILE_NAME = "mcp_calendar.log"
_LOG_ENV_KEYS = (
    "ICS_URLS",
    "CLOCKIFY_API_KEY",
    "CLOCKIFY_BASE_URL",
    "CLOCKIFY_WORKSPACE_ID",
    "CLOCKIFY_USER_ID",
    "CLOCKIFY_EMPLOYEES_FILE",
    "TZ",
    "CACHE_MS",
    "OVERRIDE_NOW",
    _LOG_FILE_ENABLED_ENV,
)
_LOG_REDACTED_ENV_KEYS = {"CLOCKIFY_API_KEY"}


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


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _log_file_enabled() -> bool:
    return _env_flag(_LOG_FILE_ENABLED_ENV, default=False)


def _log_file_path() -> Path:
    return Path(__file__).resolve().with_name(_LOG_FILE_NAME)


def _logged_env_snapshot() -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for key in _LOG_ENV_KEYS:
        value = os.environ.get(key)
        if value is None:
            continue
        if key in _LOG_REDACTED_ENV_KEYS and value:
            snapshot[key] = "***REDACTED***"
        else:
            snapshot[key] = value
    return snapshot


def _append_log_record(record: dict[str, Any]) -> None:
    if not _log_file_enabled():
        return
    try:
        path = _log_file_path()
        line = json.dumps(record, ensure_ascii=True, default=str)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.write("\n")
    except OSError:
        # Logging must not change tool behavior.
        pass


def _tool_call_args(func: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    bound = inspect.signature(func).bind_partial(*args, **kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)


def _logged_tool(func: Any) -> Any:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        invocation_id = uuid4().hex
        tool_args = _tool_call_args(func, args, kwargs)
        _append_log_record(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "request",
                "invocationId": invocation_id,
                "tool": func.__name__,
                "args": tool_args,
                "env": _logged_env_snapshot(),
            }
        )
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            _append_log_record(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "error",
                    "invocationId": invocation_id,
                    "tool": func.__name__,
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                }
            )
            raise
        _append_log_record(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "response",
                "invocationId": invocation_id,
                "tool": func.__name__,
                "response": result,
            }
        )
        return result

    return wrapper


def _clockify_config() -> dict[str, str]:
    return {
        "api_key": os.environ.get("CLOCKIFY_API_KEY", "").strip(),
        "base_url": os.environ.get("CLOCKIFY_BASE_URL", "https://api.clockify.me/api").strip() or "https://api.clockify.me/api",
        "workspace_id": os.environ.get("CLOCKIFY_WORKSPACE_ID", "").strip(),
        "user_id": os.environ.get("CLOCKIFY_USER_ID", "").strip(),
    }


def _clockify_employees_file(default_path: Optional[str] = None) -> str:
    if default_path:
        return default_path
    env_path = os.environ.get("CLOCKIFY_EMPLOYEES_FILE", "").strip()
    if env_path:
        return env_path
    return str(Path(__file__).resolve().with_name("clockifycal").joinpath("employees.json"))


def _load_employee_names(path_value: str) -> list[str]:
    path = Path(path_value)
    if not path.exists():
        raise ValueError(f"Employees file not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        names = [str(item).strip() for item in raw]
    elif isinstance(raw, dict):
        employees = raw.get("employees")
        if not isinstance(employees, list):
            raise ValueError("Employees file must have 'employees' array")
        names = [str(item).strip() for item in employees]
    else:
        raise ValueError("Employees file must be JSON array or object with 'employees'")

    cleaned = [name for name in names if name]
    if not cleaned:
        raise ValueError("Employees file does not contain non-empty names")
    return cleaned


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


def _fetch_clockify_events(target: date, tz: pytz.BaseTzInfo,
                           now_utc: datetime) -> list[dict]:
    cfg = _clockify_config()
    if not cfg["api_key"]:
        raise ValueError("CLOCKIFY_API_KEY environment variable is not set")
    if get_clockify_events_for_day is None:
        raise RuntimeError("clockifycal is not available")

    key = (
        f"clockify|{tz.zone}|{target.isoformat()}|{cfg['base_url']}|"
        f"{cfg['workspace_id']}|{cfg['user_id']}"
    )
    ttl = _cache_ms()

    cached = _cache_get(key, ttl)
    if cached is not None:
        return cached

    events = get_clockify_events_for_day(
        api_key=cfg["api_key"],
        user_timezone=tz.zone,
        target_date=target,
        now_override=now_utc,
        base_url=cfg["base_url"],
        workspace_id=cfg["workspace_id"] or None,
        user_id=cfg["user_id"] or None,
    )
    _cache_set(key, events)
    return events


def _fetch_clockify_free_slots(target: date, tz: pytz.BaseTzInfo,
                               now_utc: datetime) -> list[dict]:
    cfg = _clockify_config()
    if not cfg["api_key"]:
        raise ValueError("CLOCKIFY_API_KEY environment variable is not set")
    if get_clockify_free_slots_for_day is None:
        raise RuntimeError("clockifycal is not available")

    key = (
        f"clockify-slots|{tz.zone}|{target.isoformat()}|{cfg['base_url']}|"
        f"{cfg['workspace_id']}|{cfg['user_id']}"
    )
    ttl = _cache_ms()

    cached = _cache_get(key, ttl)
    if cached is not None:
        return cached

    slots = get_clockify_free_slots_for_day(
        api_key=cfg["api_key"],
        user_timezone=tz.zone,
        target_date=target,
        now_override=now_utc,
        base_url=cfg["base_url"],
        workspace_id=cfg["workspace_id"] or None,
        user_id=cfg["user_id"] or None,
    )
    _cache_set(key, slots)
    return slots


def _fetch_clockify_employee_events(
    target: date,
    tz: pytz.BaseTzInfo,
    now_utc: datetime,
    employees_file: Optional[str] = None,
) -> list[dict]:
    cfg = _clockify_config()
    if not cfg["api_key"]:
        raise ValueError("CLOCKIFY_API_KEY environment variable is not set")
    if get_clockify_employee_events_for_day is None:
        raise RuntimeError("clockifycal is not available")

    resolved_employees_file = _clockify_employees_file(employees_file)
    employee_names = _load_employee_names(resolved_employees_file)

    key = (
        f"clockify-employees|{tz.zone}|{target.isoformat()}|{cfg['base_url']}|"
        f"{cfg['workspace_id']}|{resolved_employees_file}|{','.join(sorted(employee_names))}"
    )
    ttl = _cache_ms()

    cached = _cache_get(key, ttl)
    if cached is not None:
        return cached

    events = get_clockify_employee_events_for_day(
        api_key=cfg["api_key"],
        employee_names=employee_names,
        user_timezone=tz.zone,
        target_date=target,
        now_override=now_utc,
        base_url=cfg["base_url"],
        workspace_id=cfg["workspace_id"] or None,
    )
    _cache_set(key, events)
    return events


# ---------------------------------------------------------------------------
# Tool 1 — get_now
# ---------------------------------------------------------------------------

@mcp.tool()
@_logged_tool
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
@_logged_tool
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
@_logged_tool
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


@mcp.tool()
@_logged_tool
def get_clockify_tasks(
    date_str: Optional[str] = None,
    override_now: Optional[str] = None,
) -> dict:
    """
    Return Clockify tasks (time entries) for a given day.

    Args:
        date_str:     ISO date "YYYY-MM-DD". Defaults to today.
        override_now: Optional ISO datetime to use as "now".
    """
    tz = _resolve_tz()
    now_utc = _resolve_now(override_now)
    target = _resolve_date(date_str, tz, now_utc)
    events = _fetch_clockify_events(target, tz, now_utc)

    window_start = tz.localize(datetime(target.year, target.month, target.day))
    window_end = tz.normalize(window_start + timedelta(days=1))

    return {
        "source": "clockify",
        "date": target.isoformat(),
        "tz": tz.zone,
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
        },
        "count": len(events),
        "tasks": [_fmt(e, tz) for e in events],
    }


@mcp.tool()
@_logged_tool
def get_clockify_free_slots(
    date_str: Optional[str] = None,
    override_now: Optional[str] = None,
) -> dict:
    """
    Return free slots computed from Clockify tasks for a given day.

    Args:
        date_str:     ISO date "YYYY-MM-DD". Defaults to today.
        override_now: Optional ISO datetime to use as "now".
    """
    tz = _resolve_tz()
    now_utc = _resolve_now(override_now)
    target = _resolve_date(date_str, tz, now_utc)
    slots = _fetch_clockify_free_slots(target, tz, now_utc)

    output_slots: list[dict[str, Any]] = []
    for slot in slots:
        start = datetime.fromisoformat(slot["start_iso"]).astimezone(tz)
        end = datetime.fromisoformat(slot["end_iso"]).astimezone(tz)
        output_slots.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "duration_min": slot["duration_min"],
            }
        )

    return {
        "source": "clockify",
        "date": target.isoformat(),
        "tz": tz.zone,
        "count": len(output_slots),
        "freeSlots": output_slots,
        "totalFreeMin": sum(s["duration_min"] for s in output_slots),
    }


@mcp.tool()
@_logged_tool
def get_clockify_employee_tasks(
    date_str: Optional[str] = None,
    override_now: Optional[str] = None,
    employees_file: Optional[str] = None,
) -> dict:
    """
    Return Clockify tasks for employees from JSON file for a given day.

    Args:
        date_str:       ISO date "YYYY-MM-DD". Defaults to today.
        override_now:   Optional ISO datetime to use as "now".
        employees_file: Optional path to employees JSON file.
    """
    tz = _resolve_tz()
    now_utc = _resolve_now(override_now)
    target = _resolve_date(date_str, tz, now_utc)
    events = _fetch_clockify_employee_events(target, tz, now_utc, employees_file=employees_file)

    window_start = tz.localize(datetime(target.year, target.month, target.day))
    window_end = tz.normalize(window_start + timedelta(days=1))

    tasks: list[dict[str, Any]] = []
    for event in events:
        task = _fmt(event, tz)
        task["employeeName"] = event.get("employee_name")
        task["projectName"] = event.get("project_name")
        tasks.append(task)

    return {
        "source": "clockify",
        "date": target.isoformat(),
        "tz": tz.zone,
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
        },
        "count": len(tasks),
        "tasks": tasks,
    }


@mcp.tool()
@_logged_tool
def get_server_overview() -> dict:
    """
    Return server purpose, day-based workflow, and tool/parameter reference.
    """
    return {
        "name": "calendar",
        "purpose": (
            "Server is designed to read calendar events from ICS sources and "
            "occupied time in Clockify."
        ),
        "primaryWorkflow": (
            "Work with one target day. Choose date_str (YYYY-MM-DD) and call "
            "the corresponding day-based tools."
        ),
        "dayBased": True,
        "tools": [
            {
                "name": "get_server_overview",
                "description": "Return this reference: purpose, workflows, tools, parameters.",
                "params": [],
            },
            {
                "name": "get_now",
                "description": "Current/next status for now based on ICS events for today's date.",
                "params": [
                    {"name": "override_now", "type": "string|null", "required": False, "format": "ISO 8601 datetime"},
                ],
            },
            {
                "name": "get_day",
                "description": "All ICS events for a specific day.",
                "params": [
                    {"name": "date_str", "type": "string|null", "required": False, "format": "YYYY-MM-DD"},
                    {"name": "override_now", "type": "string|null", "required": False, "format": "ISO 8601 datetime"},
                ],
            },
            {
                "name": "get_free_slots",
                "description": "Free slots for a specific day from ICS events.",
                "params": [
                    {"name": "date_str", "type": "string|null", "required": False, "format": "YYYY-MM-DD"},
                    {"name": "min_duration", "type": "int", "required": False, "default": 30},
                    {"name": "day_start", "type": "string", "required": False, "default": "09:00"},
                    {"name": "day_end", "type": "string", "required": False, "default": "18:00"},
                    {"name": "override_now", "type": "string|null", "required": False, "format": "ISO 8601 datetime"},
                ],
            },
            {
                "name": "get_clockify_tasks",
                "description": "Clockify occupied entries for a specific day.",
                "params": [
                    {"name": "date_str", "type": "string|null", "required": False, "format": "YYYY-MM-DD"},
                    {"name": "override_now", "type": "string|null", "required": False, "format": "ISO 8601 datetime"},
                ],
                "envRequired": ["CLOCKIFY_API_KEY"],
            },
            {
                "name": "get_clockify_free_slots",
                "description": "Free slots for a specific day based on Clockify occupied entries.",
                "params": [
                    {"name": "date_str", "type": "string|null", "required": False, "format": "YYYY-MM-DD"},
                    {"name": "override_now", "type": "string|null", "required": False, "format": "ISO 8601 datetime"},
                ],
                "envRequired": ["CLOCKIFY_API_KEY"],
            },
            {
                "name": "get_clockify_employee_tasks",
                "description": "Clockify employee tasks for a specific day from employees JSON file.",
                "params": [
                    {"name": "date_str", "type": "string|null", "required": False, "format": "YYYY-MM-DD"},
                    {"name": "override_now", "type": "string|null", "required": False, "format": "ISO 8601 datetime"},
                    {"name": "employees_file", "type": "string|null", "required": False},
                ],
                "envRequired": ["CLOCKIFY_API_KEY"],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
