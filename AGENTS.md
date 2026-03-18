# AGENTS.md

Agent entrypoint for this repository.

## Primary Instructions
- Read and follow [CLAUDE.MD](./CLAUDE.MD).
- Additional context files:
  - `@README.md`
  - `@clockifycal/README.md`

## Quick Commands
- Run MCP server: `fastmcp run E:\projects\mcp-calendar\mcp_calendar.py --no-banner --log-level ERROR`
- MCP overview: `python run-mcp.py get_server_overview`
- MCP clockify tasks: `python run-mcp.py get_clockify_tasks --date 2026-03-06`
- MCP clockify free slots: `python run-mcp.py get_clockify_free_slots --date 2026-03-06`
- In this Windows environment, prefer `py`; `python` and `pytest` may be unavailable in `PATH`.
- Run ICS tests: `py -m pytest -q icscal/tests.py`
- Run Clockify tests: `py -m pytest -q test_clockifycal.py`
- Run MCP tests: `py -m pytest -q test_mcp_calendar.py`
- Run lambda tests: `py -m pytest -q test-lambda.py`

## Scope
- Keep changes minimal and targeted.
- Keep workflow day-based when possible (`date_str` / `--date`).
- Update docs when behavior changes.
