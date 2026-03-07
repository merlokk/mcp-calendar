# CLAUDE.md

## Project
- Repo: `mcp-calendar`
- Goal: collect busy time from ICS + Clockify, expose MCP tools, and support planning/free-slot logic.

## Stack
- Python 3.13+
- FastMCP
- icalendar, recurring-ical-events, pytz
- pytest

## Key Files
- `mcp_calendar.py`: MCP tools (`get_now`, `get_day`, `get_free_slots`, `get_clockify_tasks`, `get_clockify_free_slots`)
- `run-mcp.py`: local CLI wrapper for MCP tools
- `clockifycal/client.py`: Clockify API calls
- `clockifycal/loader.py`: Clockify event transform + free-slot calculation
- `clockifycal/cli.py`: Clockify CLI (`--pretty`, `--list`, `--free-slots`)
- `lambda_handler.py`: lambda interface (ICS path)

## Commands
- Run clockify CLI: `python -m clockifycal.cli --api-key <KEY> --date 2026-03-06 --pretty`
- Run short list: `python -m clockifycal.cli --api-key <KEY> --date 2026-03-06 --list`
- Run free slots: `python -m clockifycal.cli --api-key <KEY> --date 2026-03-06 --free-slots --pretty`
- Run MCP helper: `python run-mcp.py get_clockify_tasks --date 2026-03-06`
- Run MCP free slots: `python run-mcp.py get_clockify_free_slots --date 2026-03-06`
- Tests: `pytest -q test_clockifycal.py` and `pytest -q test_mcp_calendar.py`

## Environment
- Shared: `TZ`, `CACHE_MS`, `OVERRIDE_NOW`
- ICS: `ICS_URLS`
- Clockify: `CLOCKIFY_API_KEY`, `CLOCKIFY_BASE_URL`, `CLOCKIFY_WORKSPACE_ID`, `CLOCKIFY_USER_ID`

## Code Rules
- Keep datetimes timezone-aware.
- Keep public event shape stable (`uid`, `summary`, `start_iso`, `end_iso`, flags).
- Do not add dependencies without explicit request.
- Keep changes minimal and targeted; avoid unrelated refactors.
- Prefer deterministic tests with mocked network/API.

## Clockify Free Slots
- Config is in `clockifycal/loader.py` constants.
- Use workday bounds only.
- Reserve 30-min lunch only if it fits inside lunch window.
- Split free gaps into chunks up to 60 minutes.

## Workflow
- Implement code + tests in same change.
- Run only relevant tests first; expand only if needed.
- Update project docs (`README.md`, `clockifycal/README.md`) when behavior changes.
- Keep this file concise; prefer short, specific, verifiable rules.
- Project context files: `@README.md`, `@clockifycal/README.md`.
