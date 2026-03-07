# clockifycal

`clockifycal` is a small module that loads time entries from Clockify and converts them into an event list (an `icscal`-like JSON shape).

## What it does

- Fetches the current user via `GET /v1/user`.
- Computes the day window in the selected timezone (`--tz` / `TZ`).
- Loads the user's `time-entries` for that window.
- Returns a sorted event list with flags:
  - `is_current`: currently active event.
  - `is_next`: next event after current one or after `now`.
  - `is_next_overlapping`: next event that overlaps with the current one.

## CLI

Run:

```bash
python -m clockifycal.cli --api-key <CLOCKIFY_API_KEY> --tz Europe/Kyiv --pretty
```

Short list view:

```bash
python -m clockifycal.cli --api-key <CLOCKIFY_API_KEY> --tz Europe/Kyiv --date 2025-06-03 --list
```

Supported arguments:

- `--api-key` (or `CLOCKIFY_API_KEY` env var): Clockify API key.
- `--tz` (or `TZ`): timezone used to compute the daily window.
- `--date`: target date in `YYYY-MM-DD` format.
- `--workspace-id`: workspace ID (optional; falls back to `defaultWorkspace`).
- `--user-id`: user ID (optional; falls back to `/v1/user` response).
- `--base-url`: API base URL (default: `https://api.clockify.me/api`).
- `--now`: override current time (ISO 8601), useful for tests.
- `--timeout`: HTTP timeout in seconds.
- `--pretty`: pretty-print JSON output.
- `--list`: print a short human-readable event list instead of JSON.

`--list` output format:

- `- <start_iso> -> <end_iso> | <summary>`
- Optional markers: `[current | next | next-overlap]`.

## Main modules

- `client.py`: HTTP client and `get_current_user`, `get_time_entries`.
- `loader.py`: day-window logic and event building (`get_events_for_day`).
- `cli.py`: command-line wrapper.
