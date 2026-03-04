# ICS Calendar Loader

Loads multiple ICS calendars from URLs and returns a JSON array of events for a given day, with full timezone support for Google Calendar and Outlook/Exchange.

## Install

```bash
pip install -r requirements.txt
```

## Quick Start

```python
from calendar_loader import get_events_for_day

json_str = get_events_for_day(
    calendar_urls=[
        "https://calendar.google.com/calendar/ical/primary/public/basic.ics",
        "https://outlook.office365.com/owa/calendar/.../calendar.ics",
    ],
    user_timezone="America/New_York",  # IANA or Windows tz name
)

import json
events = json.loads(json_str)
for e in events:
    print(e["summary"], e["start_iso"], "current:", e["is_current"])
```

## Output JSON Schema

Each event in the array:

| Field | Type | Description |
|---|---|---|
| `uid` | str | Calendar UID |
| `summary` | str | Event title |
| `start_iso` | str | ISO 8601 start (UTC) |
| `end_iso` | str | ISO 8601 end (UTC) |
| `start_ms` | int | Start as Unix ms |
| `end_ms` | int | End as Unix ms |
| `calendar_url` | str | Source calendar URL |
| `is_current` | bool | Happening right now |
| `is_next` | bool | Next after current ends |
| `is_next_overlapping` | bool | First event overlapping with next |
| `is_next_non_overlapping` | bool | First event after overlapping cluster |

## Run Tests

```bash
python -m pytest tests.py -v
```

## Critical Implementation Details

### 1. Window Calculation
- Window = `[midnight today, midnight tomorrow)` in user's timezone
- Recurring events expanded from **windowStart** (not NOW) so early-morning events are included

### 2. Timezone Handling
- Scans VTIMEZONE blocks in each ICS file first
- **Does NOT remap** Windows TZ names (e.g. "Pacific Standard Time") that have a VTIMEZONE block
- Only maps Windows→IANA when no VTIMEZONE block exists for that TZID

### 3. Recurring Event Duration
- Duration = `master.end - master.start`
- Applied to each occurrence: `occurrence.end = occurrence.start + duration`
- Avoids the "stale end time" bug where all occurrences get the first occurrence's end

### 4. UID Priority
- Multiple calendars: first URL in list wins for duplicate UIDs
- Uses `calendar_index` for tie-breaking

### 5. Current/Next Logic
- `current` = event where `start <= NOW < end`
- `next` = first event where `start >= current.end` (not `start > NOW`)

### 6. Override (RECURRENCE-ID) Handling
- **Used overrides**: replace the specific occurrence in the master series
- **Orphaned overrides** (no master): included if they overlap the day window
