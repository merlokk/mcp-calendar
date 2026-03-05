# CLAUDE.md — mcp-calendar

## Stack

- Python 3.13+
- AWS Lambda (handler signature: `handler(event, context)`)
- icalendar ≥ 5.0, recurring-ical-events ≥ 3.0, pytz ≥ 2024.1
- pytest ≥ 7.0
- No frameworks. No ORM. No async. Plain stdlib + the four deps above.

## Hard Boundaries

- **No new dependencies** without updating `icscal/requirements.txt`.
- **No `datetime.now()` without `pytz.utc`** — all datetimes must be timezone-aware.
- **No `pytz.normalize()` at module level** — it's `tz_instance.normalize(dt)`.
- **No relative imports outside the `icscal/` package** — use try/except dual import pattern.
- **No writing to filesystem in Lambda** — `windows_zones.configure(file_cache=False)` stays.
- **`get_events_for_day` returns `list[dict]`**, never JSON string.
- **TZID normalization happens on raw bytes before `Calendar.from_ical()`** — never after.
- **Tests must not use `tmp_path`** — use `tempfile.mkdtemp()` inside `icscal/` dir instead.

## File Structure

```
main.py                  # Local runner for quick library testing with real URLs
lambda_handler.py        # AWS Lambda entry point — param parsing, cache, response shaping
lambda_run.py            # CLI wrapper for local testing of lambda_handler
test-lambda.py           # Tests for lambda_handler only (library mocked)
icscal/
  __init__.py            # Exports get_events_for_day
  calendar_loader.py     # Core library — ICS fetch, parse, dedup, flag logic
  windows_zones.py       # Windows→IANA timezone mapping with optional file cache
  tests.py               # All library tests (114 tests)
  conftest.py            # sys.path setup for pytest
  requirements.txt       # Runtime + test deps
```

- New helpers for calendar parsing → `icscal/calendar_loader.py`.
- New timezone mappings → `icscal/windows_zones.py` `_FALLBACK` dict.
- New lambda-level logic → `lambda_handler.py` + tests in `test-lambda.py`.
- Do **not** create new files unless the rule above has no applicable category.

## Task Contract Template

Before writing any code, confirm each field:

```
GOAL
  One sentence. What changes in the system when this task is done?

CONSTRAINTS
  - Which file(s) are in scope?
  - Which files must not be touched?
  - Any edge cases from existing tests that must keep passing?

OUTPUT FORMAT
  - New function / modified function / new test class?
  - Signature if applicable.
  - Return type.

FAILURE CONDITIONS
  - What does a wrong solution look like?
  - Which existing test would it break?
  - What timezone or DST edge case could silently corrupt results?
```

### Flag semantics (never change without updating tests)

| Flag | Condition |
|---|---|
| `is_current` | `start <= now < end` |
| `is_next` | first event with `start >= current.end` (or `>= now` if no current) |
| `is_next_overlapping` | first event with `current.start < start < current.end`; only when current exists |

### `now` resolution order in `get_events_for_day`

1. `now_override` if passed explicitly
2. `target_date` if it is a `datetime` (not just `date`)
3. `datetime.now(pytz.utc)`