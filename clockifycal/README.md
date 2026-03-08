# clockifycal

`clockifycal` is a stdlib-only Clockify adapter.
It loads time entries and exposes:

- event list for a day (`get_events_for_day`)
- free slot list for a day (`get_free_slots_for_day`)
- project names for a day (`get_project_names_for_day`)

## API calls used

- `GET /v1/user`
- `GET /v1/workspaces/{workspaceId}/user/{userId}/time-entries`
- `GET /v1/workspaces/{workspaceId}/projects/{projectId}`

Base URL default:

- `https://api.clockify.me/api`

## Main functions

- `get_events_for_day(...)`: returns normalized events with flags (`is_current`, `is_next`, `is_next_overlapping`).
- `get_free_slots_for_day(...)`: returns free intervals based on workday constants and current booked entries.
- `get_project_names_for_day(...)`: returns unique project names used by day entries.

## Free-slot rules

Configured as constants in `loader.py`:

- `WORKDAY_START_HHMM = "10:00"`
- `WORKDAY_END_HHMM = "19:00"`
- `LUNCH_WINDOW_START_HHMM = "13:30"`
- `LUNCH_WINDOW_END_HHMM = "17:00"`
- `LUNCH_BREAK_MINUTES = 30`
- `MAX_FREE_SLOT_MINUTES = 60`

Behavior:

- Free time is calculated only inside workday bounds.
- A 30-minute lunch break is reserved inside lunch window if a gap exists.
- Any free gap longer than 60 minutes is split into 60-minute slots.
- Any free gap shorter than 60 minutes is kept as one slot.

## CLI

Run as module (recommended):

```bash
python -m clockifycal.cli --api-key <CLOCKIFY_API_KEY> --tz Europe/Kyiv --pretty
```

Run as script:

```bash
python clockifycal/cli.py --api-key <CLOCKIFY_API_KEY> --tz Europe/Kyiv --pretty
```

Short list output:

```bash
python -m clockifycal.cli --api-key <CLOCKIFY_API_KEY> --date 2025-06-03 --list
```

`--list` prints start/end in the timezone from `--tz` (or `TZ`).

Free slots output (JSON):

```bash
python -m clockifycal.cli --api-key <CLOCKIFY_API_KEY> --date 2025-06-03 --free-slots --pretty
```

Free slots short list:

```bash
python -m clockifycal.cli --api-key <CLOCKIFY_API_KEY> --date 2025-06-03 --free-slots --list
```

Project names:

```bash
python -m clockifycal.cli --api-key <CLOCKIFY_API_KEY> --date 2025-06-03 --project-names --list
```

Supported args:

- `--api-key` (or env `CLOCKIFY_API_KEY`)
- `--tz` (or env `TZ`)
- `--date` (`YYYY-MM-DD`)
- `--workspace-id`
- `--user-id`
- `--base-url`
- `--now` (ISO 8601)
- `--timeout`
- `--pretty`
- `--list`
- `--free-slots`
- `--project-names`
