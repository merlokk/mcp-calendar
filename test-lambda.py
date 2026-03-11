"""
test-lambda.py
Tests for lambda_function.py
============================
Only tests lambda-level logic:
  - parameter parsing (env / querystring / body priority)
  - input validation (missing ICS_URLS, bad TZ, bad mode)
  - response shape (summary / full)
  - _parse_now, _minutes_until, _event_to_summary helpers
  - warm-container cache (_cache_get / _cache_set)
  - window metadata (start, end, tz)

get_events_for_day is always mocked — library behaviour is tested in icscal/tests.py.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
import types
from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytz

# ---------------------------------------------------------------------------
# Make sure the parent directory is on sys.path so we can import lambda_function
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Helpers to load / reload lambda_function cleanly between tests
# ---------------------------------------------------------------------------

def _load_handler():
    """Import (or re-import) lambda_function and return the module."""
    if "lambda_function" in sys.modules:
        del sys.modules["lambda_function"]
    import lambda_function as lh
    return lh


# ---------------------------------------------------------------------------
# Fake event data returned by the mocked library
# ---------------------------------------------------------------------------

def _fake_event(
    uid: str = "uid-1",
    summary: str = "Stand-up",
    start_iso: str = "2026-02-09T08:00:00+00:00",
    end_iso:   str = "2026-02-09T08:30:00+00:00",
    is_current: bool = False,
    is_next:    bool = False,
    is_next_overlapping: bool = False,
    location: str | None = None,
    organizer: str | None = None,
) -> dict:
    start_ms = int(datetime.fromisoformat(start_iso).timestamp() * 1000)
    end_ms   = int(datetime.fromisoformat(end_iso).timestamp()   * 1000)
    return {
        "uid": uid,
        "summary": summary,
        "location": location,
        "organizer": organizer,
        "start_iso": start_iso,
        "end_iso":   end_iso,
        "start_ms":  start_ms,
        "end_ms":    end_ms,
        "calendar_id":  0,
        "calendar_url": "mock://cal.ics",
        "is_current":   is_current,
        "is_next":      is_next,
        "is_next_overlapping": is_next_overlapping,
    }


FAKE_URL = "https://example.com/calendar.ics"
FAKE_NOW = "2026-02-09T08:00:00Z"


def _invoke(
    lh,
    *,
    env_ics_urls: str = FAKE_URL,
    qs: dict | None = None,
    body: dict | None = None,
    env_extras: dict | None = None,
) -> dict:
    """Set env vars and call handler(), return parsed body dict."""
    env = {"ICS_URLS": env_ics_urls}
    if env_extras:
        env.update(env_extras)

    event: dict = {}
    if qs:
        event["queryStringParameters"] = qs
    if body:
        event["body"] = json.dumps(body)

    with patch.dict(os.environ, env, clear=False):
        response = lh.handler(event, None)

    assert "statusCode" in response
    assert "body" in response
    return response


def _body(response: dict) -> dict:
    return json.loads(response["body"])


# ===========================================================================
# 1. Parameter sources and priority
# ===========================================================================

class TestParamSources:

    def test_ics_urls_from_env(self):
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            _invoke(lh, env_ics_urls=FAKE_URL,
                    qs={"override_now": FAKE_NOW, "mode": "summary"})
            called_urls = mock.call_args.kwargs["calendar_urls"]
            assert called_urls == [FAKE_URL]

    def test_multiple_ics_urls_space_separated(self):
        url2 = "https://example.com/cal2.ics"
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            _invoke(lh, env_ics_urls=f"{FAKE_URL} {url2}",
                    qs={"override_now": FAKE_NOW})
            called_urls = mock.call_args.kwargs["calendar_urls"]
            assert called_urls == [FAKE_URL, url2]

    def test_qs_overrides_env_ics_urls(self):
        qs_url = "https://qs.example.com/cal.ics"
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            _invoke(lh, env_ics_urls=FAKE_URL,
                    qs={"ics_urls": qs_url, "override_now": FAKE_NOW})
            called_urls = mock.call_args.kwargs["calendar_urls"]
            assert called_urls == [qs_url]

    def test_body_overrides_qs_ics_urls(self):
        qs_url   = "https://qs.example.com/cal.ics"
        body_url = "https://body.example.com/cal.ics"
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            _invoke(lh, env_ics_urls=FAKE_URL,
                    qs={"ics_urls": qs_url, "override_now": FAKE_NOW},
                    body={"ics_urls": body_url})
            called_urls = mock.call_args.kwargs["calendar_urls"]
            assert called_urls == [body_url]

    def test_tz_from_env(self):
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            _invoke(lh, qs={"override_now": FAKE_NOW},
                    env_extras={"TZ": "Asia/Nicosia"})
            assert mock.call_args.kwargs["user_timezone"] == "Asia/Nicosia"

    def test_tz_from_qs_overrides_env(self):
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            _invoke(lh, qs={"tz": "America/New_York", "override_now": FAKE_NOW},
                    env_extras={"TZ": "Asia/Nicosia"})
            assert mock.call_args.kwargs["user_timezone"] == "America/New_York"

    def test_override_now_from_env(self):
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            _invoke(lh, env_extras={"OVERRIDE_NOW": FAKE_NOW})
            kw = mock.call_args.kwargs
            assert kw["now_override"] is not None

    def test_override_now_from_qs_overrides_env(self):
        qs_now = "2026-03-01T10:00:00Z"
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            _invoke(lh, qs={"override_now": qs_now},
                    env_extras={"OVERRIDE_NOW": FAKE_NOW})
            kw = mock.call_args.kwargs
            # qs wins: 2026-03-01
            assert kw["now_override"].year == 2026
            assert kw["now_override"].month == 3

    def test_mode_from_qs(self):
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]):
            resp = _invoke(lh, qs={"override_now": FAKE_NOW, "mode": "full"})
            b = _body(resp)
            assert "events" in b  # full mode key

    def test_invalid_mode_falls_back_to_summary(self):
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]):
            resp = _invoke(lh, qs={"override_now": FAKE_NOW, "mode": "bogus"})
            b = _body(resp)
            # summary mode keys present
            assert "current" in b
            assert "next" in b

    def test_body_json_parsed(self):
        """JSON body is parsed and its keys override env/qs."""
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            _invoke(lh, body={"ics_urls": FAKE_URL, "override_now": FAKE_NOW})
            assert mock.called

    def test_malformed_body_ignored(self):
        """Non-JSON body must not crash the handler."""
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]):
            event = {"body": "not json at all",
                     "queryStringParameters": {"override_now": FAKE_NOW}}
            with patch.dict(os.environ, {"ICS_URLS": FAKE_URL}):
                resp = lh.handler(event, None)
            assert resp["statusCode"] == 200


# ===========================================================================
# 2. Input validation
# ===========================================================================

class TestValidation:

    def test_missing_ics_urls_returns_400(self):
        lh = _load_handler()
        with patch.dict(os.environ, {"ICS_URLS": ""}, clear=False):
            resp = lh.handler({}, None)
        assert resp["statusCode"] == 400
        assert "ICS_URLS" in _body(resp)["error"]

    def test_unknown_timezone_returns_400(self):
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]):
            resp = _invoke(lh, qs={"tz": "Mars/Olympus", "override_now": FAKE_NOW})
        assert resp["statusCode"] == 400
        assert "timezone" in _body(resp)["error"].lower()

    def test_library_exception_returns_502(self):
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", side_effect=RuntimeError("network down")):
            resp = _invoke(lh, qs={"override_now": FAKE_NOW})
        assert resp["statusCode"] == 502
        assert "detail" in _body(resp)

    def test_default_tz_is_europe_nicosia(self):
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            with patch.dict(os.environ, {"ICS_URLS": FAKE_URL, "TZ": ""}, clear=False):
                lh.handler({"queryStringParameters": {"override_now": FAKE_NOW}}, None)
            assert mock.call_args.kwargs["user_timezone"] == "Europe/Nicosia"


# ===========================================================================
# 3. Response shape — summary mode
# ===========================================================================

class TestSummaryShape:

    def _call_summary(self, lh, events):
        with patch.object(lh, "get_events_for_day", return_value=events):
            resp = _invoke(lh, qs={"override_now": FAKE_NOW, "mode": "summary"})
        assert resp["statusCode"] == 200
        return _body(resp)

    def test_required_top_level_keys(self):
        lh = _load_handler()
        b = self._call_summary(lh, [])
        for key in ("generatedAt", "window", "now", "minutesUntilNext",
                    "isOverlappingNow", "current", "next", "nextOverlapping"):
            assert key in b, f"missing key: {key}"

    def test_window_has_start_end_tz(self):
        lh = _load_handler()
        b = self._call_summary(lh, [])
        w = b["window"]
        assert "start" in w and "end" in w and "tz" in w

    def test_window_tz_matches_requested(self):
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]):
            resp = _invoke(lh, qs={"override_now": FAKE_NOW,
                                   "tz": "America/New_York"})
        assert _body(resp)["window"]["tz"] == "America/New_York"

    def test_all_null_when_no_events(self):
        lh = _load_handler()
        b = self._call_summary(lh, [])
        assert b["current"] is None
        assert b["next"] is None
        assert b["nextOverlapping"] is None
        assert b["minutesUntilNext"] is None
        assert b["isOverlappingNow"] is False

    def test_current_event_populated(self):
        lh = _load_handler()
        ev = _fake_event(summary="Stand-up",
                         start_iso="2026-02-09T08:00:00+00:00",
                         end_iso="2026-02-09T08:30:00+00:00",
                         is_current=True)
        b = self._call_summary(lh, [ev])
        assert b["current"] is not None
        assert b["current"]["title"] == "Stand-up"

    def test_current_event_shape(self):
        """current block must have uid, title, location, organizer, start, end."""
        lh = _load_handler()
        ev = _fake_event(is_current=True, location="Room 1", organizer="boss@x.com")
        b = self._call_summary(lh, [ev])
        cur = b["current"]
        for key in ("uid", "title", "location", "organizer", "start", "end"):
            assert key in cur, f"missing key in current: {key}"

    def test_next_event_populated(self):
        lh = _load_handler()
        nxt = _fake_event(uid="uid-2", summary="Zoom",
                          start_iso="2026-02-09T10:00:00+00:00",
                          end_iso="2026-02-09T11:00:00+00:00",
                          is_next=True)
        b = self._call_summary(lh, [nxt])
        assert b["next"]["title"] == "Zoom"

    def test_minutes_until_next_calculated(self):
        """now=08:00Z, next starts 10:00Z → 120 minutes."""
        lh = _load_handler()
        nxt = _fake_event(uid="uid-2", summary="Later",
                          start_iso="2026-02-09T10:00:00+00:00",
                          end_iso="2026-02-09T11:00:00+00:00",
                          is_next=True)
        b = self._call_summary(lh, [nxt])
        assert b["minutesUntilNext"] == 120

    def test_minutes_until_next_is_zero_when_starting_now(self):
        """next starts at exactly now → 0 minutes."""
        lh = _load_handler()
        nxt = _fake_event(uid="uid-2", summary="Now",
                          start_iso="2026-02-09T08:00:00+00:00",
                          end_iso="2026-02-09T08:30:00+00:00",
                          is_next=True)
        b = self._call_summary(lh, [nxt])
        assert b["minutesUntilNext"] == 0

    def test_is_overlapping_now_true(self):
        lh = _load_handler()
        ov = _fake_event(uid="uid-ov", summary="Overlap",
                         is_next_overlapping=True)
        b = self._call_summary(lh, [ov])
        assert b["isOverlappingNow"] is True
        assert b["nextOverlapping"]["title"] == "Overlap"

    def test_is_overlapping_now_false_when_absent(self):
        lh = _load_handler()
        b = self._call_summary(lh, [_fake_event(is_next=True)])
        assert b["isOverlappingNow"] is False
        assert b["nextOverlapping"] is None

    def test_start_end_converted_to_local_tz(self):
        """start/end in summary must be in the requested local timezone, not UTC."""
        lh = _load_handler()
        # Event at 08:00 UTC; Nicosia = UTC+2 → should show 10:00+02:00
        ev = _fake_event(is_current=True,
                         start_iso="2026-02-09T08:00:00+00:00",
                         end_iso="2026-02-09T09:00:00+00:00")
        with patch.object(lh, "get_events_for_day", return_value=[ev]):
            resp = _invoke(lh, qs={"override_now": FAKE_NOW,
                                   "tz": "Asia/Nicosia"})
        cur = _body(resp)["current"]
        # +02:00 offset expected
        assert "+02:00" in cur["start"]

    def test_generated_at_is_utc_iso(self):
        lh = _load_handler()
        b = self._call_summary(lh, [])
        # Should end with Z
        assert b["generatedAt"].endswith("Z")

    def test_now_field_is_local_time(self):
        """'now' field must be in the local timezone."""
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]):
            resp = _invoke(lh, qs={"override_now": FAKE_NOW, "tz": "Asia/Nicosia"})
        now_str = _body(resp)["now"]
        assert "+02:00" in now_str


# ===========================================================================
# 4. Response shape — full mode
# ===========================================================================

class TestFullShape:

    def test_full_mode_has_events_key(self):
        lh = _load_handler()
        ev = _fake_event()
        with patch.object(lh, "get_events_for_day", return_value=[ev]):
            resp = _invoke(lh, qs={"override_now": FAKE_NOW, "mode": "full"})
        b = _body(resp)
        assert "events" in b
        assert isinstance(b["events"], list)

    def test_full_mode_events_passthrough(self):
        """Full mode returns library events as-is (no reshaping)."""
        lh = _load_handler()
        ev = _fake_event(summary="Raw Event")
        with patch.object(lh, "get_events_for_day", return_value=[ev]):
            resp = _invoke(lh, qs={"override_now": FAKE_NOW, "mode": "full"})
        events = _body(resp)["events"]
        assert events[0]["summary"] == "Raw Event"

    def test_full_mode_no_summary_keys(self):
        """Full mode must not have current/next/minutesUntilNext."""
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]):
            resp = _invoke(lh, qs={"override_now": FAKE_NOW, "mode": "full"})
        b = _body(resp)
        assert "current" not in b
        assert "next" not in b
        assert "minutesUntilNext" not in b

    def test_full_mode_has_window_and_now(self):
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]):
            resp = _invoke(lh, qs={"override_now": FAKE_NOW, "mode": "full"})
        b = _body(resp)
        assert "window" in b
        assert "now" in b


# ===========================================================================
# 5. Warm-container cache
# ===========================================================================

class TestCache:

    def setup_method(self):
        """Clear the module-level cache before each test."""
        lh = _load_handler()
        lh._cache.clear()

    def test_second_call_uses_cache(self):
        """get_events_for_day called once; second identical request uses cache."""
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            _invoke(lh, qs={"override_now": FAKE_NOW})
            _invoke(lh, qs={"override_now": FAKE_NOW})
            assert mock.call_count == 1

    def test_different_date_bypasses_cache(self):
        """Different day → different cache key → library called again."""
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            _invoke(lh, qs={"override_now": "2026-02-09T08:00:00Z"})
            _invoke(lh, qs={"override_now": "2026-02-10T08:00:00Z"})
            assert mock.call_count == 2

    def test_different_tz_bypasses_cache(self):
        """Different timezone can yield different date → cache miss."""
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            _invoke(lh, qs={"override_now": FAKE_NOW, "tz": "UTC"})
            _invoke(lh, qs={"override_now": FAKE_NOW, "tz": "Pacific/Auckland"})
            assert mock.call_count == 2

    def test_expired_cache_refetches(self):
        """Entry older than TTL → library called again."""
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            _invoke(lh, env_extras={"CACHE_MS": "1"},
                    qs={"override_now": FAKE_NOW})
            time.sleep(0.002)  # let 1 ms TTL expire
            _invoke(lh, env_extras={"CACHE_MS": "1"},
                    qs={"override_now": FAKE_NOW})
            assert mock.call_count == 2

    def test_cache_zero_ms_always_refetches(self):
        """CACHE_MS=0 → always fetch fresh."""
        lh = _load_handler()
        with patch.object(lh, "get_events_for_day", return_value=[]) as mock:
            _invoke(lh, env_extras={"CACHE_MS": "0"},
                    qs={"override_now": FAKE_NOW})
            _invoke(lh, env_extras={"CACHE_MS": "0"},
                    qs={"override_now": FAKE_NOW})
            assert mock.call_count == 2


# ===========================================================================
# 6. _parse_now helper
# ===========================================================================

class TestParseNow:

    def setup_method(self):
        self.lh = _load_handler()

    def test_z_suffix(self):
        dt = self.lh._parse_now("2026-02-09T08:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.year == 2026 and dt.hour == 8

    def test_plus_offset(self):
        dt = self.lh._parse_now("2026-02-09T10:00:00+02:00")
        assert dt.utcoffset().total_seconds() == 7200

    def test_naive_gets_utc(self):
        dt = self.lh._parse_now("2026-02-09T08:00:00")
        assert dt.tzinfo == timezone.utc

    def test_none_returns_none(self):
        assert self.lh._parse_now(None) is None

    def test_empty_string_returns_none(self):
        assert self.lh._parse_now("") is None

    def test_invalid_string_returns_none(self):
        assert self.lh._parse_now("not-a-date") is None


# ===========================================================================
# 7. _minutes_until helper
# ===========================================================================

class TestMinutesUntil:

    def setup_method(self):
        self.lh = _load_handler()

    def test_120_minutes(self):
        now = datetime(2026, 2, 9, 8, 0, tzinfo=timezone.utc)
        ev  = _fake_event(start_iso="2026-02-09T10:00:00+00:00")
        assert self.lh._minutes_until(now, ev) == 120

    def test_zero_when_starting_now(self):
        now = datetime(2026, 2, 9, 8, 0, tzinfo=timezone.utc)
        ev  = _fake_event(start_iso="2026-02-09T08:00:00+00:00")
        assert self.lh._minutes_until(now, ev) == 0

    def test_zero_when_already_started(self):
        """Event started in the past → clamp to 0, not negative."""
        now = datetime(2026, 2, 9, 9, 0, tzinfo=timezone.utc)
        ev  = _fake_event(start_iso="2026-02-09T08:00:00+00:00")
        assert self.lh._minutes_until(now, ev) == 0

    def test_none_event_returns_none(self):
        now = datetime(2026, 2, 9, 8, 0, tzinfo=timezone.utc)
        assert self.lh._minutes_until(now, None) is None


# ===========================================================================
# 8. _event_to_summary helper
# ===========================================================================

class TestEventToSummary:

    def setup_method(self):
        self.lh = _load_handler()
        self.tz = pytz.timezone("Asia/Nicosia")  # UTC+2

    def test_fields_present(self):
        ev = _fake_event(location="Room A", organizer="x@corp.com")
        s = self.lh._event_to_summary(ev, self.tz)
        for key in ("uid", "title", "location", "organizer", "start", "end"):
            assert key in s

    def test_summary_becomes_title(self):
        ev = _fake_event(summary="My Meeting")
        s = self.lh._event_to_summary(ev, self.tz)
        assert s["title"] == "My Meeting"

    def test_start_end_converted_to_tz(self):
        """UTC+0 start must appear as UTC+2 in Nicosia output."""
        ev = _fake_event(start_iso="2026-02-09T08:00:00+00:00",
                         end_iso="2026-02-09T09:00:00+00:00")
        s = self.lh._event_to_summary(ev, self.tz)
        assert "+02:00" in s["start"]
        assert "+02:00" in s["end"]

    def test_location_none_passthrough(self):
        ev = _fake_event(location=None)
        assert self.lh._event_to_summary(ev, self.tz)["location"] is None

    def test_organizer_none_passthrough(self):
        ev = _fake_event(organizer=None)
        assert self.lh._event_to_summary(ev, self.tz)["organizer"] is None
