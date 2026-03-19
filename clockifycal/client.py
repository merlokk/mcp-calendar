from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://api.clockify.me/api"


class ClockifyAPIError(RuntimeError):
    pass


def _build_headers(api_key: str) -> dict[str, str]:
    return {
        "X-Api-Key": api_key,
        "Accept": "application/json",
        "User-Agent": "clockifycal/1.0",
    }


def _http_get_json(url: str, api_key: str, timeout: int = 15) -> Any:
    req = urllib.request.Request(
        url,
        headers=_build_headers(api_key),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ClockifyAPIError(f"Clockify HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ClockifyAPIError(f"Clockify connection error: {exc}") from exc


def _http_post_json(url: str, api_key: str, payload: dict[str, Any], timeout: int = 15) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            **_build_headers(api_key),
            "Content-Type": "application/json",
        },
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ClockifyAPIError(f"Clockify HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ClockifyAPIError(f"Clockify connection error: {exc}") from exc


def get_current_user(
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = 15,
) -> dict[str, Any]:
    if not api_key:
        raise ValueError("Clockify API key is empty")
    url = f"{base_url.rstrip('/')}/v1/user"
    payload = _http_get_json(url, api_key=api_key, timeout=timeout)
    if not isinstance(payload, dict):
        raise ClockifyAPIError("Unexpected /v1/user response shape")
    return payload


def get_time_entries(
    api_key: str,
    workspace_id: str,
    user_id: str,
    *,
    start: str | None = None,
    end: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = 15,
) -> list[dict[str, Any]]:
    if not workspace_id:
        raise ValueError("workspace_id is required")
    if not user_id:
        raise ValueError("user_id is required")

    query: dict[str, str] = {}
    if start:
        query["start"] = start
    if end:
        query["end"] = end

    path = f"/v1/workspaces/{workspace_id}/user/{user_id}/time-entries"
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"

    payload = _http_get_json(url, api_key=api_key, timeout=timeout)
    if not isinstance(payload, list):
        raise ClockifyAPIError("Unexpected time-entries response shape")
    return [entry for entry in payload if isinstance(entry, dict)]


def get_project(
    api_key: str,
    workspace_id: str,
    project_id: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = 15,
) -> dict[str, Any]:
    if not workspace_id:
        raise ValueError("workspace_id is required")
    if not project_id:
        raise ValueError("project_id is required")

    path = f"/v1/workspaces/{workspace_id}/projects/{project_id}"
    url = f"{base_url.rstrip('/')}{path}"
    payload = _http_get_json(url, api_key=api_key, timeout=timeout)
    if not isinstance(payload, dict):
        raise ClockifyAPIError("Unexpected project response shape")
    return payload


def get_workspace_users(
    api_key: str,
    workspace_id: str,
    *,
    page_size: int = 200,
    max_pages: int = 200,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = 15,
) -> list[dict[str, Any]]:
    if not workspace_id:
        raise ValueError("workspace_id is required")
    if page_size <= 0:
        raise ValueError("page_size must be > 0")
    if max_pages <= 0:
        raise ValueError("max_pages must be > 0")

    path = f"/v1/workspaces/{workspace_id}/users"
    root_url = f"{base_url.rstrip('/')}{path}"
    out: list[dict[str, Any]] = []

    for page in range(1, max_pages + 1):
        query = urllib.parse.urlencode({"page": page, "page-size": page_size})
        url = f"{root_url}?{query}"
        payload = _http_get_json(url, api_key=api_key, timeout=timeout)
        if not isinstance(payload, list):
            raise ClockifyAPIError("Unexpected workspace users response shape")
        page_users = [user for user in payload if isinstance(user, dict)]
        if not page_users:
            break
        out.extend(page_users)
        if len(payload) < page_size:
            break

    return out


def get_workspace_projects(
    api_key: str,
    workspace_id: str,
    *,
    page_size: int = 200,
    max_pages: int = 200,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = 15,
) -> list[dict[str, Any]]:
    if not workspace_id:
        raise ValueError("workspace_id is required")
    if page_size <= 0:
        raise ValueError("page_size must be > 0")
    if max_pages <= 0:
        raise ValueError("max_pages must be > 0")

    path = f"/v1/workspaces/{workspace_id}/projects"
    root_url = f"{base_url.rstrip('/')}{path}"
    out: list[dict[str, Any]] = []

    for page in range(1, max_pages + 1):
        query = urllib.parse.urlencode({"page": page, "page-size": page_size})
        url = f"{root_url}?{query}"
        payload = _http_get_json(url, api_key=api_key, timeout=timeout)
        if not isinstance(payload, list):
            raise ClockifyAPIError("Unexpected workspace projects response shape")
        page_projects = [project for project in payload if isinstance(project, dict)]
        if not page_projects:
            break
        out.extend(page_projects)
        if len(payload) < page_size:
            break

    return out


def create_time_entry(
    api_key: str,
    workspace_id: str,
    *,
    start: str,
    end: str,
    description: str,
    project_id: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = 15,
) -> dict[str, Any]:
    if not api_key:
        raise ValueError("Clockify API key is empty")
    if not workspace_id:
        raise ValueError("workspace_id is required")
    if not start:
        raise ValueError("start is required")
    if not end:
        raise ValueError("end is required")

    path = f"/v1/workspaces/{workspace_id}/time-entries"
    url = f"{base_url.rstrip('/')}{path}"
    payload = {
        "start": start,
        "end": end,
        "description": description,
    }
    if project_id:
        payload["projectId"] = project_id
    response = _http_post_json(url, api_key=api_key, payload=payload, timeout=timeout)
    if not isinstance(response, dict):
        raise ClockifyAPIError("Unexpected create time-entry response shape")
    return response
