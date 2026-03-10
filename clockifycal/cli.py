from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if __package__ in (None, ""):
    # Allow direct execution: `python clockifycal/cli.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from clockifycal.client import ClockifyAPIError
    from clockifycal.loader import (
        get_employee_events_for_day,
        get_events_for_day,
        get_free_slots_for_day,
        get_project_names_for_day,
        get_workspace_users_for_workspace,
    )
else:
    from .client import ClockifyAPIError
    from .loader import (
        get_employee_events_for_day,
        get_events_for_day,
        get_free_slots_for_day,
        get_project_names_for_day,
        get_workspace_users_for_workspace,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clockify time-entries -> icscal-like JSON")
    parser.add_argument("--api-key", default=os.environ.get("CLOCKIFY_API_KEY", ""))
    parser.add_argument("--tz", default=os.environ.get("TZ", "UTC"))
    parser.add_argument("--date", dest="target_date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--workspace-id", default=None)
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--base-url", default=os.environ.get("CLOCKIFY_BASE_URL", "https://api.clockify.me/api"))
    parser.add_argument("--now", default=None, help="Override now, ISO 8601")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--list", dest="short_list", action="store_true", help="Print a short event list")
    parser.add_argument("--free-slots", action="store_true", help="Return free slots instead of events")
    parser.add_argument("--project-names", action="store_true", help="Return Clockify project names for the selected day")
    parser.add_argument("--workspace-users", action="store_true", help="Return all users in workspace")
    parser.add_argument(
        "--employees-tasks",
        action="store_true",
        help="Return day tasks for employees from JSON file (adds employee_name and project_name)",
    )
    parser.add_argument(
        "--employees-file",
        default=str(Path(__file__).with_name("employees.json")),
        help="Path to JSON with employee names (array or {'employees': [...]})",
    )
    return parser


def _resolve_output_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _to_local_iso(raw: object, tz: ZoneInfo) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return ""
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(tz).isoformat()


def _print_short_list(
    events: list[dict[str, object]],
    tz_name: str,
    project_name_by_id: dict[str, str] | None = None,
) -> None:
    if not events:
        print("No events")
        return

    out_tz = _resolve_output_tz(tz_name)
    for event in events:
        start = _to_local_iso(event.get("start_iso"), out_tz)
        end = _to_local_iso(event.get("end_iso"), out_tz)
        summary = str(event.get("summary", "")).strip() or "Clockify Time Entry"
        employee_name = str(event.get("employee_name", "")).strip()
        project_id = str(event.get("project_id", "")).strip()
        event_project_name = str(event.get("project_name", "")).strip()
        project_name = event_project_name
        if not project_name and project_id and project_name_by_id:
            project_name = project_name_by_id.get(project_id, "").strip()

        flags: list[str] = []
        if event.get("is_current"):
            flags.append("current")
        if event.get("is_next"):
            flags.append("next")
        if event.get("is_next_overlapping"):
            flags.append("next-overlap")

        suffix = f" [{' | '.join(flags)}]" if flags else ""
        if employee_name and project_name:
            print(f"- {start} -> {end} | {employee_name} | {summary} | {project_name}{suffix}")
        elif employee_name:
            print(f"- {start} -> {end} | {employee_name} | {summary}{suffix}")
        elif project_name:
            print(f"- {start} -> {end} | {summary} | {project_name}{suffix}")
        else:
            print(f"- {start} -> {end} | {summary}{suffix}")


def _print_short_free_slots(slots: list[dict[str, object]], tz_name: str) -> None:
    if not slots:
        print("No free slots")
        return

    out_tz = _resolve_output_tz(tz_name)
    for slot in slots:
        start = _to_local_iso(slot.get("start_iso"), out_tz)
        end = _to_local_iso(slot.get("end_iso"), out_tz)
        duration = slot.get("duration_min")
        print(f"- {start} -> {end} | {duration} min")


def _print_short_project_names(projects: list[dict[str, object]]) -> None:
    if not projects:
        print("No projects")
        return
    for project in projects:
        project_id = str(project.get("project_id", "")).strip()
        name = str(project.get("project_name", "")).strip() or project_id
        print(f"- {name} ({project_id})")


def _print_short_workspace_users(users: list[dict[str, object]]) -> None:
    if not users:
        print("No users")
        return
    for user in users:
        name = str(user.get("name", "")).strip() or str(user.get("user_id", "")).strip()
        user_id = str(user.get("user_id", "")).strip()
        email = str(user.get("email", "")).strip()
        active = bool(user.get("active", False))
        status = "active" if active else "inactive"
        if email:
            print(f"- {name} ({email}) [{status}] ({user_id})")
        else:
            print(f"- {name} [{status}] ({user_id})")


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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.api_key:
        print("CLOCKIFY_API_KEY or --api-key is required", file=sys.stderr)
        return 2

    target = date.fromisoformat(args.target_date) if args.target_date else None

    try:
        if args.workspace_users:
            output_data = get_workspace_users_for_workspace(
                api_key=args.api_key,
                base_url=args.base_url,
                workspace_id=args.workspace_id,
                timeout=args.timeout,
            )
        elif args.project_names:
            output_data = get_project_names_for_day(
                api_key=args.api_key,
                user_timezone=args.tz,
                target_date=target,
                now_override=args.now,
                base_url=args.base_url,
                workspace_id=args.workspace_id,
                user_id=args.user_id,
                timeout=args.timeout,
            )
        elif args.employees_tasks:
            employee_names = _load_employee_names(args.employees_file)
            output_data = get_employee_events_for_day(
                api_key=args.api_key,
                employee_names=employee_names,
                user_timezone=args.tz,
                target_date=target,
                now_override=args.now,
                base_url=args.base_url,
                workspace_id=args.workspace_id,
                timeout=args.timeout,
            )
        elif args.free_slots:
            output_data = get_free_slots_for_day(
                api_key=args.api_key,
                user_timezone=args.tz,
                target_date=target,
                now_override=args.now,
                base_url=args.base_url,
                workspace_id=args.workspace_id,
                user_id=args.user_id,
                timeout=args.timeout,
            )
        else:
            output_data = get_events_for_day(
                api_key=args.api_key,
                user_timezone=args.tz,
                target_date=target,
                now_override=args.now,
                base_url=args.base_url,
                workspace_id=args.workspace_id,
                user_id=args.user_id,
                timeout=args.timeout,
            )
    except (ClockifyAPIError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.short_list:
        if args.workspace_users:
            _print_short_workspace_users(output_data)
        elif args.project_names:
            _print_short_project_names(output_data)
        elif args.free_slots:
            _print_short_free_slots(output_data, args.tz)
        else:
            project_name_by_id: dict[str, str] = {}
            include_project_lookup = not args.employees_tasks
            project_ids = (
                sorted(
                    {
                        str(event.get("project_id", "")).strip()
                        for event in output_data
                        if str(event.get("project_id", "")).strip()
                    }
                )
                if include_project_lookup
                else []
            )
            if project_ids:
                projects = get_project_names_for_day(
                    api_key=args.api_key,
                    user_timezone=args.tz,
                    target_date=target,
                    now_override=args.now,
                    base_url=args.base_url,
                    workspace_id=args.workspace_id,
                    user_id=args.user_id,
                    timeout=args.timeout,
                )
                project_name_by_id = {
                    str(project.get("project_id", "")).strip(): str(project.get("project_name", "")).strip()
                    for project in projects
                    if str(project.get("project_id", "")).strip()
                }
            _print_short_list(output_data, args.tz, project_name_by_id)
    elif args.pretty:
        print(json.dumps(output_data, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(output_data, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
