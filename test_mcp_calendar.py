from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import mcp_calendar as srv
from fastmcp import Client


def _clockify_event(uid: str, summary: str, start_iso: str, end_iso: str) -> dict:
    return {
        "uid": uid,
        "summary": summary,
        "location": None,
        "organizer": "u@example.com",
        "start_iso": start_iso,
        "end_iso": end_iso,
        "start_ms": 0,
        "end_ms": 0,
        "calendar_id": "ws-1",
        "calendar_url": "https://api.clockify.me/api/v1/workspaces/ws-1/user/u-1/time-entries",
        "is_current": False,
        "is_next": False,
        "is_next_overlapping": False,
    }


def test_get_clockify_tasks_returns_formatted_tasks(monkeypatch):
    srv._cache.clear()

    def fake_clockify_loader(**kwargs):
        assert kwargs["api_key"] == "key-1"
        assert kwargs["user_timezone"] == "UTC"
        return [
            _clockify_event("te-1", "Task A", "2026-03-06T10:00:00+00:00", "2026-03-06T11:00:00+00:00"),
            _clockify_event("te-2", "Task B", "2026-03-06T12:00:00+00:00", "2026-03-06T13:00:00+00:00"),
        ]

    monkeypatch.setattr(srv, "get_clockify_events_for_day", fake_clockify_loader)

    with patch.dict(
        "os.environ",
        {
            "CLOCKIFY_API_KEY": "key-1",
            "TZ": "UTC",
        },
        clear=False,
    ):
        data = srv.get_clockify_tasks(date_str="2026-03-06", override_now="2026-03-06T08:00:00Z")

    assert data["source"] == "clockify"
    assert data["count"] == 2
    assert data["tasks"][0]["title"] == "Task A"
    assert data["tasks"][0]["start"].endswith("+00:00")


def test_get_clockify_tasks_requires_api_key(monkeypatch):
    srv._cache.clear()
    monkeypatch.setattr(srv, "get_clockify_events_for_day", lambda **kwargs: [])

    with patch.dict(
        "os.environ",
        {
            "CLOCKIFY_API_KEY": "",
            "TZ": "UTC",
        },
        clear=False,
    ):
        try:
            srv.get_clockify_tasks(date_str="2026-03-06", override_now="2026-03-06T08:00:00Z")
            assert False, "Expected ValueError"
        except ValueError as exc:
            assert "CLOCKIFY_API_KEY" in str(exc)


def test_get_clockify_tasks_uses_cache(monkeypatch):
    srv._cache.clear()
    calls = {"n": 0}

    def fake_clockify_loader(**kwargs):
        calls["n"] += 1
        return [
            _clockify_event("te-1", "Task A", "2026-03-06T10:00:00+00:00", "2026-03-06T11:00:00+00:00"),
        ]

    monkeypatch.setattr(srv, "get_clockify_events_for_day", fake_clockify_loader)

    with patch.dict(
        "os.environ",
        {
            "CLOCKIFY_API_KEY": "key-1",
            "TZ": "UTC",
            "CACHE_MS": "60000",
        },
        clear=False,
    ):
        srv.get_clockify_tasks(date_str="2026-03-06", override_now="2026-03-06T08:00:00Z")
        srv.get_clockify_tasks(date_str="2026-03-06", override_now="2026-03-06T08:05:00Z")

    assert calls["n"] == 1


def test_get_clockify_free_slots_returns_local_slots(monkeypatch):
    srv._cache.clear()

    def fake_slots_loader(**kwargs):
        assert kwargs["api_key"] == "key-1"
        return [
            {
                "start_iso": "2026-03-06T10:00:00+00:00",
                "end_iso": "2026-03-06T11:00:00+00:00",
                "duration_min": 60,
            }
        ]

    monkeypatch.setattr(srv, "get_clockify_free_slots_for_day", fake_slots_loader)

    with patch.dict(
        "os.environ",
        {
            "CLOCKIFY_API_KEY": "key-1",
            "TZ": "Europe/Kyiv",
        },
        clear=False,
    ):
        data = srv.get_clockify_free_slots(date_str="2026-03-06", override_now="2026-03-06T08:00:00Z")

    assert data["source"] == "clockify"
    assert data["count"] == 1
    assert data["totalFreeMin"] == 60
    assert data["freeSlots"][0]["start"].endswith("+02:00")


def test_get_clockify_free_slots_requires_api_key(monkeypatch):
    srv._cache.clear()
    monkeypatch.setattr(srv, "get_clockify_free_slots_for_day", lambda **kwargs: [])

    with patch.dict(
        "os.environ",
        {
            "CLOCKIFY_API_KEY": "",
            "TZ": "UTC",
        },
        clear=False,
    ):
        try:
            srv.get_clockify_free_slots(date_str="2026-03-06", override_now="2026-03-06T08:00:00Z")
            assert False, "Expected ValueError"
        except ValueError as exc:
            assert "CLOCKIFY_API_KEY" in str(exc)


def test_get_clockify_free_slots_uses_cache(monkeypatch):
    srv._cache.clear()
    calls = {"n": 0}

    def fake_slots_loader(**kwargs):
        calls["n"] += 1
        return [
            {
                "start_iso": "2026-03-06T10:00:00+00:00",
                "end_iso": "2026-03-06T11:00:00+00:00",
                "duration_min": 60,
            }
        ]

    monkeypatch.setattr(srv, "get_clockify_free_slots_for_day", fake_slots_loader)

    with patch.dict(
        "os.environ",
        {
            "CLOCKIFY_API_KEY": "key-1",
            "TZ": "UTC",
            "CACHE_MS": "60000",
        },
        clear=False,
    ):
        srv.get_clockify_free_slots(date_str="2026-03-06", override_now="2026-03-06T08:00:00Z")
        srv.get_clockify_free_slots(date_str="2026-03-06", override_now="2026-03-06T08:05:00Z")

    assert calls["n"] == 1


def test_get_server_overview_contains_purpose_and_tool_params():
    data = srv.get_server_overview()

    assert data["name"] == "calendar"
    assert data["dayBased"] is True
    assert "ICS" in data["purpose"]
    assert "Clockify" in data["purpose"]
    assert "target day" in data["primaryWorkflow"]
    assert isinstance(data["tools"], list) and len(data["tools"]) >= 6

    names = [tool["name"] for tool in data["tools"]]
    assert "get_server_overview" in names
    assert "get_day" in names
    assert "get_clockify_tasks" in names
    assert "get_clockify_free_slots" in names

    day_tool = next(tool for tool in data["tools"] if tool["name"] == "get_day")
    assert any(param["name"] == "date_str" for param in day_tool["params"])


def test_mcp_stdio_transport_lists_tools_and_calls_tool():
    async def _run() -> None:
        server_path = Path(__file__).with_name("mcp_calendar.py")
        async with Client(server_path, timeout=20) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools}
            assert {
                "get_now",
                "get_day",
                "get_free_slots",
                "get_clockify_tasks",
                "get_clockify_free_slots",
                "get_server_overview",
            }.issubset(names)

            result = await client.call_tool("get_server_overview", {})
            assert result.is_error is False
            assert result.data["name"] == "calendar"
            assert result.data["dayBased"] is True

    asyncio.run(_run())
