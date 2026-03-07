from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .client import DEFAULT_BASE_URL, get_current_user, get_time_entries


def _to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_to_utc(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_tz(user_timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(user_timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _resolve_now(now_override: Optional[datetime | str]) -> datetime:
    if now_override is None:
        return datetime.now(timezone.utc)
    if isinstance(now_override, datetime):
        return now_override.astimezone(timezone.utc) if now_override.tzinfo else now_override.replace(tzinfo=timezone.utc)
    return _parse_iso_to_utc(now_override)


def _compute_window(
    user_timezone: str,
    target_date: Optional[date | datetime],
    now_utc: datetime,
) -> tuple[datetime, datetime, date]:
    tz = _resolve_tz(user_timezone)
    local_now = now_utc.astimezone(tz)

    if target_date is None:
        day = local_now.date()
    elif isinstance(target_date, datetime):
        day = target_date.astimezone(tz).date() if target_date.tzinfo else target_date.date()
    else:
        day = target_date

    midnight_today = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=tz)
    midnight_tomorrow = midnight_today + timedelta(days=1)
    return midnight_today.astimezone(timezone.utc), midnight_tomorrow.astimezone(timezone.utc), day


def _entry_end(start_utc: datetime, end_value: Any, now_utc: datetime) -> datetime:
    if isinstance(end_value, str) and end_value.strip():
        end = _parse_iso_to_utc(end_value)
        if end > start_utc:
            return end
    if now_utc > start_utc:
        return now_utc
    return start_utc + timedelta(minutes=1)


def get_events_for_day(
    *,
    api_key: str,
    user_timezone: str = "UTC",
    target_date: Optional[date | datetime] = None,
    now_override: Optional[datetime | str] = None,
    base_url: str = DEFAULT_BASE_URL,
    workspace_id: str | None = None,
    user_id: str | None = None,
    timeout: int = 15,
    user_payload: Optional[dict[str, Any]] = None,
    time_entries_payload: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    now_utc = _resolve_now(now_override)
    window_start, window_end, _ = _compute_window(user_timezone, target_date, now_utc)

    user = user_payload or get_current_user(api_key=api_key, base_url=base_url, timeout=timeout)
    resolved_workspace_id = workspace_id or str(user.get("defaultWorkspace", "")).strip()
    resolved_user_id = user_id or str(user.get("id", "")).strip()
    if not resolved_workspace_id:
        raise ValueError("Clockify user payload has no defaultWorkspace and workspace_id not provided")
    if not resolved_user_id:
        raise ValueError("Clockify user payload has no id and user_id not provided")

    if time_entries_payload is None:
        entries = get_time_entries(
            api_key=api_key,
            workspace_id=resolved_workspace_id,
            user_id=resolved_user_id,
            start=_to_utc_iso(window_start),
            end=_to_utc_iso(window_end),
            base_url=base_url,
            timeout=timeout,
        )
    else:
        entries = time_entries_payload

    organizer = str(user.get("email", "")).strip() or None
    endpoint_url = (
        f"{base_url.rstrip('/')}/v1/workspaces/{resolved_workspace_id}/user/{resolved_user_id}/time-entries"
    )
    collected: list[dict[str, Any]] = []

    for entry in entries:
        interval = entry.get("timeInterval")
        if not isinstance(interval, dict):
            continue

        start_raw = interval.get("start")
        if not isinstance(start_raw, str) or not start_raw.strip():
            continue

        start_utc = _parse_iso_to_utc(start_raw)
        end_utc = _entry_end(start_utc, interval.get("end"), now_utc)
        if not (start_utc < window_end and end_utc > window_start):
            continue

        uid = str(entry.get("id", "")).strip() or f"clockify-{int(start_utc.timestamp() * 1000)}"
        description = str(entry.get("description", "")).strip()
        summary = description or "Clockify Time Entry"

        collected.append(
            {
                "uid": uid,
                "summary": summary,
                "location": None,
                "organizer": organizer,
                "start": start_utc,
                "end": end_utc,
                "start_ms": int(start_utc.timestamp() * 1000),
                "end_ms": int(end_utc.timestamp() * 1000),
                "calendar_id": resolved_workspace_id,
                "calendar_url": endpoint_url,
            }
        )

    collected.sort(key=lambda ev: ev["start_ms"])

    now_ms = int(now_utc.timestamp() * 1000)
    current_idx: int | None = None
    for i, ev in enumerate(collected):
        if ev["start_ms"] <= now_ms < ev["end_ms"]:
            current_idx = i
            break

    search_from_ms = collected[current_idx]["end_ms"] if current_idx is not None else now_ms
    next_idx: int | None = None
    for i, ev in enumerate(collected):
        if i == current_idx:
            continue
        if ev["start_ms"] >= search_from_ms:
            next_idx = i
            break

    next_overlapping_idx: int | None = None
    if current_idx is not None:
        current_ev = collected[current_idx]
        for i, ev in enumerate(collected):
            if i == current_idx:
                continue
            if ev["start_ms"] > current_ev["start_ms"] and ev["start_ms"] < current_ev["end_ms"]:
                next_overlapping_idx = i
                break

    output: list[dict[str, Any]] = []
    for i, ev in enumerate(collected):
        output.append(
            {
                "uid": ev["uid"],
                "summary": ev["summary"],
                "location": ev["location"],
                "organizer": ev["organizer"],
                "start_iso": ev["start"].isoformat(),
                "end_iso": ev["end"].isoformat(),
                "start_ms": ev["start_ms"],
                "end_ms": ev["end_ms"],
                "calendar_id": ev["calendar_id"],
                "calendar_url": ev["calendar_url"],
                "is_current": i == current_idx,
                "is_next": i == next_idx,
                "is_next_overlapping": i == next_overlapping_idx,
            }
        )

    return output
