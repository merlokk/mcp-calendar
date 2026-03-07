# AGENTS.md

Agent entrypoint for this repository.

## Primary Instructions
- Read and follow [CLAUDE.MD](./CLAUDE.MD).
- Additional context files:
  - `@README.md`
  - `@clockifycal/README.md`

## Quick Commands
- Run Clockify tests: `pytest -q test_clockifycal.py`
- Run MCP tests: `pytest -q test_mcp_calendar.py`
- Run lambda tests: `pytest -q test-lambda.py`
- Run MCP clockify free slots: `python run-mcp.py get_clockify_free_slots --date 2026-03-06`

## Scope
- Keep changes minimal and targeted.
- Update docs when behavior changes.
