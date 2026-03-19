# mcp-calendar

Calendar and time-tracking toolkit with three layers:

- `icscal`: load events from ICS URLs.
- `clockifycal`: load Clockify time entries and compute free slots.
- `mcp_calendar.py`: FastMCP server tools for assistants.

## Install

```bash
pip install -r requirements.txt
```

For development and tests:

```bash
pip install -r requirements-dev.txt
```

Windows (recommended launcher):

```powershell
py -m pip install -r requirements-dev.txt
```

Verify test tooling:

```bash
python -m pytest --version
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
python -m clockifycal.cli --api-key YOUR_KEY --date 2026-03-06 --tz UTC --add-task --start 10:00 --duration-min 90 --description "Deep work" --project-name "Internal" --pretty
python -m clockifycal.cli --api-key YOUR_KEY --date 2026-03-06 --tz UTC --employees-tasks --employees-file clockifycal/employees.json --list
python -m clockifycal.cli --api-key YOUR_KEY --workspace-users --list
```

### 3) MCP server (`mcp_calendar.py`)

Server purpose:

- read calendar events from ICS sources
- read occupied time and free slots from Clockify

Primary workflow is day-based:

- pick one target day (`date_str` in `YYYY-MM-DD`)
- call relevant day tools for that day

Available tools:

- `get_server_overview` (returns purpose, workflow, tools, params)
- `get_now`
- `get_day`
- `get_free_slots`
- `get_clockify_tasks`
- `get_clockify_free_slots`
- `get_clockify_employee_tasks`
- `create_clockify_task`

Run MCP server:

```bash
ICS_URLS="https://example.com/a.ics" python mcp_calendar.py
```

Run tools directly via helper CLI:

```bash
python run-mcp.py get_server_overview
python run-mcp.py get_now
python run-mcp.py get_day --date 2026-03-06
python run-mcp.py get_free_slots --date 2026-03-06 --min-duration 30
python run-mcp.py get_clockify_tasks --date 2026-03-06
python run-mcp.py get_clockify_free_slots --date 2026-03-06
python run-mcp.py get_clockify_employee_tasks --date 2026-03-06 --employees-file clockifycal/employees.json
python run-mcp.py create_clockify_task --date 2026-03-06 --start-time 15:00 --duration-min 60 --description "Test task" --project-name "T-Platform"
```

`create_clockify_task` restrictions:

- creates entries only for current Clockify user
- project is mandatory (`project_name` or `project_id`)
- max duration is 240 minutes
- new task must not overlap existing entries
- this is a responsible write operation
- callers must not invent/simulate execution; to create the task they must actually call the tool

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

MCP logging:

- `MCP_LOG_FILE_ENABLED` (optional, set `1`/`true`/`yes`/`on` to append JSONL logs to `mcp_calendar.log` next to `mcp_calendar.py`)
- When enabled, each MCP tool call logs request args, relevant env values, response payload, and errors

## Tests

```bash
py -m pytest -q test_clockifycal.py test_mcp_calendar.py test-lambda.py icscal/tests.py
pytest -q test_clockifycal.py
pytest -q test_mcp_calendar.py
pytest -q test-lambda.py
```
