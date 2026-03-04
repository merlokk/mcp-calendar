"""
Local runner for lambda_handler.py
====================================
Simulates an AWS Lambda + API Gateway invocation from the command line.

Usage
-----
  python run_local.py --ics-urls "https://..." --tz Asia/Nicosia --mode summary
  python run_local.py --ics-urls "https://..." --override-now 2026-02-13T17:00:00+02:00
  python run_local.py --ics-urls "https://..." --mode full
  python run_local.py --ics-urls "https://..." --compact | python -m json.tool

Flags
-----
--ics-urls      Space-separated .ics URLs (overrides ICS_URLS env var)
--tz            IANA timezone, default Europe/Nicosia
--cache-ms      Warm-cache TTL ms, default 60000
--override-now  ISO datetime to use as "now", e.g. "2026-02-13T17:00:00+02:00"
--mode          summary (default) | full
--no-color      Disable colored output
--compact       Print raw JSON only (no pretty table)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_GRAY   = "\033[90m"


def _c(text: str, code: str, enabled: bool) -> str:
    return f"{code}{text}{_RESET}" if enabled else text


# ---------------------------------------------------------------------------
# Pretty-printer: summary mode
# ---------------------------------------------------------------------------
def _print_summary(body: dict, color: bool) -> None:
    def c(t: str, code: str) -> str:
        return _c(t, code, color)

    sep = c("━" * 60, _GRAY)
    print(sep)
    print(c("  Calendar Summary", _BOLD))
    print(sep)
    print(f"  Generated : {body.get('generatedAt', '?')}")
    print(f"  Now       : {c(body.get('now', '?'), _BOLD)}")
    w = body.get("window", {})
    print(f"  Window    : {w.get('start')}  ->  {w.get('end')}  ({w.get('tz')})")
    print()

    def print_event(label: str, ev, col: str) -> None:
        if ev is None:
            print(f"  {c(label, col)}: {c('—  (none)', _GRAY)}")
            return
        print(f"  {c(label, col)}:")
        print(f"    title    : {c(ev.get('title', '?'), _BOLD)}")
        print(f"    time     : {ev.get('start')}  ->  {ev.get('end')}")
        if ev.get("location"):
            print(f"    location : {ev['location']}")
        if ev.get("organizer"):
            print(f"    organizer: {ev['organizer']}")
        print(f"    uid      : {c(ev.get('uid', '?'), _GRAY)}")

    print_event("Current         ", body.get("current"),        _GREEN)
    print()
    print_event("Next            ", body.get("next"),           _CYAN)
    mun = body.get("minutesUntilNext")
    mun_str = f"{mun} min" if mun is not None else "—"
    print(f"  Minutes until next : {c(mun_str, _CYAN)}")
    print()
    is_ov = body.get("isOverlappingNow", False)
    print(f"  isOverlappingNow   : {c('YES', _YELLOW) if is_ov else c('no', _GRAY)}")
    print_event("Next Overlapping", body.get("nextOverlapping"), _YELLOW)
    print(sep)


# ---------------------------------------------------------------------------
# Pretty-printer: full mode
# ---------------------------------------------------------------------------
def _print_full(body: dict, color: bool) -> None:
    def c(t: str, code: str) -> str:
        return _c(t, code, color)

    events = body.get("events", [])
    sep = c("━" * 70, _GRAY)
    print(sep)
    print(c(f"  Full event list  ({len(events)} events)", _BOLD))
    w = body.get("window", {})
    print(f"  Window: {w.get('start')}  ->  {w.get('end')}  ({w.get('tz')})")
    print(f"  Now:    {body.get('now')}")
    print(sep)

    for ev in events:
        flags = []
        if ev.get("is_current"):          flags.append(c("CURRENT",          _GREEN))
        if ev.get("is_next"):             flags.append(c("NEXT",             _CYAN))
        if ev.get("is_next_overlapping"): flags.append(c("NEXT-OVERLAPPING", _YELLOW))
        flag_str = "  " + "  ".join(flags) if flags else ""
        print(f"  {c(ev.get('summary', '?'), _BOLD)}{flag_str}")
        print(f"    {ev.get('start_iso')}  ->  {ev.get('end_iso')}")
        if ev.get("location"):
            print(f"    {ev['location']}")
        print()

    print(sep)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run lambda_handler.py locally",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--ics-urls",     metavar="URLS",
                   help="Space-separated .ics URLs (overrides ICS_URLS env var)")
    p.add_argument("--tz",           metavar="TZ",
                   help="IANA timezone (overrides TZ env var)")
    p.add_argument("--cache-ms",     metavar="MS", type=int,
                   help="Cache TTL in milliseconds")
    p.add_argument("--override-now", metavar="ISO",
                   help='ISO datetime used as "now"')
    p.add_argument("--mode",         choices=["summary", "full"], default="summary")
    p.add_argument("--no-color",     action="store_true")
    p.add_argument("--compact",      action="store_true",
                   help="Print raw JSON without indentation")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    args  = _parse_args()
    color = not args.no_color and sys.stdout.isatty()

    # Apply CLI flags as env vars so lambda_handler picks them up
    if args.ics_urls:
        os.environ["ICS_URLS"] = args.ics_urls
    if args.tz:
        os.environ["TZ"] = args.tz
    if args.cache_ms is not None:
        os.environ["CACHE_MS"] = str(args.cache_ms)
    if args.override_now:
        os.environ["OVERRIDE_NOW"] = args.override_now

    # Build a minimal API Gateway-style event dict
    lambda_event = {"queryStringParameters": {"mode": args.mode}}

    # Load lambda_handler.py by absolute path — immune to sys.path issues
    script_dir   = os.path.dirname(os.path.abspath(__file__))
    handler_path = os.path.join(script_dir, "lambda_handler.py")

    if not os.path.exists(handler_path):
        print(f"File not found: {handler_path}", file=sys.stderr)
        sys.exit(1)

    try:
        spec = importlib.util.spec_from_file_location("lambda_handler", handler_path)
        lh   = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lh)
        handler = lh.handler
    except AttributeError:
        print("lambda_handler.py has no function named 'handler'", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Failed to load lambda_handler.py: {e}", file=sys.stderr)
        sys.exit(1)

    class _FakeContext:
        function_name      = "calendar-lambda-local"
        memory_limit_in_mb = 256
        aws_request_id     = "local-run"

    response = handler(lambda_event, _FakeContext())
    status   = response.get("statusCode", 0)
    raw_body = response.get("body", "{}")

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        print(raw_body)
        sys.exit(0 if status < 400 else 1)

    if status >= 400:
        print(f"ERROR {status}: {body.get('error', '?')}", file=sys.stderr)
        if body.get("detail"):
            print(f"Detail: {body['detail']}", file=sys.stderr)
        sys.exit(1)

    if args.compact:
        print(json.dumps(body, ensure_ascii=False))
        return

    if args.mode == "summary":
        _print_summary(body, color)
    else:
        _print_full(body, color)

    # Raw JSON to stderr (doesn't interfere with piping stdout)
    print("\n" + json.dumps(body, ensure_ascii=False, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()