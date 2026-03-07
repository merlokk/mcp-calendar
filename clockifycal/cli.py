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
    from clockifycal.loader import get_events_for_day, get_free_slots_for_day
else:
    from .client import ClockifyAPIError
    from .loader import get_events_for_day, get_free_slots_for_day


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


def _print_short_list(events: list[dict[str, object]], tz_name: str) -> None:
    if not events:
        print("No events")
        return

    out_tz = _resolve_output_tz(tz_name)
    for event in events:
        start = _to_local_iso(event.get("start_iso"), out_tz)
        end = _to_local_iso(event.get("end_iso"), out_tz)
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.api_key:
        print("CLOCKIFY_API_KEY or --api-key is required", file=sys.stderr)
        return 2

    target = date.fromisoformat(args.target_date) if args.target_date else None

    try:
        if args.free_slots:
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
        if args.free_slots:
            _print_short_free_slots(output_data, args.tz)
        else:
            _print_short_list(output_data, args.tz)
    elif args.pretty:
        print(json.dumps(output_data, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(output_data, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
