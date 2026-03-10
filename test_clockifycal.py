from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from clockifycal.client import get_workspace_users
from clockifycal.loader import (
    LUNCH_BREAK_MINUTES,
    LUNCH_WINDOW_START_HHMM,
    MAX_FREE_SLOT_MINUTES,
    WORKDAY_END_HHMM,
    WORKDAY_START_HHMM,
    get_employee_events_for_day,
    get_events_for_day,
    get_free_slots_for_day,
    get_project_names_for_day,
    get_workspace_users_for_workspace,
)


def _hhmm_to_iso(day: str, hhmm: str) -> str:
    return f"{day}T{hhmm}:00+00:00"


def _write_local_employees_file(contents: str) -> Path:
    root = Path(".pytest-local")
    root.mkdir(exist_ok=True)
    path = root / f"employees-{uuid4().hex}.json"
    path.write_text(contents, encoding="utf-8")
    return path


def test_loader_fetches_user_then_time_entries_and_transforms(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_get_user(*, api_key, base_url, timeout):
        calls.append(("user", {"api_key": api_key, "base_url": base_url, "timeout": timeout}))
        return {"id": "user-1", "defaultWorkspace": "ws-1", "email": "u@example.com"}

    def fake_get_entries(*, api_key, workspace_id, user_id, start, end, base_url, timeout):
        calls.append(
            (
                "entries",
                {
                    "api_key": api_key,
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "start": start,
                    "end": end,
                    "base_url": base_url,
                    "timeout": timeout,
                },
            )
        )
        return [
            {
                "id": "te-1",
                "description": "Deep work",
                "timeInterval": {
                    "start": "2026-03-06T10:00:00Z",
                    "end": "2026-03-06T11:00:00Z",
                },
            }
        ]

    monkeypatch.setattr("clockifycal.loader.get_current_user", fake_get_user)
    monkeypatch.setattr("clockifycal.loader.get_time_entries", fake_get_entries)

    events = get_events_for_day(
        api_key="token",
        user_timezone="UTC",
        target_date=datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc),
        now_override="2026-03-06T10:30:00Z",
        base_url="https://api.clockify.me/api",
        timeout=9,
    )

    assert [name for name, _ in calls] == ["user", "entries"]
    assert calls[1][1]["workspace_id"] == "ws-1"
    assert calls[1][1]["user_id"] == "user-1"

    assert len(events) == 1
    assert events[0]["uid"] == "te-1"
    assert events[0]["summary"] == "Deep work"
    assert events[0]["organizer"] == "u@example.com"
    assert events[0]["calendar_id"] == "ws-1"
    assert events[0]["is_current"] is True
    assert events[0]["is_next"] is False


def test_loader_sets_current_next_and_overlapping_flags():
    entries = [
        {
            "id": "te-1",
            "description": "Current",
            "timeInterval": {"start": "2026-03-06T10:00:00Z", "end": "2026-03-06T11:00:00Z"},
        },
        {
            "id": "te-2",
            "description": "Overlap",
            "timeInterval": {"start": "2026-03-06T10:30:00Z", "end": "2026-03-06T11:10:00Z"},
        },
        {
            "id": "te-3",
            "description": "Next",
            "timeInterval": {"start": "2026-03-06T11:15:00Z", "end": "2026-03-06T11:45:00Z"},
        },
    ]

    events = get_events_for_day(
        api_key="token",
        user_payload={"id": "u1", "defaultWorkspace": "w1"},
        time_entries_payload=entries,
        target_date=datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc),
        now_override="2026-03-06T10:35:00Z",
    )

    assert len(events) == 3
    current = [ev for ev in events if ev["is_current"]]
    nxt = [ev for ev in events if ev["is_next"]]
    ov = [ev for ev in events if ev["is_next_overlapping"]]
    assert len(current) == 1 and current[0]["uid"] == "te-1"
    assert len(ov) == 1 and ov[0]["uid"] == "te-2"
    assert len(nxt) == 1 and nxt[0]["uid"] == "te-3"


def test_loader_filters_entries_outside_target_day():
    entries = [
        {
            "id": "te-1",
            "description": "In day",
            "timeInterval": {"start": "2026-03-06T22:00:00Z", "end": "2026-03-06T23:00:00Z"},
        },
        {
            "id": "te-2",
            "description": "Out of day",
            "timeInterval": {"start": "2026-03-07T00:00:00Z", "end": "2026-03-07T01:00:00Z"},
        },
    ]

    events = get_events_for_day(
        api_key="token",
        user_payload={"id": "u1", "defaultWorkspace": "w1"},
        time_entries_payload=entries,
        user_timezone="UTC",
        target_date=datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc),
        now_override="2026-03-06T21:00:00Z",
    )

    assert [ev["uid"] for ev in events] == ["te-1"]


def test_cli_returns_error_when_api_key_missing(capsys):
    from clockifycal.cli import main

    exit_code = main(["--date", "2026-03-06"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "CLOCKIFY_API_KEY" in captured.err


def test_cli_prints_json(monkeypatch, capsys):
    from clockifycal.cli import main

    def fake_loader(**kwargs):
        assert kwargs["api_key"] == "key-1"
        return [{"uid": "te-1", "summary": "Task"}]

    monkeypatch.setattr("clockifycal.cli.get_events_for_day", fake_loader)

    exit_code = main(["--api-key", "key-1", "--date", "2026-03-06", "--pretty"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"uid": "te-1"' in captured.out


def test_cli_prints_free_slots(monkeypatch, capsys):
    from clockifycal.cli import main

    def fake_slots_loader(**kwargs):
        assert kwargs["api_key"] == "key-1"
        return [{"start_iso": "2026-03-06T10:00:00+00:00", "end_iso": "2026-03-06T11:00:00+00:00", "duration_min": 60}]

    monkeypatch.setattr("clockifycal.cli.get_free_slots_for_day", fake_slots_loader)

    exit_code = main(["--api-key", "key-1", "--date", "2026-03-06", "--free-slots", "--pretty"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"duration_min": 60' in captured.out


def test_cli_list_renders_in_requested_timezone(monkeypatch, capsys):
    from clockifycal.cli import main

    def fake_loader(**kwargs):
        return [
            {
                "uid": "te-1",
                "summary": "Task",
                "start_iso": "2026-03-06T08:00:00+00:00",
                "end_iso": "2026-03-06T09:00:00+00:00",
                "is_current": False,
                "is_next": True,
                "is_next_overlapping": False,
            }
        ]

    monkeypatch.setattr("clockifycal.cli.get_events_for_day", fake_loader)
    monkeypatch.setattr("clockifycal.cli.get_project_names_for_day", lambda **kwargs: [])

    exit_code = main(["--api-key", "key-1", "--tz", "Europe/Kyiv", "--date", "2026-03-06", "--list"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "2026-03-06T10:00:00+02:00" in captured.out
    assert "2026-03-06T11:00:00+02:00" in captured.out


def test_cli_list_prints_project_name(monkeypatch, capsys):
    from clockifycal.cli import main

    def fake_loader(**kwargs):
        return [
            {
                "uid": "te-1",
                "summary": "Task",
                "project_id": "p-1",
                "start_iso": "2026-03-06T08:00:00+00:00",
                "end_iso": "2026-03-06T09:00:00+00:00",
                "is_current": False,
                "is_next": True,
                "is_next_overlapping": False,
            }
        ]

    def fake_projects_loader(**kwargs):
        return [{"project_id": "p-1", "project_name": "Internal"}]

    monkeypatch.setattr("clockifycal.cli.get_events_for_day", fake_loader)
    monkeypatch.setattr("clockifycal.cli.get_project_names_for_day", fake_projects_loader)

    exit_code = main(["--api-key", "key-1", "--tz", "Europe/Kyiv", "--date", "2026-03-06", "--list"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert " | Internal" in captured.out


def test_cli_prints_project_names(monkeypatch, capsys):
    from clockifycal.cli import main

    def fake_projects_loader(**kwargs):
        assert kwargs["api_key"] == "key-1"
        return [{"project_id": "p-1", "project_name": "Internal"}]

    monkeypatch.setattr("clockifycal.cli.get_project_names_for_day", fake_projects_loader)

    exit_code = main(["--api-key", "key-1", "--date", "2026-03-06", "--project-names", "--list"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Internal (p-1)" in captured.out


def test_cli_prints_workspace_users(monkeypatch, capsys):
    from clockifycal.cli import main

    def fake_workspace_users_loader(**kwargs):
        assert kwargs["api_key"] == "key-1"
        return [
            {
                "user_id": "u-1",
                "name": "Alice Johnson",
                "email": "alice@example.com",
                "active": True,
                "workspace_id": "w1",
            }
        ]

    monkeypatch.setattr("clockifycal.cli.get_workspace_users_for_workspace", fake_workspace_users_loader)

    exit_code = main(["--api-key", "key-1", "--workspace-users", "--list"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Alice Johnson (alice@example.com) [active] (u-1)" in captured.out


def test_cli_prints_employee_tasks_from_file(monkeypatch, capsys):
    from clockifycal.cli import main

    employees_file = _write_local_employees_file('{"employees":["ali"]}')

    def fake_employee_loader(**kwargs):
        assert kwargs["employee_names"] == ["ali"]
        return [
            {
                "uid": "te-1",
                "summary": "Task",
                "employee_name": "Alice Johnson",
                "project_name": "Internal",
                "start_iso": "2026-03-06T08:00:00+00:00",
                "end_iso": "2026-03-06T09:00:00+00:00",
                "is_current": False,
                "is_next": False,
                "is_next_overlapping": False,
            }
        ]

    monkeypatch.setattr("clockifycal.cli.get_employee_events_for_day", fake_employee_loader)

    try:
        exit_code = main(
            [
                "--api-key",
                "key-1",
                "--tz",
                "Europe/Kyiv",
                "--date",
                "2026-03-06",
                "--employees-tasks",
                "--employees-file",
                str(employees_file),
                "--list",
            ]
        )
        captured = capsys.readouterr()

        assert exit_code == 0
        assert "Alice Johnson | Task | Internal" in captured.out
    finally:
        employees_file.unlink(missing_ok=True)


def test_loader_raises_if_workspace_not_found():
    with pytest.raises(ValueError):
        get_events_for_day(
            api_key="token",
            user_payload={"id": "u1"},
            time_entries_payload=[],
        )


def test_project_names_for_day_resolves_project_names(monkeypatch):
    entries = [
        {
            "id": "te-1",
            "description": "A",
            "projectId": "p-1",
            "timeInterval": {"start": "2026-03-06T10:00:00Z", "end": "2026-03-06T11:00:00Z"},
        },
        {
            "id": "te-2",
            "description": "B",
            "projectId": "p-2",
            "timeInterval": {"start": "2026-03-06T11:00:00Z", "end": "2026-03-06T12:00:00Z"},
        },
    ]
    calls: list[str] = []

    def fake_get_project(*, api_key, workspace_id, project_id, base_url, timeout):
        calls.append(project_id)
        return {"id": project_id, "name": f"Project {project_id}"}

    monkeypatch.setattr("clockifycal.loader.get_project", fake_get_project)

    projects = get_project_names_for_day(
        api_key="token",
        user_timezone="UTC",
        target_date=datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc),
        now_override="2026-03-06T12:00:00Z",
        workspace_id="w1",
        user_payload={"id": "u1", "defaultWorkspace": "w1"},
        time_entries_payload=entries,
    )

    assert calls == ["p-1", "p-2"]
    assert projects == [
        {"project_id": "p-1", "project_name": "Project p-1"},
        {"project_id": "p-2", "project_name": "Project p-2"},
    ]


def test_employee_events_for_day_resolves_partial_names_and_adds_project_name():
    workspace_users = [
        {"id": "u-1", "name": "Alice Johnson", "email": "alice@example.com"},
        {"id": "u-2", "name": "Bob Smith", "email": "bob@example.com"},
    ]
    entries_by_user = {
        "u-1": [
            {
                "id": "te-1",
                "description": "Alice task",
                "projectId": "p-1",
                "timeInterval": {"start": "2026-03-06T10:00:00Z", "end": "2026-03-06T11:00:00Z"},
            }
        ],
        "u-2": [
            {
                "id": "te-2",
                "description": "Bob task",
                "projectId": "p-2",
                "timeInterval": {"start": "2026-03-06T11:00:00Z", "end": "2026-03-06T12:00:00Z"},
            }
        ],
    }
    project_payloads = {
        "p-1": {"id": "p-1", "name": "Project One"},
        "p-2": {"id": "p-2", "name": "Project Two"},
    }

    events = get_employee_events_for_day(
        api_key="token",
        employee_names=["ali", "bo"],
        user_timezone="UTC",
        target_date=datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc),
        now_override="2026-03-06T09:00:00Z",
        workspace_id="w1",
        user_payload={"id": "owner", "defaultWorkspace": "w1", "email": "owner@example.com"},
        workspace_users_payload=workspace_users,
        time_entries_payload_by_user=entries_by_user,
        project_payloads=project_payloads,
    )

    assert [event["employee_name"] for event in events] == ["Alice Johnson", "Bob Smith"]
    assert [event["project_name"] for event in events] == ["Project One", "Project Two"]


def test_workspace_users_for_workspace_resolves_owner_workspace():
    users = get_workspace_users_for_workspace(
        api_key="token",
        user_payload={"id": "owner", "defaultWorkspace": "w1"},
        workspace_users_payload=[
            {"id": "u-2", "name": "Bob", "email": "bob@example.com", "active": False},
            {"id": "u-1", "name": "Alice", "email": "alice@example.com", "active": True},
        ],
    )

    assert [u["user_id"] for u in users] == ["u-1", "u-2"]
    assert users[0]["workspace_id"] == "w1"
    assert users[1]["active"] is False


def test_free_slots_split_by_max_one_hour():
    day = "2026-03-06"
    entries = [
        {
            "id": "te-1",
            "description": "Morning",
            "timeInterval": {"start": f"{day}T09:00:00Z", "end": _hhmm_to_iso(day, WORKDAY_START_HHMM).replace("+00:00", "Z")},
        },
        {
            "id": "te-2",
            "description": "Late",
            "timeInterval": {"start": f"{day}T12:30:00Z", "end": _hhmm_to_iso(day, WORKDAY_END_HHMM).replace("+00:00", "Z")},
        },
    ]

    slots = get_free_slots_for_day(
        api_key="token",
        user_timezone="UTC",
        target_date=datetime(2026, 3, 6, 0, 0, tzinfo=timezone.utc),
        now_override="2026-03-06T08:00:00Z",
        user_payload={"id": "u1", "defaultWorkspace": "w1"},
        time_entries_payload=entries,
    )

    assert [s["duration_min"] for s in slots] == [MAX_FREE_SLOT_MINUTES, MAX_FREE_SLOT_MINUTES, 30]
    assert slots[0]["start_iso"] == _hhmm_to_iso(day, WORKDAY_START_HHMM)
    assert slots[-1]["end_iso"] == f"{day}T12:30:00+00:00"


def test_free_slots_keeps_short_gap_as_single_interval():
    day = "2026-03-06"
    entries = [
        {
            "id": "te-1",
            "description": "First",
            "timeInterval": {"start": f"{day}T09:00:00Z", "end": _hhmm_to_iso(day, WORKDAY_START_HHMM).replace("+00:00", "Z")},
        },
        {
            "id": "te-2",
            "description": "Second",
            "timeInterval": {"start": f"{day}T10:45:00Z", "end": _hhmm_to_iso(day, WORKDAY_END_HHMM).replace("+00:00", "Z")},
        },
    ]

    slots = get_free_slots_for_day(
        api_key="token",
        user_timezone="UTC",
        target_date=datetime(2026, 3, 6, 0, 0, tzinfo=timezone.utc),
        now_override="2026-03-06T08:00:00Z",
        user_payload={"id": "u1", "defaultWorkspace": "w1"},
        time_entries_payload=entries,
    )

    assert len(slots) == 1
    assert slots[0]["duration_min"] == 45
    assert slots[0]["start_iso"] == _hhmm_to_iso(day, WORKDAY_START_HHMM)
    assert slots[0]["end_iso"] == f"{day}T10:45:00+00:00"


def test_free_slots_reserve_lunch_break_when_it_fits():
    day = "2026-03-06"
    slots = get_free_slots_for_day(
        api_key="token",
        user_timezone="UTC",
        target_date=datetime(2026, 3, 6, 0, 0, tzinfo=timezone.utc),
        now_override="2026-03-06T08:00:00Z",
        user_payload={"id": "u1", "defaultWorkspace": "w1"},
        time_entries_payload=[],
    )

    lunch_start = _hhmm_to_iso(day, LUNCH_WINDOW_START_HHMM)
    lunch_start_dt = datetime.fromisoformat(lunch_start)
    lunch_end_dt = lunch_start_dt + timedelta(minutes=LUNCH_BREAK_MINUTES)
    lunch_end = lunch_end_dt.isoformat()

    assert slots[0]["start_iso"] == _hhmm_to_iso(day, WORKDAY_START_HHMM)
    assert any(s["end_iso"] == lunch_start for s in slots)
    assert any(s["start_iso"] == lunch_end for s in slots)


def test_client_get_workspace_users_fetches_all_pages(monkeypatch):
    calls: list[str] = []

    def fake_http_get_json(url: str, api_key: str, timeout: int = 15):
        calls.append(url)
        if "page=1" in url:
            return [{"id": "u-1"}, {"id": "u-2"}]
        if "page=2" in url:
            return [{"id": "u-3"}]
        return []

    monkeypatch.setattr("clockifycal.client._http_get_json", fake_http_get_json)

    users = get_workspace_users(
        api_key="token",
        workspace_id="w1",
        base_url="https://api.clockify.me/api",
        page_size=2,
    )

    assert [u["id"] for u in users] == ["u-1", "u-2", "u-3"]
    assert any("page=1" in url and "page-size=2" in url for url in calls)
    assert any("page=2" in url and "page-size=2" in url for url in calls)


def test_client_get_workspace_users_validates_page_size():
    with pytest.raises(ValueError):
        get_workspace_users(api_key="token", workspace_id="w1", page_size=0)
