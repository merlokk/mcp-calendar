"""
AWS Lambda — calendar events handler
=====================================
Environment variables
---------------------
ICS_URLS               (required) space-separated list of public .ics URLs
TZ                     (optional) IANA timezone, default "Europe/Nicosia"
DEFAULT_DURATION_MIN   (optional) fallback event duration in minutes, default 60
CACHE_MS               (optional) warm-container in-memory cache TTL in ms, default 60000
OVERRIDE_NOW           (optional) ISO datetime for testing, e.g. "2026-02-09T08:00:00Z"

Query-string / event body parameters (override env vars per-request)
---------------------------------------------------------------------
ics_urls               same as ICS_URLS env var
tz                     same as TZ env var
override_now           same as OVERRIDE_NOW env var
mode                   "summary" (default) | "full"

Response shapes
---------------
mode=summary  →  { generatedAt, window, now, minutesUntilNext,
                   isOverlappingNow, current, next, nextOverlapping }
                   where summary event blocks include:
                   { uid, title, location, organizer, start, end, calendarId }
mode=full     →  { generatedAt, window, now, events: [...] }
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, date, timezone, timedelta
from typing import Any, Optional

import pytz

# ---------------------------------------------------------------------------
# Import our library — works both as `icscal` package and direct sibling
# ---------------------------------------------------------------------------
try:
    from icscal.calendar_loader import get_events_for_day
    from icscal.windows_zones import configure as _wz_configure
except ImportError:
    from calendar_loader import get_events_for_day          # type: ignore
    from windows_zones import configure as _wz_configure    # type: ignore

# ---------------------------------------------------------------------------
# Module-level setup (runs once per cold start)
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Use memory-only cache for Lambda (stateless, no filesystem write needed)
_wz_configure(file_cache=False)

# ---------------------------------------------------------------------------
# Warm-container event cache
# ---------------------------------------------------------------------------
_cache: dict[str, Any] = {}          # key → {"ts_ms": int, "data": list}
_CACHE_TTL_MS_DEFAULT = 60_000       # 1 minute


def _cache_get(key: str, ttl_ms: int) -> Optional[list]:
    entry = _cache.get(key)
    if entry and (time.time() * 1000 - entry["ts_ms"]) < ttl_ms:
        return entry["data"]
    return None


def _cache_set(key: str, data: list) -> None:
    _cache[key] = {"ts_ms": int(time.time() * 1000), "data": data}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_now(raw: Optional[str]) -> Optional[datetime]:
    """Parse OVERRIDE_NOW / override_now into an aware datetime."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        logger.warning("Cannot parse OVERRIDE_NOW %r — ignoring", raw)
        return None


def _event_to_summary(ev: dict, tz: pytz.BaseTzInfo) -> dict:
    """Convert a library event dict to the slim summary shape."""
    start_local = datetime.fromisoformat(ev["start_iso"]).astimezone(tz)
    end_local   = datetime.fromisoformat(ev["end_iso"]).astimezone(tz)
    return {
        "uid":       ev["uid"],
        "title":     ev["summary"],
        "location":  ev.get("location"),
        "organizer": ev.get("organizer"),
        "start":     start_local.isoformat(),
        "end":       end_local.isoformat(),
        "calendarId": ev.get("calendar_id"),
    }


def _minutes_until(now_utc: datetime, event: Optional[dict]) -> Optional[int]:
    if event is None:
        return None
    start_utc = datetime.fromisoformat(event["start_iso"])
    delta = start_utc - now_utc
    return max(0, int(delta.total_seconds() / 60))


def _build_response(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handler(event: dict, context: Any) -> dict:
    # ------------------------------------------------------------------ #
    # 1. Read parameters: env vars → query string → body (priority order) #
    # ------------------------------------------------------------------ #
    params: dict[str, str] = {}

    # Environment variables (lowest priority)
    params["ics_urls"]    = os.environ.get("ICS_URLS", "")
    params["tz"]          = os.environ.get("TZ", "Europe/Nicosia")
    params["cache_ms"]    = os.environ.get("CACHE_MS", str(_CACHE_TTL_MS_DEFAULT))
    params["override_now"]= os.environ.get("OVERRIDE_NOW", "")
    params["mode"]        = "summary"

    # Query-string parameters
    qs = (event or {}).get("queryStringParameters") or {}
    for key in ("ics_urls", "tz", "override_now", "mode"):
        if key in qs:
            params[key] = qs[key]

    # JSON body (highest priority for ics_urls / tz / override_now)
    body_raw = (event or {}).get("body") or ""
    if body_raw:
        try:
            body_json = json.loads(body_raw)
            for key in ("ics_urls", "tz", "override_now", "mode"):
                if key in body_json:
                    params[key] = str(body_json[key])
        except (json.JSONDecodeError, TypeError):
            pass

    # ------------------------------------------------------------------ #
    # 2. Validate                                                          #
    # ------------------------------------------------------------------ #
    ics_urls_raw = params["ics_urls"].strip()
    if not ics_urls_raw:
        return _build_response(400, {"error": "ICS_URLS is required"})

    ics_urls = ics_urls_raw.split()

    tz_name = params["tz"].strip() or "Europe/Nicosia"
    try:
        tz = pytz.timezone(tz_name)
    except pytz.exceptions.UnknownTimeZoneError:
        return _build_response(400, {"error": f"Unknown timezone: {tz_name!r}"})

    try:
        cache_ms = int(params["cache_ms"])
    except (ValueError, TypeError):
        cache_ms = _CACHE_TTL_MS_DEFAULT

    now_override = _parse_now(params.get("override_now"))
    mode = params["mode"].strip().lower()
    if mode not in ("summary", "full"):
        mode = "summary"

    # ------------------------------------------------------------------ #
    # 3. Resolve "now"                                                     #
    # ------------------------------------------------------------------ #
    now_utc: datetime = (
        now_override.astimezone(timezone.utc)
        if now_override
        else datetime.now(timezone.utc)
    )
    now_local = now_utc.astimezone(tz)

    # ------------------------------------------------------------------ #
    # 4. Fetch events (with warm cache)                                    #
    # ------------------------------------------------------------------ #
    cache_key = f"{','.join(sorted(ics_urls))}|{tz_name}|{now_local.date().isoformat()}"
    events = _cache_get(cache_key, cache_ms)

    if events is None:
        try:
            events = get_events_for_day(
                calendar_urls=ics_urls,
                user_timezone=tz_name,
                target_date=now_local,   # datetime → also sets "now" inside the lib
                now_override=now_override,
            )
        except Exception as exc:
            logger.exception("get_events_for_day failed: %s", exc)
            return _build_response(502, {"error": "Failed to fetch calendar data", "detail": str(exc)})
        _cache_set(cache_key, events)

    # ------------------------------------------------------------------ #
    # 5. Build window metadata                                             #
    # ------------------------------------------------------------------ #
    today_local  = now_local.date()
    window_start = tz.localize(datetime(today_local.year, today_local.month, today_local.day))
    window_end   = tz.normalize(window_start + timedelta(days=1))

    generated_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    window_meta = {
        "start": window_start.isoformat(),
        "end":   window_end.isoformat(),
        "tz":    tz_name,
    }

    # ------------------------------------------------------------------ #
    # 6. Build response body                                               #
    # ------------------------------------------------------------------ #
    if mode == "full":
        body = {
            "generatedAt": generated_at,
            "window":      window_meta,
            "now":         now_local.isoformat(),
            "events":      events,
        }
        return _build_response(200, body)

    # --- mode == "summary" ---
    current_ev   = next((e for e in events if e["is_current"]),           None)
    next_ev      = next((e for e in events if e["is_next"]),              None)
    next_ov_ev   = next((e for e in events if e["is_next_overlapping"]),  None)

    minutes_until_next = _minutes_until(now_utc, next_ev)

    body = {
        "generatedAt":      generated_at,
        "window":           window_meta,
        "now":              now_local.isoformat(),
        "minutesUntilNext": minutes_until_next,
        "isOverlappingNow": next_ov_ev is not None,
        "current":          _event_to_summary(current_ev, tz) if current_ev else None,
        "next":             _event_to_summary(next_ev,    tz) if next_ev    else None,
        "nextOverlapping":  _event_to_summary(next_ov_ev, tz) if next_ov_ev else None,
    }
    return _build_response(200, body)


# AWS Lambda default handler alias for configurations using
# "lambda_function.lambda_handler".
lambda_handler = handler
