"""
run_mcp.py — call mcp_calendar tools from the command line
===========================================================
Imports tool functions directly (no MCP transport overhead).
Output is always JSON to stdout.

Usage
-----
  python run_mcp.py get_now
  python run_mcp.py get_now --override-now "2026-02-13T17:00:00+02:00"

  python run_mcp.py get_day
  python run_mcp.py get_day --date 2026-02-13
  python run_mcp.py get_day --date 2026-02-13 --override-now "2026-02-13T09:00:00+02:00"

  python run_mcp.py get_free_slots
  python run_mcp.py get_free_slots --date 2026-02-13
  python run_mcp.py get_free_slots --date 2026-02-13 --min-duration 60
  python run_mcp.py get_free_slots --day-start 10:00 --day-end 19:00

  python run_mcp.py get_clockify_tasks --date 2026-02-13
  python run_mcp.py get_clockify_free_slots --date 2026-02-13
  python run_mcp.py get_clockify_employee_tasks --date 2026-02-13 --employees-file clockifycal/employees.json
  python run_mcp.py get_server_overview

  # pipe into jq
  python run_mcp.py get_now | jq .current
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Ensure the script's own directory is first so we load the local mcp_calendar.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp_calendar import (
    get_now,
    get_day,
    get_free_slots,
    get_clockify_tasks,
    get_clockify_free_slots,
    get_clockify_employee_tasks,
    get_server_overview,
)  # noqa: E402


def _print(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Call mcp_calendar tools and print JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("tool", choices=["get_now", "get_day", "get_free_slots", "get_clockify_tasks", "get_clockify_free_slots", "get_clockify_employee_tasks", "get_server_overview"],
                   help="Tool to call")

    p.add_argument("--override-now", metavar="ISO",
                   help='Override "now", e.g. "2026-02-13T17:00:00+02:00"')

    # get_day / get_free_slots
    p.add_argument("--date", metavar="YYYY-MM-DD",
                   help="Target date (default: today)")

    # get_free_slots
    p.add_argument("--min-duration", metavar="MIN", type=int, default=30,
                   help="Minimum free slot length in minutes (default: 30)")
    p.add_argument("--day-start", metavar="HH:MM", default="09:00",
                   help="Working day start (default: 09:00)")
    p.add_argument("--day-end", metavar="HH:MM", default="18:00",
                   help="Working day end (default: 18:00)")
    p.add_argument("--employees-file", metavar="PATH",
                   help="Employees JSON file path (for get_clockify_employee_tasks)")

    return p.parse_args()


def main() -> None:
    args = _parse_args()

    try:
        if args.tool == "get_now":
            result = get_now(override_now=args.override_now)

        elif args.tool == "get_day":
            result = get_day(
                date_str=args.date,
                override_now=args.override_now,
            )

        elif args.tool == "get_free_slots":
            result = get_free_slots(
                date_str=args.date,
                min_duration=args.min_duration,
                day_start=args.day_start,
                day_end=args.day_end,
                override_now=args.override_now,
            )

        elif args.tool == "get_clockify_tasks":
            result = get_clockify_tasks(
                date_str=args.date,
                override_now=args.override_now,
            )

        elif args.tool == "get_clockify_free_slots":
            result = get_clockify_free_slots(
                date_str=args.date,
                override_now=args.override_now,
            )

        elif args.tool == "get_clockify_employee_tasks":
            result = get_clockify_employee_tasks(
                date_str=args.date,
                override_now=args.override_now,
                employees_file=args.employees_file,
            )

        elif args.tool == "get_server_overview":
            result = get_server_overview()

    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": type(e).__name__, "detail": str(e)}),
              file=sys.stderr)
        sys.exit(1)

    _print(result)


if __name__ == "__main__":
    main()
