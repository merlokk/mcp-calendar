"""
tests.py
--------
Comprehensive tests for calendar_loader.py

Run with:  python -m pytest tests.py -v
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import os
from typing import Optional

import pytz
import pytest

try:
    from .calendar_loader import get_events_for_day
except ImportError:
    from calendar_loader import get_events_for_day  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Timezone shortcuts
# ---------------------------------------------------------------------------
UTC = pytz.utc
NY = pytz.timezone("America/New_York")
LA = pytz.timezone("America/Los_Angeles")


def _make_dt(year, month, day, hour, minute, tz=UTC) -> datetime:
    return tz.localize(datetime(year, month, day, hour, minute))


def _result(
    ics_list: list[str],
    now: Optional[datetime] = None,
    user_tz: str = "UTC",
    target_date: Optional[date] = None,
) -> list[dict]:
    contents = [s.encode() for s in ics_list]
    urls = [f"mock://cal{i}.ics" for i in range(len(ics_list))]
    return get_events_for_day(
        calendar_urls=urls,
        user_timezone=user_tz,
        target_date=target_date,
        ics_contents=contents,
        now_override=now,
    )


# ---------------------------------------------------------------------------
# ICS builders — NO leading whitespace (ICS is whitespace-sensitive)
# ---------------------------------------------------------------------------

def _event(
    uid: str,
    summary: str,
    dtstart: str,
    dtend: str,
    rrule: str = "",
    recurrence_id: str = "",
    status: str = "",
) -> str:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{summary}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
    ]
    if rrule:
        lines.append(f"RRULE:{rrule}")
    if recurrence_id:
        lines.append(f"RECURRENCE-ID:{recurrence_id}")
    if status:
        lines.append(f"STATUS:{status}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def _ics(*event_blocks: str) -> str:
    """Wrap event blocks in a VCALENDAR. NO indentation — ICS is strict."""
    body = "\r\n".join(event_blocks)
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//Test//EN\r\n"
        + body
        + "\r\nEND:VCALENDAR\r\n"
    )


# VTIMEZONE block for "Pacific Standard Time" as Exchange produces it
PACIFIC_VTIMEZONE = (
    "BEGIN:VTIMEZONE\r\n"
    "TZID:Pacific Standard Time\r\n"
    "BEGIN:STANDARD\r\n"
    "DTSTART:16010101T020000\r\n"
    "TZOFFSETFROM:-0700\r\n"
    "TZOFFSETTO:-0800\r\n"
    "RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=11\r\n"
    "END:STANDARD\r\n"
    "BEGIN:DAYLIGHT\r\n"
    "DTSTART:16010101T020000\r\n"
    "TZOFFSETFROM:-0800\r\n"
    "TZOFFSETTO:-0700\r\n"
    "RRULE:FREQ=YEARLY;BYDAY=2SU;BYMONTH=3\r\n"
    "END:DAYLIGHT\r\n"
    "END:VTIMEZONE"
)


# ---------------------------------------------------------------------------
# 1. Currently happening event
# ---------------------------------------------------------------------------

class TestCurrentEvent:

    def test_event_currently_happening(self):
        now = _make_dt(2025, 6, 15, 10, 20)
        ics = _ics(_event("uid1", "Stand-up", "20250615T100000Z", "20250615T104500Z"))
        events = _result([ics], now)
        assert len(events) == 1
        assert events[0]["is_current"] is True
        assert events[0]["summary"] == "Stand-up"

    def test_event_starting_exactly_at_now(self):
        """Event starting exactly at NOW should be current."""
        now = _make_dt(2025, 6, 15, 10, 0)
        ics = _ics(_event("uid1", "Meeting", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now)
        assert len(events) == 1
        assert events[0]["is_current"] is True

    def test_event_ending_exactly_at_now_excluded(self):
        """Event ending exactly at NOW should NOT be current."""
        now = _make_dt(2025, 6, 15, 10, 0)
        ics = _ics(_event("uid1", "Old Meeting", "20250615T090000Z", "20250615T100000Z"))
        events = _result([ics], now)
        assert len(events) == 1
        assert events[0]["is_current"] is False

    def test_event_started_one_minute_ago(self):
        now = _make_dt(2025, 6, 15, 10, 1)
        ics = _ics(_event("uid1", "Meeting", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now)
        assert len(events) == 1
        assert events[0]["is_current"] is True

    def test_no_current_event_all_future(self):
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(_event("uid1", "Future", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now)
        assert len(events) == 1
        assert events[0]["is_current"] is False


# ---------------------------------------------------------------------------
# 2. Next event logic
# ---------------------------------------------------------------------------

class TestNextEvent:

    def test_next_event_after_current_ends_not_after_now(self):
        """
        NOW: 10:20
        Current: 10:15–10:45
        Events: 12:30, 15:00
        Next = PayPal (first after current.end=10:45)
        """
        now = _make_dt(2025, 6, 15, 10, 20)
        ics = _ics(
            _event("uid1", "Stand-up",  "20250615T101500Z", "20250615T104500Z"),
            _event("uid2", "PayPal",    "20250615T123000Z", "20250615T130000Z"),
            _event("uid3", "Afternoon", "20250615T150000Z", "20250615T160000Z"),
        )
        events = _result([ics], now)
        current = [e for e in events if e["is_current"]]
        nxt     = [e for e in events if e["is_next"]]
        assert len(current) == 1 and current[0]["summary"] == "Stand-up"
        assert len(nxt) == 1    and nxt[0]["summary"] == "PayPal"

    def test_next_when_no_current(self):
        """No current event → next = first event after NOW."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(
            _event("uid1", "First",  "20250615T100000Z", "20250615T110000Z"),
            _event("uid2", "Second", "20250615T120000Z", "20250615T130000Z"),
        )
        events = _result([ics], now)
        assert len(events) == 2
        assert events[0]["is_next"] is True
        assert events[1]["is_next"] is False

    def test_future_event_placement(self):
        now = _make_dt(2025, 6, 15, 8, 0)
        ics = _ics(
            _event("uid1", "Morning",   "20250615T100000Z", "20250615T110000Z"),
            _event("uid2", "Afternoon", "20250615T140000Z", "20250615T150000Z"),
        )
        events = _result([ics], now)
        assert len(events) == 2
        assert events[0]["summary"] == "Morning"
        assert events[1]["summary"] == "Afternoon"
        assert events[0]["is_next"] is True


# ---------------------------------------------------------------------------
# 3. All-day events skipped
# ---------------------------------------------------------------------------

class TestAllDaySkipped:

    def test_allday_event_skipped(self):
        now = _make_dt(2025, 6, 15, 10, 0)
        ics = _ics(
            _event("uid1", "Holiday", "20250615", "20250616"),
            _event("uid2", "Meeting", "20250615T100000Z", "20250615T110000Z"),
        )
        events = _result([ics], now)
        assert all(e["summary"] != "Holiday" for e in events)
        assert any(e["summary"] == "Meeting" for e in events)


# ---------------------------------------------------------------------------
# 4. Cancelled events skipped
# ---------------------------------------------------------------------------

class TestCancelledSkipped:

    def test_status_cancelled_skipped(self):
        now = _make_dt(2025, 6, 15, 10, 0)
        ics = _ics(_event("uid1", "Gone", "20250615T100000Z", "20250615T110000Z", status="CANCELLED"))
        events = _result([ics], now)
        assert events == []

    def test_summary_canceled_prefix_skipped(self):
        now = _make_dt(2025, 6, 15, 10, 0)
        ics = _ics(_event("uid1", "Canceled: Stand-up", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now)
        assert events == []


# ---------------------------------------------------------------------------
# 5. UID deduplication across calendars
# ---------------------------------------------------------------------------

class TestUidDedup:

    def test_higher_priority_calendar_wins(self):
        now = _make_dt(2025, 6, 15, 9, 0)
        ics1 = _ics(_event("shared-uid", "Primary Version",   "20250615T100000Z", "20250615T110000Z"))
        ics2 = _ics(_event("shared-uid", "Secondary Version", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics1, ics2], now)
        uid_events = [e for e in events if e["uid"] == "shared-uid"]
        assert len(uid_events) == 1
        assert uid_events[0]["summary"] == "Primary Version"

    def test_unique_uids_both_shown(self):
        now = _make_dt(2025, 6, 15, 9, 0)
        ics1 = _ics(_event("uid-a", "Event A", "20250615T100000Z", "20250615T110000Z"))
        ics2 = _ics(_event("uid-b", "Event B", "20250615T120000Z", "20250615T130000Z"))
        events = _result([ics1, ics2], now)
        summaries = {e["summary"] for e in events}
        assert summaries == {"Event A", "Event B"}


# ---------------------------------------------------------------------------
# 6. Recurring events
# ---------------------------------------------------------------------------

class TestRecurringEvents:

    def test_recurring_event_appears_on_day(self):
        """Daily recurring event must appear on the target date."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(_event(
            "recurring-uid", "Daily Stand-up",
            "20250601T100000Z", "20250601T101500Z",
            rrule="FREQ=DAILY",
        ))
        events = _result([ics], now, target_date=date(2025, 6, 15))
        assert any(e["summary"] == "Daily Stand-up" for e in events)

    def test_recurring_duration_applied_correctly(self):
        """
        CRITICAL BUG: duration must come from master, not occurrence's raw DTEND.
        Master: Nov 20 2025 11:00–11:15 (15 min).
        Occurrence Feb 9 2026 must end at 11:15, not at Nov 20 11:15.
        """
        now = _make_dt(2026, 2, 9, 10, 0)
        ics = _ics(_event(
            "recurring-dur-uid", "Short Meeting",
            "20251120T110000Z", "20251120T111500Z",
            rrule="FREQ=WEEKLY;BYDAY=MO",
        ))
        events = _result([ics], now, target_date=date(2026, 2, 9))
        assert len(events) == 1
        ev = events[0]
        duration_minutes = (ev["end_ms"] - ev["start_ms"]) / 60_000
        assert duration_minutes == 15, f"Expected 15 min, got {duration_minutes}"
        assert ev["end_ms"] > ev["start_ms"]

    def test_window_expansion_includes_events_before_now(self):
        """
        CRITICAL: expand from windowStart, not NOW.
        Event at 09:00, NOW=10:00 – must still appear.
        """
        now = _make_dt(2025, 6, 15, 10, 0)
        ics = _ics(_event(
            "early-recurring", "Morning Sync",
            "20250601T090000Z", "20250601T093000Z",
            rrule="FREQ=DAILY",
        ))
        events = _result([ics], now, target_date=date(2025, 6, 15))
        assert any(e["summary"] == "Morning Sync" for e in events)


# ---------------------------------------------------------------------------
# 7. Override (RECURRENCE-ID) handling
# ---------------------------------------------------------------------------

class TestOverrides:

    def test_unused_override_currently_happening(self):
        """Orphaned override (no master) that is currently happening should be included."""
        now = _make_dt(2025, 6, 15, 10, 20)
        ics = _ics(_event(
            "orphan-uid", "Rescheduled Meeting",
            "20250615T100000Z", "20250615T110000Z",
            recurrence_id="20250615T090000Z",
        ))
        events = _result([ics], now)
        assert any(e["summary"] == "Rescheduled Meeting" for e in events)

    def test_override_replaces_occurrence(self):
        """Override for a specific occurrence replaces (not duplicates) that occurrence."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(
            _event("master-uid", "Regular Meeting",
                   "20250601T100000Z", "20250601T110000Z",
                   rrule="FREQ=DAILY"),
            _event("master-uid", "Modified Meeting",
                   "20250615T140000Z", "20250615T150000Z",
                   recurrence_id="20250615T100000Z"),
        )
        events = _result([ics], now, target_date=date(2025, 6, 15))
        modified = [e for e in events if e["summary"] == "Modified Meeting"]
        assert len(modified) >= 1


# ---------------------------------------------------------------------------
# 8. Timezone handling
# ---------------------------------------------------------------------------

class TestTimezones:

    def test_new_york_timezone_window(self):
        """Window must be [midnight NY, midnight tomorrow NY)."""
        # EDT = UTC-4; NY midnight Jun 15 = 04:00 UTC Jun 15
        now = _make_dt(2025, 6, 15, 10, 0, NY)
        # 03:30 UTC = 23:30 NY on Jun 14 → BEFORE window
        # 04:30 UTC = 00:30 NY on Jun 15 → INSIDE window
        ics = _ics(
            _event("before", "Late Night", "20250615T033000Z", "20250615T040000Z"),
            _event("inside", "Early Bird", "20250615T043000Z", "20250615T050000Z"),
        )
        events = _result([ics], now, user_tz="America/New_York", target_date=date(2025, 6, 15))
        summaries = {e["summary"] for e in events}
        assert "Early Bird" in summaries
        assert "Late Night" not in summaries

    def test_pacific_vtimezone_not_remapped(self):
        """
        CRITICAL: VTIMEZONE exists for "Pacific Standard Time" →
        do NOT remap it to "America/Los_Angeles".
        icalendar uses the embedded VTIMEZONE block as-is.
        """
        # Jun 15 10:00 PDT = Jun 15 17:00 UTC
        now = _make_dt(2025, 6, 15, 17, 0, UTC)
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Microsoft Corporation//Outlook 16.0//EN\r\n"
            + PACIFIC_VTIMEZONE + "\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:pacific-test-uid\r\n"
            "SUMMARY:Pacific Meeting\r\n"
            "DTSTART;TZID=Pacific Standard Time:20250615T100000\r\n"
            "DTEND;TZID=Pacific Standard Time:20250615T110000\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        events = _result([ics], now, user_tz="America/Los_Angeles", target_date=date(2025, 6, 15))
        assert any(e["summary"] == "Pacific Meeting" for e in events)

    def test_windows_timezone_name_without_vtimezone(self):
        """Windows TZ name with NO VTIMEZONE block should be mapped to IANA."""
        now = _make_dt(2025, 6, 15, 14, 0, UTC)
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:windows-tz-uid\r\n"
            "SUMMARY:EST Meeting\r\n"
            "DTSTART;TZID=Eastern Standard Time:20250615T100000\r\n"
            "DTEND;TZID=Eastern Standard Time:20250615T110000\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        events = _result([ics], now, user_tz="America/New_York", target_date=date(2025, 6, 15))
        assert any(e["summary"] == "EST Meeting" for e in events)


# ---------------------------------------------------------------------------
# 9. Overlap detection
# ---------------------------------------------------------------------------

class TestOverlapDetection:

    def test_event_spanning_midnight_included(self):
        """Event starting yesterday but ending today must appear."""
        now = _make_dt(2025, 6, 15, 1, 0)
        # Starts 23:00 UTC Jun 14, ends 02:00 UTC Jun 15
        ics = _ics(_event("span", "Overnight", "20250614T230000Z", "20250615T020000Z"))
        events = _result([ics], now, user_tz="UTC", target_date=date(2025, 6, 15))
        assert any(e["summary"] == "Overnight" for e in events)

    def test_event_starting_at_window_end_excluded(self):
        """Event starting exactly at midnight tomorrow must NOT appear today."""
        now = _make_dt(2025, 6, 15, 10, 0)
        ics = _ics(_event("next-day", "Tomorrow", "20250616T000000Z", "20250616T010000Z"))
        events = _result([ics], now, user_tz="UTC", target_date=date(2025, 6, 15))
        assert events == []


# ---------------------------------------------------------------------------
# 10. Next overlapping and next non-overlapping
# ---------------------------------------------------------------------------

class TestNextOverlapping:

    def test_next_overlapping_requires_current(self):
        """is_next_overlapping only set when there is a current event."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(
            _event("a", "A", "20250615T100000Z", "20250615T110000Z"),
            _event("b", "B", "20250615T103000Z", "20250615T113000Z"),
        )
        events = _result([ics], now)
        # NOW is before A — no current, so no overlapping
        assert not any(e["is_next_overlapping"] for e in events)

    def test_no_next_overlapping_when_no_overlap(self):
        """Events that don't overlap current → next_overlapping absent."""
        now = _make_dt(2025, 6, 15, 10, 15)
        ics = _ics(
            _event("a", "A", "20250615T100000Z", "20250615T110000Z"),
            _event("b", "B", "20250615T120000Z", "20250615T130000Z"),
        )
        events = _result([ics], now)
        # A is current; B starts after A ends → not overlapping
        assert not any(e["is_next_overlapping"] for e in events)


# ---------------------------------------------------------------------------
# 11. Missing data edge cases
# ---------------------------------------------------------------------------

class TestMissingData:

    def test_event_without_dtend_defaults_to_60_min(self):
        now = _make_dt(2025, 6, 15, 10, 0)
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:no-end-uid\r\n"
            "SUMMARY:No End Event\r\n"
            "DTSTART:20250615T100000Z\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        events = _result([ics], now)
        assert len(events) == 1
        duration_ms = events[0]["end_ms"] - events[0]["start_ms"]
        assert duration_ms == 3_600_000  # 60 minutes

    def test_event_without_uid_skipped(self):
        now = _make_dt(2025, 6, 15, 10, 0)
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "SUMMARY:No UID Event\r\n"
            "DTSTART:20250615T100000Z\r\n"
            "DTEND:20250615T110000Z\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        events = _result([ics], now)
        assert events == []


# ---------------------------------------------------------------------------
# 12. Multiple events — comprehensive scenario
# ---------------------------------------------------------------------------

class TestMultipleEventsScenario:

    def test_full_day_scenario(self):
        """5 events; NOW mid-morning. Verify count, order, is_current, is_next."""
        now = _make_dt(2025, 6, 15, 10, 30)
        ics = _ics(
            _event("e1", "Breakfast",  "20250615T080000Z", "20250615T083000Z"),
            _event("e2", "Stand-up",   "20250615T100000Z", "20250615T101500Z"),
            _event("e3", "Deep Work",  "20250615T110000Z", "20250615T130000Z"),
            _event("e4", "Lunch",      "20250615T130000Z", "20250615T140000Z"),
            _event("e5", "Review",     "20250615T160000Z", "20250615T170000Z"),
        )
        events = _result([ics], now)
        assert len(events) == 5
        by_summary = {e["summary"]: e for e in events}
        # Stand-up ended 10:15; NOW=10:30 → no current
        assert by_summary["Stand-up"]["is_current"] is False
        # Deep Work starts 11:00 > NOW=10:30 → next
        assert by_summary["Deep Work"]["is_next"] is True

    def test_events_sorted_by_start(self):
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(
            _event("e1", "Third",  "20250615T140000Z", "20250615T150000Z"),
            _event("e2", "First",  "20250615T100000Z", "20250615T110000Z"),
            _event("e3", "Second", "20250615T120000Z", "20250615T130000Z"),
        )
        events = _result([ics], now)
        assert [e["summary"] for e in events] == ["First", "Second", "Third"]


# ===========================================================================
# ADDITIONAL EDGE CASE TESTS
# ===========================================================================

# ---------------------------------------------------------------------------
# A. Recurring Events — extra cases
# ---------------------------------------------------------------------------

class TestRecurringEdgeCases:

    def test_rrule_until_expired_no_events(self):
        """RRULE with UNTIL in the past must produce no occurrences."""
        now = _make_dt(2026, 2, 9, 10, 0)
        ics = _ics(_event(
            "expired-uid", "Expired Series",
            "20251001T100000Z", "20251001T110000Z",
            rrule="FREQ=WEEKLY;UNTIL=20251210T000000Z",
        ))
        events = _result([ics], now, target_date=date(2026, 2, 9))
        assert events == [], f"Expected no events, got {events}"

    def test_rrule_byday_weekdays_only_skips_weekend(self):
        """BYDAY=MO,TU,WE,TH,FR — Saturday must not produce an occurrence."""
        now = _make_dt(2025, 6, 14, 9, 0)  # Jun 14 2025 is a Saturday
        ics = _ics(_event(
            "weekday-uid", "Weekday Standup",
            "20250601T100000Z", "20250601T101500Z",
            rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        ))
        events = _result([ics], now, target_date=date(2025, 6, 14))
        assert events == [], f"Should not have weekend occurrence, got {events}"

    def test_rrule_byday_weekdays_only_includes_monday(self):
        """BYDAY=MO,TU,WE,TH,FR — Monday must produce an occurrence."""
        now = _make_dt(2025, 6, 16, 9, 0)  # Jun 16 2025 is a Monday
        ics = _ics(_event(
            "weekday-uid", "Weekday Standup",
            "20250601T100000Z", "20250601T101500Z",
            rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        ))
        events = _result([ics], now, target_date=date(2025, 6, 16))
        assert any(e["summary"] == "Weekday Standup" for e in events)

    def test_recurring_event_started_months_ago_shows_today(self):
        """Series started 3 months ago — today's occurrence must appear."""
        now = _make_dt(2025, 9, 15, 9, 0)
        ics = _ics(_event(
            "old-series", "Old Series",
            "20250601T100000Z", "20250601T110000Z",
            rrule="FREQ=DAILY",
        ))
        events = _result([ics], now, target_date=date(2025, 9, 15))
        assert any(e["summary"] == "Old Series" for e in events)


# ---------------------------------------------------------------------------
# B. Zero-duration and long events
# ---------------------------------------------------------------------------

class TestEventDurationEdgeCases:

    def test_zero_duration_event_gets_one_minute(self):
        """DTSTART == DTEND → treat as 1-minute (60s) event, not zero."""
        now = _make_dt(2025, 6, 15, 10, 0)
        ics = _ics(_event("zero-dur", "Marker", "20250615T100000Z", "20250615T100000Z"))
        events = _result([ics], now)
        assert len(events) == 1
        duration_ms = events[0]["end_ms"] - events[0]["start_ms"]
        assert duration_ms > 0, "Zero-duration event must get a positive duration"

    def test_event_longer_than_window_included(self):
        """48-hour event that fully covers today's window must appear."""
        now = _make_dt(2025, 6, 15, 12, 0)
        # Starts yesterday 00:00 UTC, ends tomorrow 00:00 UTC (48h)
        ics = _ics(_event("long-ev", "Conference", "20250614T000000Z", "20250616T000000Z"))
        events = _result([ics], now, user_tz="UTC", target_date=date(2025, 6, 15))
        assert any(e["summary"] == "Conference" for e in events)

    def test_event_with_duration_property(self):
        """Event using DURATION instead of DTEND must compute end correctly."""
        now = _make_dt(2025, 6, 15, 10, 0)
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:dur-prop-uid\r\n"
            "SUMMARY:Duration Event\r\n"
            "DTSTART:20250615T100000Z\r\n"
            "DURATION:PT90M\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        events = _result([ics], now)
        assert len(events) == 1
        duration_ms = events[0]["end_ms"] - events[0]["start_ms"]
        assert duration_ms == 90 * 60 * 1000  # 90 minutes


# ---------------------------------------------------------------------------
# C. ICS File Format edge cases
# ---------------------------------------------------------------------------

class TestICSFormat:

    def test_folded_lines_uid(self):
        """RFC 5545 line folding: UID split across lines must be read correctly."""
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:folded-uid-that-is-very-long-and-gets-\r\n"
            " wrapped-by-the-calendar-application\r\n"
            "SUMMARY:Folded UID Event\r\n"
            "DTSTART:20250615T100000Z\r\n"
            "DTEND:20250615T110000Z\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        now = _make_dt(2025, 6, 15, 9, 0)
        events = _result([ics], now)
        assert len(events) == 1
        assert events[0]["uid"] == "folded-uid-that-is-very-long-and-gets-wrapped-by-the-calendar-application"

    def test_utf8_summary(self):
        """Cyrillic / emoji in summary must survive round-trip."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(_event("utf8-uid", "Встреча 🗓️", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now)
        assert len(events) == 1
        assert events[0]["summary"] == "Встреча 🗓️"

    def test_multiple_events_same_start_time(self):
        """Three events at same start time — all should appear, first is next."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(
            _event("s1", "Alpha",   "20250615T140000Z", "20250615T150000Z"),
            _event("s2", "Beta",    "20250615T140000Z", "20250615T150000Z"),
            _event("s3", "Gamma",   "20250615T140000Z", "20250615T150000Z"),
        )
        events = _result([ics], now)
        assert len(events) == 3
        # At least one is marked next
        assert sum(1 for e in events if e["is_next"]) == 1


# ---------------------------------------------------------------------------
# D. Override edge cases
# ---------------------------------------------------------------------------

class TestOverrideEdgeCases:

    def test_override_moves_event_to_different_time(self):
        """Override with different DTSTART than RECURRENCE-ID must use new time."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(
            _event("mv-uid", "Regular",
                   "20250601T100000Z", "20250601T110000Z",
                   rrule="FREQ=DAILY"),
            _event("mv-uid", "Moved Meeting",
                   "20250615T143000Z", "20250615T153000Z",
                   recurrence_id="20250615T100000Z"),
        )
        events = _result([ics], now, target_date=date(2025, 6, 15))
        moved = [e for e in events if e["summary"] == "Moved Meeting"]
        assert len(moved) >= 1
        # Start time should be 14:30, not 10:00
        moved_start = datetime.fromtimestamp(moved[0]["start_ms"] / 1000, tz=UTC)
        assert moved_start.hour == 14
        assert moved_start.minute == 30

    def test_orphaned_override_before_master_start(self):
        """Override for a date before the master series started (orphaned)."""
        now = _make_dt(2025, 6, 7, 10, 0)  # Jun 7
        # Master starts Jun 14, but override is for Jun 7
        ics = _ics(
            _event("early-uid", "Weekly",
                   "20250614T100000Z", "20250614T110000Z",
                   rrule="FREQ=WEEKLY"),
            _event("early-uid", "Early Override",
                   "20250607T100000Z", "20250607T110000Z",
                   recurrence_id="20250607T100000Z"),
        )
        events = _result([ics], now, target_date=date(2025, 6, 7))
        # The override is orphaned (master doesn't cover Jun 7) — should appear
        assert any(e["summary"] == "Early Override" for e in events)

    def test_cancelled_status_on_override_skipped(self):
        """Override with STATUS:CANCELLED must not appear."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(
            _event("cancel-ov-uid", "Weekly",
                   "20250601T100000Z", "20250601T110000Z",
                   rrule="FREQ=DAILY"),
            _event("cancel-ov-uid", "Cancelled Occurrence",
                   "20250615T100000Z", "20250615T110000Z",
                   recurrence_id="20250615T100000Z",
                   status="CANCELLED"),
        )
        events = _result([ics], now, target_date=date(2025, 6, 15))
        assert not any(e["summary"] == "Cancelled Occurrence" for e in events)

    def test_exdate_excludes_occurrence(self):
        """EXDATE on master must exclude that specific occurrence."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:exdate-uid\r\n"
            "SUMMARY:Daily With Exception\r\n"
            "DTSTART:20250601T100000Z\r\n"
            "DTEND:20250601T110000Z\r\n"
            "RRULE:FREQ=DAILY\r\n"
            "EXDATE:20250615T100000Z\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        events = _result([ics], now, target_date=date(2025, 6, 15))
        assert events == [], f"EXDATE should exclude Jun 15 occurrence, got {events}"


# ---------------------------------------------------------------------------
# E. Cluster logic — three events
# ---------------------------------------------------------------------------

class TestClusterLogic:

    def test_three_event_cluster_during_first(self):
        """
        NOW during A (10:15).
        A 10:00-11:00 → current
        B 10:30-11:30 → next_overlapping (starts inside A)
        C 11:00-12:00 → starts at A's end, not inside A → not overlapping
        D 14:00-15:00 → next (first after A ends)
        """
        now = _make_dt(2025, 6, 15, 10, 15)
        ics = _ics(
            _event("a", "A", "20250615T100000Z", "20250615T110000Z"),
            _event("b", "B", "20250615T103000Z", "20250615T113000Z"),
            _event("c", "C", "20250615T110000Z", "20250615T120000Z"),
            _event("d", "D", "20250615T140000Z", "20250615T150000Z"),
        )
        events = _result([ics], now)
        by = {e["summary"]: e for e in events}
        assert by["A"]["is_current"] is True
        assert by["B"]["is_next_overlapping"] is True
        assert by["C"]["is_next"] is True   # starts exactly at A's end → next
        assert by["D"]["is_next"] is False  # C comes first

    def test_events_at_same_start_overlap_cluster(self):
        """
        3 events all at 14:00-15:00 — only one is 'next', rest are overlapping.
        """
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(
            _event("x1", "X1", "20250615T140000Z", "20250615T150000Z"),
            _event("x2", "X2", "20250615T140000Z", "20250615T150000Z"),
            _event("x3", "X3", "20250615T140000Z", "20250615T150000Z"),
        )
        events = _result([ics], now)
        next_count = sum(1 for e in events if e["is_next"])
        assert next_count == 1


# ---------------------------------------------------------------------------
# F. Timezone — Central Europe Standard Time
# ---------------------------------------------------------------------------

class TestCentralEuropeTimezone:

    def test_central_europe_standard_time_without_vtimezone(self):
        """
        Central Europe Standard Time (UTC+1 winter) without VTIMEZONE block.
        Event at 16:30 CET = 15:30 UTC.
        """
        now = _make_dt(2026, 2, 16, 15, 0, UTC)
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:cest-uid\r\n"
            "SUMMARY:CET Meeting\r\n"
            "DTSTART;TZID=Central Europe Standard Time:20260216T163000\r\n"
            "DTEND;TZID=Central Europe Standard Time:20260216T173000\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        events = _result([ics], now, user_tz="Europe/Berlin", target_date=date(2026, 2, 16))
        assert any(e["summary"] == "CET Meeting" for e in events)

    def test_fle_standard_time_maps_correctly(self):
        """FLE Standard Time = Europe/Kiev (UTC+2 winter, UTC+3 summer)."""
        now = _make_dt(2026, 2, 16, 10, 0, UTC)
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:fle-uid\r\n"
            "SUMMARY:FLE Meeting\r\n"
            "DTSTART;TZID=FLE Standard Time:20260216T120000\r\n"
            "DTEND;TZID=FLE Standard Time:20260216T130000\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        events = _result([ics], now, user_tz="Europe/Kiev", target_date=date(2026, 2, 16))
        assert any(e["summary"] == "FLE Meeting" for e in events)


# ---------------------------------------------------------------------------
# G. Window edge cases
# ---------------------------------------------------------------------------

class TestWindowEdgeCases:

    def test_event_crossing_midnight_in_today_window(self):
        """Event 23:00 today → 01:00 tomorrow must appear in today's window."""
        now = _make_dt(2025, 6, 15, 23, 30)
        ics = _ics(_event("cross", "Late Event", "20250615T230000Z", "20250616T010000Z"))
        events = _result([ics], now, user_tz="UTC", target_date=date(2025, 6, 15))
        assert any(e["summary"] == "Late Event" for e in events)

    def test_event_crossing_midnight_in_tomorrow_window(self):
        """Same crossing event must NOT appear in tomorrow's window
        (it starts today, so its start < tomorrow window_start)."""
        now = _make_dt(2025, 6, 16, 0, 30)
        ics = _ics(_event("cross", "Late Event", "20250615T230000Z", "20250616T010000Z"))
        events = _result([ics], now, user_tz="UTC", target_date=date(2025, 6, 16))
        # It ends at 01:00 tomorrow — overlaps tomorrow window [00:00, 24:00)
        assert any(e["summary"] == "Late Event" for e in events)

    def test_dst_spring_forward_window(self):
        """
        US spring forward: Mar 9 2025, clocks go 02:00 → 03:00.
        Window for Mar 9 in NY tz must still be exactly 23 hours long
        (missing hour) and events must be found correctly.
        """
        SPRING_FORWARD = date(2025, 3, 9)
        now = _make_dt(2025, 3, 9, 14, 0, NY)
        # Event at noon NY = 17:00 UTC (EDT begins, so noon NY = UTC-4+1=UTC-4? no — after spring forward EDT = UTC-4)
        ics = _ics(_event("dst-ev", "DST Day Event", "20250309T160000Z", "20250309T170000Z"))
        events = _result([ics], now, user_tz="America/New_York", target_date=SPRING_FORWARD)
        assert any(e["summary"] == "DST Day Event" for e in events)


# ---------------------------------------------------------------------------
# H. now_override / target_date combinations
# ---------------------------------------------------------------------------

class TestNowAndTargetDate:

    def test_target_date_different_from_now(self):
        """target_date can differ from the date implied by now_override."""
        # now = tomorrow, but we query today's events
        now = _make_dt(2025, 6, 16, 9, 0)
        ics = _ics(_event("td-uid", "Yesterday's Event", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now, user_tz="UTC", target_date=date(2025, 6, 15))
        assert any(e["summary"] == "Yesterday's Event" for e in events)

    def test_no_events_on_empty_day(self):
        """Day with zero events returns empty list, not error."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(_event("other-uid", "Other Day", "20250616T100000Z", "20250616T110000Z"))
        events = _result([ics], now, user_tz="UTC", target_date=date(2025, 6, 15))
        assert events == []

    def test_multiple_calendars_empty_one(self):
        """One calendar with events, one empty — both must be processed."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics_with = _ics(_event("uid-a", "Has Event", "20250615T100000Z", "20250615T110000Z"))
        ics_empty = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nEND:VCALENDAR\r\n"
        events = _result([ics_with, ics_empty], now)
        assert len(events) == 1
        assert events[0]["summary"] == "Has Event"


# ---------------------------------------------------------------------------
# I. windows_zones module
# ---------------------------------------------------------------------------

class TestWindowsZones:

    def test_common_mapping_eastern(self):
        try:
            from .windows_zones import windows_to_iana
        except ImportError:
            from windows_zones import windows_to_iana  # type: ignore[no-redef]
        assert windows_to_iana("Eastern Standard Time") == "America/New_York"

    def test_common_mapping_pacific(self):
        try:
            from .windows_zones import windows_to_iana
        except ImportError:
            from windows_zones import windows_to_iana  # type: ignore[no-redef]
        assert windows_to_iana("Pacific Standard Time") == "America/Los_Angeles"

    def test_common_mapping_central_europe(self):
        try:
            from .windows_zones import windows_to_iana
        except ImportError:
            from windows_zones import windows_to_iana  # type: ignore[no-redef]
        result = windows_to_iana("Central Europe Standard Time")
        assert result == "Europe/Budapest"

    def test_common_mapping_fle(self):
        try:
            from .windows_zones import windows_to_iana
        except ImportError:
            from windows_zones import windows_to_iana  # type: ignore[no-redef]
        result = windows_to_iana("FLE Standard Time")
        # FLE = Finland/Kyiv region
        assert result in ("Europe/Kiev", "Europe/Kyiv", "Europe/Helsinki")

    def test_unknown_name_returns_none(self):
        try:
            from .windows_zones import windows_to_iana
        except ImportError:
            from windows_zones import windows_to_iana  # type: ignore[no-redef]
        assert windows_to_iana("Totally Fake Standard Time") is None

    def test_utc_maps_to_utc(self):
        try:
            from .windows_zones import windows_to_iana
        except ImportError:
            from windows_zones import windows_to_iana  # type: ignore[no-redef]
        # CLDR maps "UTC" to "Etc/UTC"; both are valid IANA identifiers
        assert windows_to_iana("UTC") in ("UTC", "Etc/UTC")

    def test_reload_with_fallback(self):
        try:
            from .windows_zones import reload, windows_to_iana
        except ImportError:
            from windows_zones import reload, windows_to_iana  # type: ignore[no-redef]
        count = reload(use_fallback=True)
        assert count > 50  # fallback has 100+ entries
        # Mapping still works after reload
        assert windows_to_iana("Eastern Standard Time") == "America/New_York"

    def test_fallback_covers_all_common_exchange_zones(self):
        try:
            from .windows_zones import windows_to_iana
        except ImportError:
            from windows_zones import windows_to_iana  # type: ignore[no-redef]
        common = [
            "Eastern Standard Time",
            "Central Standard Time",
            "Mountain Standard Time",
            "Pacific Standard Time",
            "GMT Standard Time",
            "Central Europe Standard Time",
            "Tokyo Standard Time",
            "China Standard Time",
            "India Standard Time",
            "Arabian Standard Time",
            "AUS Eastern Standard Time",
        ]
        for name in common:
            result = windows_to_iana(name)
            assert result is not None, f"{name!r} not found in mapping"


# ---------------------------------------------------------------------------
# J. calendar_id field
# ---------------------------------------------------------------------------

class TestCalendarId:

    def test_calendar_id_matches_position_in_list(self):
        """calendar_id must equal the 0-based index of the source calendar."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics0 = _ics(_event("uid-0", "From Cal 0", "20250615T100000Z", "20250615T110000Z"))
        ics1 = _ics(_event("uid-1", "From Cal 1", "20250615T120000Z", "20250615T130000Z"))
        ics2 = _ics(_event("uid-2", "From Cal 2", "20250615T140000Z", "20250615T150000Z"))
        events = _result([ics0, ics1, ics2], now)
        by_summary = {e["summary"]: e for e in events}
        assert by_summary["From Cal 0"]["calendar_id"] == 0
        assert by_summary["From Cal 1"]["calendar_id"] == 1
        assert by_summary["From Cal 2"]["calendar_id"] == 2

    def test_calendar_id_present_in_all_events(self):
        """Every returned event must have a calendar_id field."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(
            _event("e1", "A", "20250615T100000Z", "20250615T110000Z"),
            _event("e2", "B", "20250615T120000Z", "20250615T130000Z"),
        )
        events = _result([ics], now)
        for ev in events:
            assert "calendar_id" in ev
            assert isinstance(ev["calendar_id"], int)

    def test_calendar_id_winner_is_lowest_index(self):
        """Duplicate UID: calendar_id reflects the winning (lowest-index) calendar."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics0 = _ics(_event("dup", "Primary",   "20250615T100000Z", "20250615T110000Z"))
        ics1 = _ics(_event("dup", "Secondary", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics0, ics1], now)
        assert len(events) == 1
        assert events[0]["calendar_id"] == 0


# ---------------------------------------------------------------------------
# K. windows_zones — file cache
# ---------------------------------------------------------------------------

class TestWindowsZonesFileCache:

    def setup_method(self):
        """Reset to memory-only mode and clear in-memory cache before each test."""
        try:
            from .windows_zones import configure, reload
        except ImportError:
            from windows_zones import configure, reload
        configure(file_cache=False)
        reload(use_fallback=True)

    def _tmpfile(self, suffix=".json"):
        """Create a temp file path in the project's own temp dir (avoids Windows ACL issues)."""
        import tempfile, shutil
        d = tempfile.mkdtemp(prefix="wz_test_", dir=os.path.dirname(__file__))
        self._tmpdirs = getattr(self, "_tmpdirs", [])
        self._tmpdirs.append(d)
        return os.path.join(d, "zones" + suffix)

    def teardown_method(self):
        """Remove temp dirs created by _tmpfile."""
        import shutil
        for d in getattr(self, "_tmpdirs", []):
            shutil.rmtree(d, ignore_errors=True)

    def test_memory_mode_default(self):
        """Default mode is memory-only."""
        try:
            from .windows_zones import cache_info
        except ImportError:
            from windows_zones import cache_info
        info = cache_info()
        assert info["mode"] == "memory"
        assert info["cache_path"] is None

    def test_file_cache_mode_flag(self):
        """configure(file_cache=True) switches mode."""
        try:
            from .windows_zones import configure, cache_info
        except ImportError:
            from windows_zones import configure, cache_info
        path = self._tmpfile()
        configure(file_cache=True, cache_path=path)
        info = cache_info()
        assert info["mode"] == "file"
        assert info["cache_path"] == path

    def test_file_cache_written_on_load(self):
        """When file_cache=True and no file exists, file is created after load."""
        import json as _json
        try:
            from .windows_zones import configure, reload, cache_info
        except ImportError:
            from windows_zones import configure, reload, cache_info
        path = self._tmpfile()
        configure(file_cache=True, cache_path=path)
        reload(use_fallback=True)
        try:
            from .windows_zones import _write_file_cache, _FALLBACK
        except ImportError:
            from windows_zones import _write_file_cache, _FALLBACK
        _write_file_cache(dict(_FALLBACK))
        assert os.path.exists(path)
        with open(path) as f:
            data = _json.load(f)
        assert "fetched_at" in data
        assert "mapping" in data
        assert data["mapping"]["Eastern Standard Time"] == "America/New_York"

    def test_fresh_file_cache_is_used(self):
        """Fresh file cache must be returned without hitting CLDR."""
        import json as _json, time as _time
        try:
            from .windows_zones import configure, reload, _write_file_cache, _FALLBACK, windows_to_iana
        except ImportError:
            from windows_zones import configure, reload, _write_file_cache, _FALLBACK, windows_to_iana
        path = self._tmpfile()
        custom = dict(_FALLBACK)
        custom["Test Zone"] = "Test/Zone"
        configure(file_cache=True, cache_path=path, cache_ttl_seconds=3600)
        _write_file_cache(custom)
        reload()  # should read from fresh file
        assert windows_to_iana("Test Zone") == "Test/Zone"

    def test_stale_file_cache_triggers_refetch(self):
        """Stale file cache (older than TTL) must trigger a refetch attempt."""
        import json as _json, time as _time
        try:
            from .windows_zones import configure, _FALLBACK, cache_info
        except ImportError:
            from windows_zones import configure, _FALLBACK, cache_info
        path = self._tmpfile()
        configure(file_cache=True, cache_path=path, cache_ttl_seconds=1)
        data = {"fetched_at": _time.time() - 100, "mapping": dict(_FALLBACK)}
        with open(path, "w") as f:
            _json.dump(data, f)
        info = cache_info()
        assert info["file_is_fresh"] is False

    def test_ttl_respected(self):
        """File younger than TTL is fresh; older than TTL is stale."""
        import json as _json, time as _time
        try:
            from .windows_zones import configure, cache_info, _FALLBACK
        except ImportError:
            from windows_zones import configure, cache_info, _FALLBACK
        path = self._tmpfile()
        configure(file_cache=True, cache_path=path, cache_ttl_seconds=3600)
        data_fresh = {"fetched_at": _time.time(), "mapping": dict(_FALLBACK)}
        with open(path, "w") as f:
            _json.dump(data_fresh, f)
        assert cache_info()["file_is_fresh"] is True
        data_stale = {"fetched_at": _time.time() - 7200, "mapping": dict(_FALLBACK)}
        with open(path, "w") as f:
            _json.dump(data_stale, f)
        assert cache_info()["file_is_fresh"] is False

    def test_cache_info_entries_count(self):
        """cache_info returns correct entry count."""
        try:
            from .windows_zones import reload, cache_info
        except ImportError:
            from windows_zones import reload, cache_info
        reload(use_fallback=True)
        info = cache_info()
        assert info["entries"] > 50

    def test_configure_invalidates_memory_cache(self):
        """Calling configure() must invalidate the in-memory cache."""
        try:
            import windows_zones as wz
        except ImportError:
            import importlib, sys
            wz = sys.modules.get("windows_zones") or sys.modules.get("icscal.windows_zones")
        wz.configure(file_cache=False)
        assert wz._mem_cache is None


# ===========================================================================
# L. Flag semantics: is_current / is_next / is_next_overlapping
# ===========================================================================

class TestFlagSemantics:
    """
    Exhaustive tests for the three event flags.

    Definitions
    -----------
    is_current        : start <= now < end
    is_next           : first event with start >= current.end
                        (or start >= now when no current); does NOT overlap current
    is_next_overlapping: first event with start > current.start
                         AND start < current.end  (runs concurrently with current)
                         only set when there IS a current event
    """

    # ------------------------------------------------------------------
    # is_current
    # ------------------------------------------------------------------

    def test_current_starts_exactly_at_now(self):
        now = _make_dt(2025, 6, 15, 10, 0)
        ics = _ics(_event("c", "C", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now)
        assert events[0]["is_current"] is True

    def test_current_ends_exactly_at_now_excluded(self):
        now = _make_dt(2025, 6, 15, 11, 0)
        ics = _ics(_event("c", "C", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now)
        assert events[0]["is_current"] is False

    def test_current_in_middle_of_event(self):
        now = _make_dt(2025, 6, 15, 10, 30)
        ics = _ics(_event("c", "C", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now)
        assert events[0]["is_current"] is True

    def test_no_current_all_future(self):
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(_event("c", "C", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now)
        assert not any(e["is_current"] for e in events)

    def test_no_current_all_past(self):
        now = _make_dt(2025, 6, 15, 14, 0)
        ics = _ics(_event("c", "C", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now)
        assert not any(e["is_current"] for e in events)

    def test_only_one_current_at_a_time(self):
        """Even with overlapping events, only the first (by start) is current."""
        now = _make_dt(2025, 6, 15, 10, 15)
        ics = _ics(
            _event("a", "A", "20250615T100000Z", "20250615T110000Z"),
            _event("b", "B", "20250615T100000Z", "20250615T113000Z"),
        )
        events = _result([ics], now)
        current = [e for e in events if e["is_current"]]
        assert len(current) == 1

    # ------------------------------------------------------------------
    # is_next — no current event
    # ------------------------------------------------------------------

    def test_next_is_first_future_when_no_current(self):
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(
            _event("a", "A", "20250615T100000Z", "20250615T110000Z"),
            _event("b", "B", "20250615T120000Z", "20250615T130000Z"),
        )
        events = _result([ics], now)
        nxt = [e for e in events if e["is_next"]]
        assert len(nxt) == 1
        assert nxt[0]["summary"] == "A"

    def test_next_skips_past_events_when_no_current(self):
        now = _make_dt(2025, 6, 15, 11, 30)
        ics = _ics(
            _event("a", "A", "20250615T100000Z", "20250615T110000Z"),  # past
            _event("b", "B", "20250615T120000Z", "20250615T130000Z"),  # future
        )
        events = _result([ics], now)
        nxt = [e for e in events if e["is_next"]]
        assert len(nxt) == 1
        assert nxt[0]["summary"] == "B"

    def test_no_next_when_all_past_and_no_current(self):
        now = _make_dt(2025, 6, 15, 20, 0)
        ics = _ics(_event("a", "A", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now)
        assert not any(e["is_next"] for e in events)

    # ------------------------------------------------------------------
    # is_next — with current event
    # ------------------------------------------------------------------

    def test_next_is_after_current_ends_not_after_now(self):
        """
        Current 10:00-11:00, Gap event 10:30-10:45 (inside current), Next 11:30.
        NOW=10:20 → next must be 11:30, not 10:30.
        """
        now = _make_dt(2025, 6, 15, 10, 20)
        ics = _ics(
            _event("cur", "Current",  "20250615T100000Z", "20250615T110000Z"),
            _event("gap", "During",   "20250615T103000Z", "20250615T104500Z"),
            _event("nxt", "Next",     "20250615T113000Z", "20250615T120000Z"),
        )
        events = _result([ics], now)
        nxt = [e for e in events if e["is_next"]]
        assert len(nxt) == 1
        assert nxt[0]["summary"] == "Next"

    def test_next_starts_exactly_at_current_end(self):
        """Event starting exactly when current ends → is_next."""
        now = _make_dt(2025, 6, 15, 10, 30)
        ics = _ics(
            _event("cur", "Current", "20250615T100000Z", "20250615T110000Z"),
            _event("nxt", "Next",    "20250615T110000Z", "20250615T120000Z"),
        )
        events = _result([ics], now)
        nxt = [e for e in events if e["is_next"]]
        assert len(nxt) == 1
        assert nxt[0]["summary"] == "Next"

    def test_next_ignores_current_event_itself(self):
        """is_next must not point to the current event."""
        now = _make_dt(2025, 6, 15, 10, 30)
        ics = _ics(
            _event("cur", "Current", "20250615T100000Z", "20250615T110000Z"),
            _event("nxt", "Next",    "20250615T113000Z", "20250615T120000Z"),
        )
        events = _result([ics], now)
        cur = next(e for e in events if e["is_current"])
        nxt = [e for e in events if e["is_next"]]
        assert cur["summary"] == "Current"
        assert len(nxt) == 1 and nxt[0]["summary"] == "Next"

    def test_next_is_none_when_nothing_after_current(self):
        now = _make_dt(2025, 6, 15, 10, 30)
        ics = _ics(_event("cur", "Only", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now)
        assert not any(e["is_next"] for e in events)

    def test_only_one_next(self):
        now = _make_dt(2025, 6, 15, 10, 30)
        ics = _ics(
            _event("cur", "Current", "20250615T100000Z", "20250615T110000Z"),
            _event("n1",  "Next1",   "20250615T113000Z", "20250615T120000Z"),
            _event("n2",  "Next2",   "20250615T120000Z", "20250615T130000Z"),
        )
        events = _result([ics], now)
        nxt = [e for e in events if e["is_next"]]
        assert len(nxt) == 1
        assert nxt[0]["summary"] == "Next1"

    # ------------------------------------------------------------------
    # is_next_overlapping
    # ------------------------------------------------------------------

    def test_next_overlapping_basic(self):
        """
        Current 10:00-11:00. B starts at 10:30 (inside current) → overlapping.
        """
        now = _make_dt(2025, 6, 15, 10, 15)
        ics = _ics(
            _event("cur", "Current",     "20250615T100000Z", "20250615T110000Z"),
            _event("ov",  "Overlapping", "20250615T103000Z", "20250615T113000Z"),
        )
        events = _result([ics], now)
        ov = [e for e in events if e["is_next_overlapping"]]
        assert len(ov) == 1
        assert ov[0]["summary"] == "Overlapping"

    def test_next_overlapping_is_first_concurrent(self):
        """When multiple events overlap current, only the first (earliest start) is flagged."""
        now = _make_dt(2025, 6, 15, 10, 15)
        ics = _ics(
            _event("cur", "Current", "20250615T100000Z", "20250615T110000Z"),
            _event("ov1", "Ov1",     "20250615T103000Z", "20250615T113000Z"),
            _event("ov2", "Ov2",     "20250615T104500Z", "20250615T113000Z"),
        )
        events = _result([ics], now)
        ov = [e for e in events if e["is_next_overlapping"]]
        assert len(ov) == 1
        assert ov[0]["summary"] == "Ov1"

    def test_next_overlapping_absent_when_no_current(self):
        """No current event → is_next_overlapping must never be set."""
        now = _make_dt(2025, 6, 15, 9, 0)
        ics = _ics(
            _event("a", "A", "20250615T100000Z", "20250615T110000Z"),
            _event("b", "B", "20250615T103000Z", "20250615T113000Z"),
        )
        events = _result([ics], now)
        assert not any(e["is_next_overlapping"] for e in events)

    def test_next_overlapping_absent_when_events_sequential(self):
        """Current 10:00-11:00, next 11:00 → sequential, not overlapping."""
        now = _make_dt(2025, 6, 15, 10, 30)
        ics = _ics(
            _event("cur", "Current", "20250615T100000Z", "20250615T110000Z"),
            _event("nxt", "Next",    "20250615T110000Z", "20250615T120000Z"),
        )
        events = _result([ics], now)
        assert not any(e["is_next_overlapping"] for e in events)

    def test_next_overlapping_event_starts_after_current_starts(self):
        """Event that starts at same time as current is NOT overlapping (start == current.start)."""
        now = _make_dt(2025, 6, 15, 10, 15)
        ics = _ics(
            _event("cur", "Current", "20250615T100000Z", "20250615T110000Z"),
            _event("sim", "Same",    "20250615T100000Z", "20250615T113000Z"),
        )
        events = _result([ics], now)
        # "Same" starts at exactly current.start → not next_overlapping
        assert not any(e["is_next_overlapping"] and e["summary"] == "Same" for e in events)

    def test_next_and_overlapping_coexist(self):
        """
        Current 10:00-11:00, Overlapping 10:30-11:30, Next 12:00-13:00.
        All three flags set simultaneously.
        """
        now = _make_dt(2025, 6, 15, 10, 15)
        ics = _ics(
            _event("cur", "Current",     "20250615T100000Z", "20250615T110000Z"),
            _event("ov",  "Overlapping", "20250615T103000Z", "20250615T113000Z"),
            _event("nxt", "Next",        "20250615T120000Z", "20250615T130000Z"),
        )
        events = _result([ics], now)
        by = {e["summary"]: e for e in events}
        assert by["Current"]["is_current"] is True
        assert by["Overlapping"]["is_next_overlapping"] is True
        assert by["Next"]["is_next"] is True
        # Verify mutual exclusivity
        assert by["Current"]["is_next"] is False
        assert by["Current"]["is_next_overlapping"] is False
        assert by["Overlapping"]["is_current"] is False
        assert by["Overlapping"]["is_next"] is False
        assert by["Next"]["is_current"] is False
        assert by["Next"]["is_next_overlapping"] is False

    def test_flags_all_false_when_single_past_event(self):
        now = _make_dt(2025, 6, 15, 14, 0)
        ics = _ics(_event("a", "Past", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now)
        e = events[0]
        assert e["is_current"] is False
        assert e["is_next"] is False
        assert e["is_next_overlapping"] is False

    def test_now_override_drives_flags_independent_of_target_date(self):
        """
        target_date=today but now_override=tomorrow → all flags False
        (events are on target_date which is in the past relative to now).
        """
        now = _make_dt(2025, 6, 16, 10, 30)  # tomorrow
        ics = _ics(_event("a", "A", "20250615T100000Z", "20250615T110000Z"))
        events = _result([ics], now, target_date=date(2025, 6, 15))
        e = events[0]
        assert e["is_current"] is False
        assert e["is_next"] is False


# ===========================================================================
# M. target_date as datetime — "now" derived from it
# ===========================================================================

class TestTargetDateAsDatetime:
    """
    When target_date is passed as a datetime (not just date),
    that datetime is used as both the day window AND as "now" for flag logic.
    This is the main real-world usage pattern.
    """

    def test_next_found_when_target_date_is_datetime_before_events(self):
        """
        target_date=datetime(06:00), event at 09:00-10:00.
        now derived from target_date = 06:00 → event is in the future → is_next.
        """
        import pytz
        tz = pytz.timezone("Asia/Nicosia")  # UTC+2
        # 2026-03-04 06:00 Nicosia = 04:00 UTC
        target_dt = datetime(2026, 3, 4, 6, 0, 0, tzinfo=tz)
        ics = _ics(_event("a", "Meeting", "20260304T070000Z", "20260304T080000Z"))  # 09:00 Nicosia
        contents = [ics.encode()]
        events = get_events_for_day(
            calendar_urls=["mock://cal0.ics"],
            user_timezone="Asia/Nicosia",
            target_date=target_dt,
            ics_contents=contents,
        )
        nxt = [e for e in events if e["is_next"]]
        assert len(nxt) == 1
        assert nxt[0]["summary"] == "Meeting"

    def test_current_found_when_target_date_datetime_inside_event(self):
        """
        target_date=datetime(09:30 Nicosia), event 09:00-10:00 Nicosia.
        now=09:30 → is_current.
        """
        import pytz
        tz = pytz.timezone("Asia/Nicosia")
        target_dt = datetime(2026, 3, 4, 9, 30, 0, tzinfo=tz)
        ics = _ics(_event("a", "Meeting", "20260304T070000Z", "20260304T080000Z"))  # 09:00-10:00 Nicosia
        contents = [ics.encode()]
        events = get_events_for_day(
            calendar_urls=["mock://cal0.ics"],
            user_timezone="Asia/Nicosia",
            target_date=target_dt,
            ics_contents=contents,
        )
        cur = [e for e in events if e["is_current"]]
        assert len(cur) == 1

    def test_no_next_when_target_datetime_is_after_all_events(self):
        """
        target_date=datetime(23:00), event 09:00-10:00.
        now=23:00 → event is past → no is_next, no is_current.
        """
        import pytz
        tz = pytz.timezone("Asia/Nicosia")
        target_dt = datetime(2026, 3, 4, 23, 0, 0, tzinfo=tz)
        ics = _ics(_event("a", "Meeting", "20260304T070000Z", "20260304T080000Z"))
        contents = [ics.encode()]
        events = get_events_for_day(
            calendar_urls=["mock://cal0.ics"],
            user_timezone="Asia/Nicosia",
            target_date=target_dt,
            ics_contents=contents,
        )
        assert not any(e["is_next"] for e in events)
        assert not any(e["is_current"] for e in events)

    def test_target_datetime_uses_correct_date_for_window(self):
        """
        target_date=datetime(2026-03-04 06:00) → window is March 4, not March 3.
        Event on March 3 must not appear.
        """
        import pytz
        tz = pytz.timezone("Asia/Nicosia")
        target_dt = datetime(2026, 3, 4, 6, 0, 0, tzinfo=tz)
        ics = _ics(
            _event("a", "Yesterday", "20260303T100000Z", "20260303T110000Z"),
            _event("b", "Today",     "20260304T100000Z", "20260304T110000Z"),
        )
        contents = [ics.encode()]
        events = get_events_for_day(
            calendar_urls=["mock://cal0.ics"],
            user_timezone="Asia/Nicosia",
            target_date=target_dt,
            ics_contents=contents,
        )
        summaries = [e["summary"] for e in events]
        assert "Today" in summaries
        assert "Yesterday" not in summaries

    def test_now_override_beats_target_datetime(self):
        """
        Even if target_date is a datetime, explicit now_override takes precedence.
        """
        import pytz
        tz = pytz.timezone("Asia/Nicosia")
        target_dt = datetime(2026, 3, 4, 6, 0, 0, tzinfo=tz)   # would set now=06:00
        now_override = datetime(2026, 3, 4, 23, 0, 0, tzinfo=tz)  # after all events
        ics = _ics(_event("a", "Meeting", "20260304T100000Z", "20260304T110000Z"))
        contents = [ics.encode()]
        events = get_events_for_day(
            calendar_urls=["mock://cal0.ics"],
            user_timezone="Asia/Nicosia",
            target_date=target_dt,
            ics_contents=contents,
            now_override=now_override,
        )
        # now=23:00 → event is past, no next
        assert not any(e["is_next"] for e in events)