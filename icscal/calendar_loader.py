"""
calendar_loader.py
------------------
Load multiple ICS calendars from URLs and return a JSON array of events for a given day.
Handles Google Calendar and Outlook/Exchange edge-cases.

Dependencies: icalendar, recurring-ical-events, pytz
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Optional

import pytz
import recurring_ical_events
from icalendar import Calendar, Event

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Windows → IANA timezone mapping  (Exchange / Outlook)
# ---------------------------------------------------------------------------
WINDOWS_TO_IANA: dict[str, str] = {
    "Afghanistan Standard Time": "Asia/Kabul",
    "Alaskan Standard Time": "America/Anchorage",
    "Arab Standard Time": "Asia/Riyadh",
    "Arabian Standard Time": "Asia/Dubai",
    "Arabic Standard Time": "Asia/Baghdad",
    "Argentina Standard Time": "America/Argentina/Buenos_Aires",
    "Atlantic Standard Time": "America/Halifax",
    "AUS Central Standard Time": "Australia/Darwin",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "Azerbaijan Standard Time": "Asia/Baku",
    "Azores Standard Time": "Atlantic/Azores",
    "Canada Central Standard Time": "America/Regina",
    "Cape Verde Standard Time": "Atlantic/Cape_Verde",
    "Caucasus Standard Time": "Asia/Yerevan",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "Central America Standard Time": "America/Guatemala",
    "Central Asia Standard Time": "Asia/Almaty",
    "Central Brazilian Standard Time": "America/Cuiaba",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "Central Pacific Standard Time": "Pacific/Guadalcanal",
    "Central Standard Time": "America/Chicago",
    "Central Standard Time (Mexico)": "America/Mexico_City",
    "China Standard Time": "Asia/Shanghai",
    "Dateline Standard Time": "Etc/GMT+12",
    "E. Africa Standard Time": "Africa/Nairobi",
    "E. Australia Standard Time": "Australia/Brisbane",
    "E. Europe Standard Time": "Asia/Nicosia",
    "E. South America Standard Time": "America/Sao_Paulo",
    "Eastern Standard Time": "America/New_York",
    "Eastern Standard Time (Mexico)": "America/Cancun",
    "Egypt Standard Time": "Africa/Cairo",
    "Ekaterinburg Standard Time": "Asia/Yekaterinburg",
    "Fiji Standard Time": "Pacific/Fiji",
    "FLE Standard Time": "Europe/Kiev",
    "Georgian Standard Time": "Asia/Tbilisi",
    "GMT Standard Time": "Europe/London",
    "Greenland Standard Time": "America/Godthab",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "GTB Standard Time": "Europe/Bucharest",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "India Standard Time": "Asia/Calcutta",
    "Iran Standard Time": "Asia/Tehran",
    "Israel Standard Time": "Asia/Jerusalem",
    "Jordan Standard Time": "Asia/Amman",
    "Korea Standard Time": "Asia/Seoul",
    "Mauritius Standard Time": "Indian/Mauritius",
    "Middle East Standard Time": "Asia/Beirut",
    "Montevideo Standard Time": "America/Montevideo",
    "Morocco Standard Time": "Africa/Casablanca",
    "Mountain Standard Time": "America/Denver",
    "Mountain Standard Time (Mexico)": "America/Chihuahua",
    "Myanmar Standard Time": "Asia/Rangoon",
    "N. Central Asia Standard Time": "Asia/Novosibirsk",
    "Namibia Standard Time": "Africa/Windhoek",
    "Nepal Standard Time": "Asia/Katmandu",
    "New Zealand Standard Time": "Pacific/Auckland",
    "Newfoundland Standard Time": "America/St_Johns",
    "North Asia East Standard Time": "Asia/Irkutsk",
    "North Asia Standard Time": "Asia/Krasnoyarsk",
    "Pacific SA Standard Time": "America/Santiago",
    "Pacific Standard Time": "America/Los_Angeles",
    "Pacific Standard Time (Mexico)": "America/Santa_Isabel",
    "Romance Standard Time": "Europe/Paris",
    "Russia Time Zone 11": "Asia/Kamchatka",
    "Russia Time Zone 3": "Europe/Samara",
    "Russia Time Zone 9": "Asia/Yakutsk",
    "Russian Standard Time": "Europe/Moscow",
    "SA Eastern Standard Time": "America/Cayenne",
    "SA Pacific Standard Time": "America/Bogota",
    "SA Western Standard Time": "America/La_Paz",
    "SE Asia Standard Time": "Asia/Bangkok",
    "Singapore Standard Time": "Asia/Singapore",
    "South Africa Standard Time": "Africa/Johannesburg",
    "Sri Lanka Standard Time": "Asia/Colombo",
    "Syria Standard Time": "Asia/Damascus",
    "Taipei Standard Time": "Asia/Taipei",
    "Tasmania Standard Time": "Australia/Hobart",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Tonga Standard Time": "Pacific/Tongatapu",
    "Turkey Standard Time": "Europe/Istanbul",
    "US Eastern Standard Time": "America/Indianapolis",
    "US Mountain Standard Time": "America/Phoenix",
    "UTC": "UTC",
    "UTC+12": "Etc/GMT-12",
    "UTC-02": "Etc/GMT+2",
    "UTC-11": "Etc/GMT+11",
    "Venezuela Standard Time": "America/Caracas",
    "Vladivostok Standard Time": "Asia/Vladivostok",
    "W. Australia Standard Time": "Australia/Perth",
    "W. Central Africa Standard Time": "Africa/Lagos",
    "W. Europe Standard Time": "Europe/Berlin",
    "West Asia Standard Time": "Asia/Tashkent",
    "West Pacific Standard Time": "Pacific/Port_Moresby",
    "Yakutsk Standard Time": "Asia/Yakutsk",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_user_tz(user_timezone: str) -> Any:
    """Resolve user timezone string (IANA or Windows name) to pytz tzinfo."""
    try:
        return pytz.timezone(user_timezone)
    except pytz.exceptions.UnknownTimeZoneError:
        pass
    iana = WINDOWS_TO_IANA.get(user_timezone)
    if iana:
        try:
            return pytz.timezone(iana)
        except pytz.exceptions.UnknownTimeZoneError:
            pass
    logger.warning("Unknown user timezone %r – falling back to UTC", user_timezone)
    return pytz.utc


def _extract_vtimezone_tzids(cal: Calendar) -> set[str]:
    """Return TZID values that have VTIMEZONE definitions in the calendar."""
    tzids: set[str] = set()
    for component in cal.walk():
        if component.name == "VTIMEZONE":
            tzid = str(component.get("TZID", "")).strip()
            if tzid:
                tzids.add(tzid)
    return tzids


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
) -> str:
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
    JSON string: list of event dicts sorted by start_ms.
    """
    tz = _resolve_user_tz(user_timezone)
    now_utc: datetime = now_override or datetime.now(pytz.utc)
    local_now = now_utc.astimezone(tz)

    if target_date is None:
        target_date = local_now.date()

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

        try:
            cal = Calendar.from_ical(raw)
        except Exception as exc:
            logger.error("Failed to parse ICS from %r: %s", url, exc)
            continue

        # CRITICAL: scan VTIMEZONE blocks first — do NOT remap tzids that have definitions
        _extract_vtimezone_tzids(cal)

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

    # Current / Next logic
    now_ms = int(now_utc.timestamp() * 1000)

    current_idx: Optional[int] = None
    for i, ev in enumerate(deduped):
        if ev["start_ms"] <= now_ms < ev["end_ms"]:
            current_idx = i
            break

    # CRITICAL: next starts at current.end, not at NOW
    search_from_ms = deduped[current_idx]["end_ms"] if current_idx is not None else now_ms
    next_idx: Optional[int] = None
    for i, ev in enumerate(deduped):
        if current_idx is not None and i == current_idx:
            continue
        if ev["start_ms"] >= search_from_ms:
            next_idx = i
            break

    # next_overlapping / next_non_overlapping
    next_overlapping_idx: Optional[int] = None
    next_non_overlapping_idx: Optional[int] = None

    if next_idx is not None:
        next_ev = deduped[next_idx]

        for i in range(next_idx + 1, len(deduped)):
            ev = deduped[i]
            if ev["start_ms"] >= next_ev["end_ms"]:
                break
            if ev["end_ms"] > next_ev["start_ms"]:
                next_overlapping_idx = i
                break

        cluster_end_ms = next_ev["end_ms"]
        for ev in deduped[next_idx + 1:]:
            if ev["start_ms"] >= cluster_end_ms:
                break
            if ev["end_ms"] > cluster_end_ms:
                cluster_end_ms = ev["end_ms"]

        for i in range(next_idx + 1, len(deduped)):
            if deduped[i]["start_ms"] >= cluster_end_ms:
                next_non_overlapping_idx = i
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
            "calendar_url": ev["calendar_url"],
            "is_current": i == current_idx,
            "is_next": i == next_idx,
            "is_next_overlapping": i == next_overlapping_idx,
            "is_next_non_overlapping": i == next_non_overlapping_idx,
        })

    return json.dumps(output, ensure_ascii=False, indent=2)
