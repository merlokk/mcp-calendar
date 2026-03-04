"""
calendar_loader.py
------------------
Load multiple ICS calendars from URLs and return a JSON array of events for a given day.
Handles Google Calendar and Outlook/Exchange edge-cases.

Dependencies: icalendar, recurring-ical-events, pytz
"""

from __future__ import annotations

import logging
import re
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Optional

import pytz
import recurring_ical_events
from icalendar import Calendar, Event

try:
    from .windows_zones import windows_to_iana as _win_to_iana
except ImportError:
    from windows_zones import windows_to_iana as _win_to_iana  # type: ignore[no-redef]

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Helpers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_user_tz(user_timezone: str) -> Any:
    """Resolve user timezone string (IANA or Windows name) to pytz tzinfo."""
    try:
        return pytz.timezone(user_timezone)
    except pytz.exceptions.UnknownTimeZoneError:
        pass
    iana = _win_to_iana(user_timezone)
    if iana:
        try:
            return pytz.timezone(iana)
        except pytz.exceptions.UnknownTimeZoneError:
            pass
    logger.warning("Unknown user timezone %r – falling back to UTC", user_timezone)
    return pytz.utc


def _extract_vtimezone_tzids_from_raw(raw: bytes) -> set[str]:
    """
    Extract TZID values that have VTIMEZONE definitions in the raw ICS bytes.
    These must NOT be remapped — the calendar already provides their DST rules.
    """
    tzids: set[str] = set()
    for m in re.finditer(rb"BEGIN:VTIMEZONE.*?TZID:([^\r\n]+)", raw, re.DOTALL):
        tzids.add(m.group(1).decode("utf-8", errors="replace").strip())
    return tzids


def _normalize_windows_tzids(raw: bytes) -> bytes:
    """
    Pre-process raw ICS bytes: replace Windows timezone names in TZID parameters
    with their IANA equivalents, but only for TZIDs that do NOT have a matching
    VTIMEZONE block (those are handled by icalendar natively).

    Exchange / Outlook produces lines like:
        DTSTART;TZID=Eastern Standard Time:20260209T100000
    which icalendar cannot parse without a VTIMEZONE definition.
    """
    # Find TZIDs that already have VTIMEZONE definitions → leave them alone
    protected = _extract_vtimezone_tzids_from_raw(raw)

    def replace_tzid(m: re.Match) -> bytes:
        tzid = m.group(1).decode("utf-8", errors="replace")
        if tzid in protected:
            return m.group(0)  # has VTIMEZONE — keep as-is
        iana = _win_to_iana(tzid)
        if iana and iana != tzid:
            logger.debug("Remapping TZID %r → %r", tzid, iana)
            return m.group(0).replace(m.group(1), iana.encode())
        return m.group(0)

    # Match TZID= in property parameters:  ;TZID=Some Name:  or  TZID=Some Name:
    return re.sub(rb"(?:;|^)TZID=([^:;\r\n]+)", replace_tzid, raw, flags=re.MULTILINE)


def _dt_to_utc(dt: Any) -> Optional[datetime]:
    """
    Convert an icalendar date/datetime to an aware UTC datetime.
    Returns None for date-only (all-day) values.
    """
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=pytz.utc)
        return dt.astimezone(pytz.utc)
    # Plain date → all-day
    return None


def _get_dt_utc(component: Event, prop: str) -> Optional[datetime]:
    val = component.get(prop)
    if val is None:
        return None
    raw = val.dt if hasattr(val, "dt") else val
    return _dt_to_utc(raw)


def _is_allday(component: Event) -> bool:
    val = component.get("DTSTART")
    if val is None:
        return False
    raw = val.dt if hasattr(val, "dt") else val
    return isinstance(raw, date) and not isinstance(raw, datetime)


def _should_skip(component: Event) -> bool:
    if _is_allday(component):
        return True
    status = str(component.get("STATUS", "")).upper()
    if status == "CANCELLED":
        return True
    summary = str(component.get("SUMMARY", ""))
    if summary.startswith("Canceled:"):
        return True
    uid = str(component.get("UID", "")).strip()
    if not uid:
        logger.warning("Event without UID – skipping")
        return True
    if component.get("DTSTART") is None:
        logger.warning("Event %r without DTSTART – skipping", uid)
        return True
    return False


def _event_end(component: Event, start_utc: datetime) -> datetime:
    # Check DTEND only if property explicitly exists
    if component.get("DTEND") is not None:
        end = _get_dt_utc(component, "DTEND")
        if end is not None:
            # Explicit DTSTART==DTEND is a zero-duration marker → treat as 1 minute
            if end <= start_utc:
                return start_utc + timedelta(minutes=1)
            return end
    dur_prop = component.get("DURATION")
    if dur_prop is not None:
        dur = dur_prop.dt if hasattr(dur_prop, "dt") else dur_prop
        result = start_utc + dur
        if result <= start_utc:
            return start_utc + timedelta(minutes=1)
        return result
    # No DTEND and no DURATION → default 60 minutes
    return start_utc + timedelta(hours=1)


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------

def fetch_ics(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "CalendarLoader/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def get_events_for_day(
    calendar_urls: list[str],
    user_timezone: str = "UTC",
    target_date: Optional[date] = None,
    ics_contents: Optional[list[bytes]] = None,
    now_override: Optional[datetime] = None,
) -> list[dict]:
    """
    Load calendars and return a JSON array of events overlapping target_date
    in user_timezone.

    Parameters
    ----------
    calendar_urls  : Ordered list of ICS URLs. Lower index = higher UID priority.
    user_timezone  : IANA or Windows timezone name for the user's local day.
    target_date    : Local date to fetch. Defaults to today in user_timezone.
    ics_contents   : Supply raw ICS bytes directly (bypasses network; for tests).
    now_override   : Override "now" moment (for tests).

    Returns
    -------
    list[dict]: events sorted by start_ms.
    """
    tz = _resolve_user_tz(user_timezone)

    # Resolve now_utc:
    # 1. Explicit now_override always wins.
    # 2. If target_date is a datetime (not just date), use it as now too.
    # 3. Otherwise use real datetime.now().
    if now_override is not None:
        now_utc: datetime = now_override
        if now_utc.tzinfo is None:
            now_utc = pytz.utc.localize(now_utc)
        now_utc = now_utc.astimezone(pytz.utc)
    elif isinstance(target_date, datetime):
        # caller passed a datetime as target_date — use it as "now" as well
        now_utc = target_date if target_date.tzinfo else pytz.utc.localize(target_date)
        now_utc = now_utc.astimezone(pytz.utc)
    else:
        now_utc = datetime.now(pytz.utc)

    local_now = now_utc.astimezone(tz)

    if target_date is None:
        target_date = local_now.date()
    # datetime is a subclass of date — extract just the date part for window calculation
    if isinstance(target_date, datetime):
        target_date = target_date.astimezone(tz).date()

    # CRITICAL: window = [midnight today, midnight tomorrow) in user tz
    # Use normalize() after timedelta to handle DST transitions correctly
    midnight_today = tz.localize(
        datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
    )
    midnight_tomorrow = tz.normalize(midnight_today + timedelta(days=1))
    window_start: datetime = midnight_today.astimezone(pytz.utc)
    window_end: datetime = midnight_tomorrow.astimezone(pytz.utc)

    # recurring_ical_events.between() uses INCLUSIVE bounds [start, end].
    # We need EXCLUSIVE end: pass window_end - 1ms so events starting exactly
    # at midnight tomorrow are NOT included.
    rie_window_end = window_end - timedelta(milliseconds=1)

    # uid → winning calendar_index (lower index = higher priority)
    uid_winner: dict[str, int] = {}
    collected: list[dict] = []

    for cal_idx, url in enumerate(calendar_urls):
        if ics_contents is not None:
            raw = ics_contents[cal_idx]
        else:
            try:
                raw = fetch_ics(url)
            except Exception as exc:
                logger.error("Failed to fetch %r: %s", url, exc)
                continue

        # Normalize Windows TZID names → IANA before parsing.
        # Must happen on raw bytes — icalendar cannot handle unknown TZID values.
        raw = _normalize_windows_tzids(raw)

        try:
            cal = Calendar.from_ical(raw)
        except Exception as exc:
            logger.error("Failed to parse ICS from %r: %s", url, exc)
            continue

        # Collect master durations for CRITICAL recurring duration fix
        master_duration: dict[str, timedelta] = {}
        for component in cal.walk():
            if component.name != "VEVENT":
                continue
            if not component.get("RRULE"):
                continue
            uid = str(component.get("UID", "")).strip()
            if not uid:
                continue
            s = _get_dt_utc(component, "DTSTART")
            e = _get_dt_utc(component, "DTEND")
            if s is not None and e is not None:
                master_duration[uid] = e - s
            elif s is not None:
                dur_prop = component.get("DURATION")
                if dur_prop is not None:
                    master_duration[uid] = dur_prop.dt if hasattr(dur_prop, "dt") else dur_prop
                else:
                    master_duration[uid] = timedelta(hours=1)

        # CRITICAL: expand from windowStart, NOT from NOW.
        # Use rie_window_end (window_end - 1ms) because between() is inclusive.
        try:
            expanded = recurring_ical_events.of(cal).between(
                window_start,
                rie_window_end,
            )
        except Exception as exc:
            logger.error("recurring_ical_events failed for %r: %s", url, exc)
            expanded = []

        for component in expanded:
            if component.name != "VEVENT":
                continue
            if _should_skip(component):
                continue

            uid = str(component.get("UID", "")).strip()
            start = _get_dt_utc(component, "DTSTART")
            if start is None:
                continue

            # CRITICAL: apply master duration to each occurrence
            if uid in master_duration:
                end = start + master_duration[uid]
            else:
                end = _event_end(component, start)

            # recurring_ical_events synthesises DTEND=DTSTART when DTEND was absent.
            # In this case end==start (or came back as start+1min from _event_end).
            # Apply the proper 60-min default for missing-DTEND events.
            raw_dtend = _get_dt_utc(component, "DTEND")
            if raw_dtend is not None and raw_dtend <= start:
                # synthetic zero — treat as 60-min default (not a 1-min marker)
                end = start + timedelta(hours=1)
            elif end <= start:
                end = start + timedelta(hours=1)

            # Overlap: event overlaps [window_start, window_end)
            if not (start < window_end and end > window_start):
                continue

            # UID priority: first calendar wins
            if uid in uid_winner and uid_winner[uid] < cal_idx:
                continue
            if uid not in uid_winner:
                uid_winner[uid] = cal_idx

            collected.append({
                "uid": uid,
                "summary": str(component.get("SUMMARY", "")),
                "start": start,
                "end": end,
                "start_ms": int(start.timestamp() * 1000),
                "end_ms": int(end.timestamp() * 1000),
                "calendar_index": cal_idx,
                "calendar_url": url,
            })

        # Orphaned overrides: RECURRENCE-ID events whose master is NOT in this calendar
        master_uids_in_cal: set[str] = set()
        for component in cal.walk():
            if component.name == "VEVENT" and component.get("RRULE"):
                uid = str(component.get("UID", "")).strip()
                if uid:
                    master_uids_in_cal.add(uid)

        for component in cal.walk():
            if component.name != "VEVENT":
                continue
            if not component.get("RECURRENCE-ID"):
                continue
            if _should_skip(component):
                continue
            uid = str(component.get("UID", "")).strip()
            if uid in master_uids_in_cal:
                continue  # already handled by recurring_ical_events

            start = _get_dt_utc(component, "DTSTART")
            if start is None:
                continue
            end = _event_end(component, start)

            if not (start < window_end and end > window_start):
                continue

            if uid in uid_winner and uid_winner[uid] < cal_idx:
                continue
            if uid not in uid_winner:
                uid_winner[uid] = cal_idx

            collected.append({
                "uid": uid,
                "summary": str(component.get("SUMMARY", "")),
                "start": start,
                "end": end,
                "start_ms": int(start.timestamp() * 1000),
                "end_ms": int(end.timestamp() * 1000),
                "calendar_index": cal_idx,
                "calendar_url": url,
            })

    # Dedup: keep only events from the winning calendar per uid
    collected.sort(key=lambda e: (e["uid"], e["start_ms"], e["calendar_index"]))
    seen_keys: set[str] = set()
    deduped: list[dict] = []
    window_start_ms = int(window_start.timestamp() * 1000)
    window_end_ms = int(window_end.timestamp() * 1000)
    for ev in collected:
        if uid_winner.get(ev["uid"]) != ev["calendar_index"]:
            continue
        key = f"{ev['uid']}|{ev['start_ms']}"
        if key in seen_keys:
            continue
        # Final strict overlap guard: event must overlap [window_start, window_end)
        # i.e. start < window_end  AND  end > window_start
        if not (ev["start_ms"] < window_end_ms and ev["end_ms"] > window_start_ms):
            continue
        seen_keys.add(key)
        deduped.append(ev)

    deduped.sort(key=lambda e: e["start_ms"])

    # ---------------------------------------------------------------------------
    # Current / Next logic
    # All comparisons use now_utc (from now_override or datetime.now()).
    # target_date only controls the window; "now" controls the flags.
    # ---------------------------------------------------------------------------
    now_ms = int(now_utc.timestamp() * 1000)

    # is_current: event that is happening right now (start <= now < end)
    current_idx: Optional[int] = None
    for i, ev in enumerate(deduped):
        if ev["start_ms"] <= now_ms < ev["end_ms"]:
            current_idx = i
            break

    # is_next: first event that starts >= current.end (or >= now if no current),
    #          i.e. does NOT overlap with current
    search_from_ms = deduped[current_idx]["end_ms"] if current_idx is not None else now_ms
    next_idx: Optional[int] = None
    for i, ev in enumerate(deduped):
        if i == current_idx:
            continue
        if ev["start_ms"] >= search_from_ms:
            next_idx = i
            break

    # is_next_overlapping: first event that starts AFTER current starts
    #                      AND overlaps with current (start < current.end)
    #                      i.e. it begins during current and runs concurrently
    next_overlapping_idx: Optional[int] = None
    if current_idx is not None:
        current_ev = deduped[current_idx]
        for i, ev in enumerate(deduped):
            if i == current_idx:
                continue
            # starts after current started, but before current ends → overlapping
            if ev["start_ms"] > current_ev["start_ms"] and ev["start_ms"] < current_ev["end_ms"]:
                next_overlapping_idx = i
                break

    # Build output
    output = []
    for i, ev in enumerate(deduped):
        output.append({
            "uid": ev["uid"],
            "summary": ev["summary"],
            "start_iso": ev["start"].isoformat(),
            "end_iso": ev["end"].isoformat(),
            "start_ms": ev["start_ms"],
            "end_ms": ev["end_ms"],
            "calendar_id": ev["calendar_index"],
            "calendar_url": ev["calendar_url"],
            "is_current": i == current_idx,
            "is_next": i == next_idx,
            "is_next_overlapping": i == next_overlapping_idx,
        })

    return output