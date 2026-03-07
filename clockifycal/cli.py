from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

if __package__ in (None, ""):
    # Allow direct execution: `python clockifycal/cli.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from clockifycal.client import ClockifyAPIError
    from clockifycal.loader import get_events_for_day
else:
    from .client import ClockifyAPIError
    from .loader import get_events_for_day


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
    return parser


def _print_short_list(events: list[dict[str, object]]) -> None:
    if not events:
        print("No events")
        return

    for event in events:
        start = str(event.get("start_iso", ""))
        end = str(event.get("end_iso", ""))
        summary = str(event.get("summary", "")).strip() or "Clockify Time Entry"

        flags: list[str] = []
        if event.get("is_current"):
            flags.append("current")
        if event.get("is_next"):
            flags.append("next")
        if event.get("is_next_overlapping"):
            flags.append("next-overlap")

        suffix = f" [{' | '.join(flags)}]" if flags else ""
        print(f"- {start} -> {end} | {summary}{suffix}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.api_key:
        print("CLOCKIFY_API_KEY or --api-key is required", file=sys.stderr)
        return 2

    target = date.fromisoformat(args.target_date) if args.target_date else None

    try:
        events = get_events_for_day(
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
        _print_short_list(events)
    elif args.pretty:
        print(json.dumps(events, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(events, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
