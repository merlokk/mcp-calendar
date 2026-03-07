# mcp-calendar

Calendar and time-tracking toolkit with three layers:

- `icscal`: load events from ICS URLs.
- `clockifycal`: load Clockify time entries and compute free slots.
- `mcp_calendar.py`: FastMCP server tools for assistants.

## Install

```bash
pip install -r requirements.txt
```

## Components

### 1) ICS loader (`icscal`)

Loads events from one or more public ICS URLs and returns normalized event dicts with flags:

- `is_current`
- `is_next`
- `is_next_overlapping`

### 2) Clockify adapter (`clockifycal`)

Loads time entries from Clockify API and returns the same event shape.
Also supports free-slot calculation for a work day with lunch constraints.

CLI examples:

```bash
python -m clockifycal.cli --api-key YOUR_KEY --date 2026-03-06 --tz UTC --pretty
python -m clockifycal.cli --api-key YOUR_KEY --date 2026-03-06 --tz UTC --list
python -m clockifycal.cli --api-key YOUR_KEY --date 2026-03-06 --tz UTC --free-slots --pretty
python -m clockifycal.cli --api-key YOUR_KEY --date 2026-03-06 --tz UTC --free-slots --list
```

### 3) MCP server (`mcp_calendar.py`)

Available tools:

- `get_now`
- `get_day`
- `get_free_slots`
- `get_clockify_tasks`
- `get_clockify_free_slots`

Run MCP server:

```bash
ICS_URLS="https://example.com/a.ics" python mcp_calendar.py
```

Run tools directly via helper CLI:

```bash
python run-mcp.py get_now
python run-mcp.py get_day --date 2026-03-06
python run-mcp.py get_free_slots --date 2026-03-06 --min-duration 30
python run-mcp.py get_clockify_tasks --date 2026-03-06
python run-mcp.py get_clockify_free_slots --date 2026-03-06
```

## Environment variables

Shared:

- `TZ` (optional, default `Europe/Nicosia`)
- `CACHE_MS` (optional, default `60000`)
- `OVERRIDE_NOW` (optional ISO datetime for tests/debug)

ICS source:

- `ICS_URLS` (required for ICS tools)

Clockify source:

- `CLOCKIFY_API_KEY` (required for Clockify API calls)
- `CLOCKIFY_BASE_URL` (optional, default `https://api.clockify.me/api`)
- `CLOCKIFY_WORKSPACE_ID` (optional override)
- `CLOCKIFY_USER_ID` (optional override)

## Tests

```bash
pytest -q test_clockifycal.py
pytest -q test_mcp_calendar.py
pytest -q test-lambda.py
```
