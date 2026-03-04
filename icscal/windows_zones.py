"""
windows_zones.py
----------------
Windows timezone name → IANA timezone name mapping.

Primary source: CLDR windowsZones.xml (Unicode CLDR project)
  https://github.com/unicode-org/cldr/blob/main/common/supplemental/windowsZones.xml

Cache modes
-----------
By default the mapping is kept **in memory only** (suitable for AWS Lambda
where the filesystem is read-only or ephemeral).

When ``configure(file_cache=True, cache_path="/some/path/windows_zones.json")``
is called, the mapping is additionally persisted to a JSON file and refreshed
from CLDR at most once every ``cache_ttl_seconds`` (default 86400 = 24 h).
This is the recommended mode for long-running local processes.

Decision table
--------------
  file_cache=False (default)  → memory-only, fetch from CLDR once per process
  file_cache=True             → read from / write to JSON file;
                                re-fetch from CLDR when file is older than TTL

Typical setup
-------------
  # Lambda (no persistent FS, memory cache is fine):
  from windows_zones import windows_to_iana

  # Local / server (persist to disk, refresh daily):
  from windows_zones import configure, windows_to_iana
  configure(file_cache=True, cache_path="/var/cache/windows_zones.json")

  iana = windows_to_iana("Eastern Standard Time")  # → "America/New_York"
  iana = windows_to_iana("Unknown Zone")            # → None
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CLDR source
# ---------------------------------------------------------------------------
CLDR_URL = (
    "https://raw.githubusercontent.com/unicode-org/cldr/main"
    "/common/supplemental/windowsZones.xml"
)
_FETCH_TIMEOUT = 10  # seconds

# ---------------------------------------------------------------------------
# Configuration (mutable via configure())
# ---------------------------------------------------------------------------
_file_cache: bool = False
_cache_path: str = os.path.join(os.path.dirname(__file__), "windows_zones_cache.json")
_cache_ttl_seconds: int = 86_400  # 24 hours

# ---------------------------------------------------------------------------
# Built-in fallback table — used when network and file cache are unavailable.
# Sourced from CLDR 45 (2024).
# ---------------------------------------------------------------------------
_FALLBACK: dict[str, str] = {
    "Afghanistan Standard Time": "Asia/Kabul",
    "Alaskan Standard Time": "America/Anchorage",
    "Aleutian Standard Time": "America/Adak",
    "Altai Standard Time": "Asia/Barnaul",
    "Arab Standard Time": "Asia/Riyadh",
    "Arabian Standard Time": "Asia/Dubai",
    "Arabic Standard Time": "Asia/Baghdad",
    "Argentina Standard Time": "America/Argentina/Buenos_Aires",
    "Astrakhan Standard Time": "Europe/Astrakhan",
    "Atlantic Standard Time": "America/Halifax",
    "AUS Central Standard Time": "Australia/Darwin",
    "Aus Central W. Standard Time": "Australia/Eucla",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "Azerbaijan Standard Time": "Asia/Baku",
    "Azores Standard Time": "Atlantic/Azores",
    "Bahia Standard Time": "America/Bahia",
    "Bangladesh Standard Time": "Asia/Dhaka",
    "Canada Central Standard Time": "America/Regina",
    "Cape Verde Standard Time": "Atlantic/Cape_Verde",
    "Caucasus Standard Time": "Asia/Yerevan",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "Central America Standard Time": "America/Guatemala",
    "Central Asia Standard Time": "Asia/Almaty",
    "Central Brazilian Standard Time": "America/Cuiaba",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "Central Pacific Standard Time": "Pacific/Guadalcanal",
    "Central Standard Time": "America/Chicago",
    "Central Standard Time (Mexico)": "America/Mexico_City",
    "Chatham Islands Standard Time": "Pacific/Chatham",
    "China Standard Time": "Asia/Shanghai",
    "Cuba Standard Time": "America/Havana",
    "Dateline Standard Time": "Etc/GMT+12",
    "E. Africa Standard Time": "Africa/Nairobi",
    "E. Australia Standard Time": "Australia/Brisbane",
    "E. Europe Standard Time": "Asia/Nicosia",
    "E. South America Standard Time": "America/Sao_Paulo",
    "Easter Island Standard Time": "Pacific/Easter",
    "Eastern Standard Time": "America/New_York",
    "Eastern Standard Time (Mexico)": "America/Cancun",
    "Egypt Standard Time": "Africa/Cairo",
    "Ekaterinburg Standard Time": "Asia/Yekaterinburg",
    "Fiji Standard Time": "Pacific/Fiji",
    "FLE Standard Time": "Europe/Kiev",
    "Georgian Standard Time": "Asia/Tbilisi",
    "GMT Standard Time": "Europe/London",
    "Greenland Standard Time": "America/Godthab",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "GTB Standard Time": "Europe/Bucharest",
    "Gulf Standard Time": "Asia/Dubai",
    "Haiti Standard Time": "America/Port-au-Prince",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "India Standard Time": "Asia/Calcutta",
    "Iran Standard Time": "Asia/Tehran",
    "Israel Standard Time": "Asia/Jerusalem",
    "Jordan Standard Time": "Asia/Amman",
    "Kaliningrad Standard Time": "Europe/Kaliningrad",
    "Korea Standard Time": "Asia/Seoul",
    "Libya Standard Time": "Africa/Tripoli",
    "Line Islands Standard Time": "Pacific/Kiritimati",
    "Lord Howe Standard Time": "Australia/Lord_Howe",
    "Magadan Standard Time": "Asia/Magadan",
    "Marquesas Standard Time": "Pacific/Marquesas",
    "Mauritius Standard Time": "Indian/Mauritius",
    "Middle East Standard Time": "Asia/Beirut",
    "Montevideo Standard Time": "America/Montevideo",
    "Morocco Standard Time": "Africa/Casablanca",
    "Mountain Standard Time": "America/Denver",
    "Mountain Standard Time (Mexico)": "America/Chihuahua",
    "Myanmar Standard Time": "Asia/Rangoon",
    "N. Central Asia Standard Time": "Asia/Novosibirsk",
    "Namibia Standard Time": "Africa/Windhoek",
    "Nepal Standard Time": "Asia/Katmandu",
    "New Zealand Standard Time": "Pacific/Auckland",
    "Newfoundland Standard Time": "America/St_Johns",
    "Norfolk Standard Time": "Pacific/Norfolk",
    "North Asia East Standard Time": "Asia/Irkutsk",
    "North Asia Standard Time": "Asia/Krasnoyarsk",
    "North Korea Standard Time": "Asia/Pyongyang",
    "Omsk Standard Time": "Asia/Omsk",
    "Pacific SA Standard Time": "America/Santiago",
    "Pacific Standard Time": "America/Los_Angeles",
    "Pacific Standard Time (Mexico)": "America/Tijuana",
    "Pakistan Standard Time": "Asia/Karachi",
    "Paraguay Standard Time": "America/Asuncion",
    "Qyzylorda Standard Time": "Asia/Qyzylorda",
    "Romance Standard Time": "Europe/Paris",
    "Russia Time Zone 10": "Asia/Srednekolymsk",
    "Russia Time Zone 11": "Asia/Kamchatka",
    "Russia Time Zone 3": "Europe/Samara",
    "Russian Standard Time": "Europe/Moscow",
    "SA Eastern Standard Time": "America/Cayenne",
    "SA Pacific Standard Time": "America/Bogota",
    "SA Western Standard Time": "America/La_Paz",
    "Saint Pierre Standard Time": "America/Miquelon",
    "Sakhalin Standard Time": "Asia/Sakhalin",
    "SE Asia Standard Time": "Asia/Bangkok",
    "Singapore Standard Time": "Asia/Singapore",
    "South Africa Standard Time": "Africa/Johannesburg",
    "South Sudan Standard Time": "Africa/Juba",
    "Sri Lanka Standard Time": "Asia/Colombo",
    "Sudan Standard Time": "Africa/Khartoum",
    "Syria Standard Time": "Asia/Damascus",
    "Taipei Standard Time": "Asia/Taipei",
    "Tasmania Standard Time": "Australia/Hobart",
    "Tocantins Standard Time": "America/Araguaina",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Tomsk Standard Time": "Asia/Tomsk",
    "Tonga Standard Time": "Pacific/Tongatapu",
    "Transbaikal Standard Time": "Asia/Chita",
    "Turkey Standard Time": "Europe/Istanbul",
    "Turks And Caicos Standard Time": "America/Grand_Turk",
    "Ulaanbaatar Standard Time": "Asia/Ulaanbaatar",
    "US Eastern Standard Time": "America/Indianapolis",
    "US Mountain Standard Time": "America/Phoenix",
    "UTC": "UTC",
    "UTC+12": "Etc/GMT-12",
    "UTC+13": "Pacific/Apia",
    "UTC-02": "Etc/GMT+2",
    "UTC-08": "Etc/GMT+8",
    "UTC-09": "Etc/GMT+9",
    "UTC-11": "Etc/GMT+11",
    "Venezuela Standard Time": "America/Caracas",
    "Vladivostok Standard Time": "Asia/Vladivostok",
    "Volgograd Standard Time": "Europe/Volgograd",
    "W. Australia Standard Time": "Australia/Perth",
    "W. Central Africa Standard Time": "Africa/Lagos",
    "W. Europe Standard Time": "Europe/Berlin",
    "W. Mongolia Standard Time": "Asia/Hovd",
    "West Asia Standard Time": "Asia/Tashkent",
    "West Bank Standard Time": "Asia/Hebron",
    "West Pacific Standard Time": "Pacific/Port_Moresby",
    "Yakutsk Standard Time": "Asia/Yakutsk",
    "Yukon Standard Time": "America/Whitehorse",
}

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
_mem_cache: Optional[dict[str, str]] = None
_cache_lock = threading.Lock()

# JSON file schema: {"fetched_at": <unix timestamp float>, "mapping": {...}}
_CACHE_KEY_TS = "fetched_at"
_CACHE_KEY_MAP = "mapping"


# ---------------------------------------------------------------------------
# Public configuration
# ---------------------------------------------------------------------------

def configure(
    *,
    file_cache: bool = False,
    cache_path: Optional[str] = None,
    cache_ttl_seconds: int = 86_400,
) -> None:
    """
    Configure caching behaviour.  Call this once at application startup,
    before the first call to ``windows_to_iana()``.

    Parameters
    ----------
    file_cache : bool
        False (default) — memory-only cache, ideal for AWS Lambda.
        True            — persist mapping to a JSON file and refresh it
                          when it is older than ``cache_ttl_seconds``.
    cache_path : str | None
        Path to the JSON cache file.  Defaults to
        ``windows_zones_cache.json`` next to this module.
        Ignored when ``file_cache=False``.
    cache_ttl_seconds : int
        How many seconds a file cache entry is considered fresh.
        Default: 86400 (24 hours).
    """
    global _file_cache, _cache_path, _cache_ttl_seconds, _mem_cache
    with _cache_lock:
        _file_cache = file_cache
        if cache_path is not None:
            _cache_path = cache_path
        _cache_ttl_seconds = cache_ttl_seconds
        # Invalidate memory cache so next call picks up new settings
        _mem_cache = None
    logger.debug(
        "windows_zones configured: file_cache=%s, cache_path=%r, ttl=%ds",
        file_cache, _cache_path, cache_ttl_seconds,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_cldr_xml(xml_bytes: bytes) -> dict[str, str]:
    """Parse CLDR windowsZones.xml bytes → {windows_name: iana_name}."""
    root = ET.fromstring(xml_bytes)
    mapping: dict[str, str] = {}
    for mz in root.iter("mapZone"):
        if mz.get("territory") != "001":  # primary/canonical mapping only
            continue
        windows_name = mz.get("other", "").strip()
        iana_name = mz.get("type", "").split()[0].strip()
        if windows_name and iana_name:
            mapping[windows_name] = iana_name
    return mapping


def _fetch_from_cldr() -> Optional[dict[str, str]]:
    """Download and parse CLDR XML. Returns None on any error."""
    try:
        req = urllib.request.Request(
            CLDR_URL,
            headers={"User-Agent": "CalendarLoader/1.0"},
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            xml_bytes = resp.read()
        mapping = _parse_cldr_xml(xml_bytes)
        logger.info("Fetched %d Windows→IANA mappings from CLDR", len(mapping))
        return mapping
    except Exception as exc:
        logger.warning("Could not fetch CLDR windowsZones.xml: %s", exc)
        return None


def _read_file_cache() -> Optional[tuple[dict[str, str], float]]:
    """
    Read the JSON file cache.
    Returns (mapping, fetched_at_timestamp) or None if unavailable/corrupt.
    """
    try:
        with open(_cache_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        mapping = data[_CACHE_KEY_MAP]
        fetched_at = float(data[_CACHE_KEY_TS])
        return mapping, fetched_at
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Could not read windows_zones file cache %r: %s", _cache_path, exc)
        return None


def _write_file_cache(mapping: dict[str, str]) -> None:
    """Persist mapping to JSON file with a timestamp."""
    try:
        data = {_CACHE_KEY_TS: time.time(), _CACHE_KEY_MAP: mapping}
        tmp_path = _cache_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _cache_path)  # atomic on POSIX; best-effort on Windows
        logger.debug("Wrote windows_zones file cache to %r (%d entries)", _cache_path, len(mapping))
    except Exception as exc:
        logger.warning("Could not write windows_zones file cache %r: %s", _cache_path, exc)


def _is_file_cache_fresh(fetched_at: float) -> bool:
    return (time.time() - fetched_at) < _cache_ttl_seconds


def _load() -> dict[str, str]:
    """
    Load mapping according to current configuration.

    file_cache=False:
        Fetch from CLDR → fallback on error.

    file_cache=True:
        1. Read file cache.
        2. If fresh → use it.
        3. If stale/missing → fetch from CLDR.
           a. Success → save to file, use result.
           b. Failure → use stale file cache if available, else fallback.
    """
    if not _file_cache:
        # Memory-only mode (Lambda / stateless)
        result = _fetch_from_cldr()
        if result is not None:
            return result
        logger.warning("Using built-in fallback (%d entries)", len(_FALLBACK))
        return dict(_FALLBACK)

    # File-cache mode (local / server)
    cached = _read_file_cache()

    if cached is not None:
        mapping, fetched_at = cached
        if _is_file_cache_fresh(fetched_at):
            age_h = (time.time() - fetched_at) / 3600
            logger.debug("Using fresh file cache (%.1fh old, %d entries)", age_h, len(mapping))
            return mapping
        else:
            age_h = (time.time() - fetched_at) / 3600
            logger.info("File cache is stale (%.1fh old) — refreshing from CLDR", age_h)
    else:
        logger.info("No file cache found — fetching from CLDR")

    # Try to refresh from CLDR
    fresh = _fetch_from_cldr()
    if fresh is not None:
        _write_file_cache(fresh)
        return fresh

    # CLDR unreachable — use stale file cache or built-in fallback
    if cached is not None:
        logger.warning(
            "CLDR fetch failed — using stale file cache (%d entries)", len(cached[0])
        )
        return cached[0]

    logger.warning(
        "CLDR fetch failed and no file cache — using built-in fallback (%d entries)",
        len(_FALLBACK),
    )
    return dict(_FALLBACK)


def _get_mapping() -> dict[str, str]:
    """Return in-memory cached mapping, loading it on first call (thread-safe)."""
    global _mem_cache
    if _mem_cache is None:
        with _cache_lock:
            if _mem_cache is None:
                _mem_cache = _load()
    return _mem_cache


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def windows_to_iana(windows_name: str) -> Optional[str]:
    """
    Convert a Windows timezone name to an IANA timezone name.
    Returns None if the name is not found.

    Examples
    --------
    >>> windows_to_iana("Eastern Standard Time")
    'America/New_York'
    >>> windows_to_iana("Pacific Standard Time")
    'America/Los_Angeles'
    >>> windows_to_iana("Nonexistent Zone")
    None
    """
    return _get_mapping().get(windows_name)


def reload(*, use_fallback: bool = False) -> int:
    """
    Force a reload of the mapping, bypassing the in-memory cache.

    Parameters
    ----------
    use_fallback : If True, reset to the built-in table without any I/O.

    Returns
    -------
    Number of entries loaded.
    """
    global _mem_cache
    with _cache_lock:
        if use_fallback:
            _mem_cache = dict(_FALLBACK)
        else:
            _mem_cache = _load()
        return len(_mem_cache)


def cache_info() -> dict:
    """
    Return diagnostic information about the current cache state.

    Returns
    -------
    dict with keys:
        mode          : "memory" | "file"
        entries       : int — number of mappings in memory
        cache_path    : str | None — path to file cache (if enabled)
        ttl_seconds   : int
        file_exists   : bool
        file_age_seconds : float | None
        file_is_fresh : bool | None
    """
    info: dict = {
        "mode": "file" if _file_cache else "memory",
        "entries": len(_mem_cache) if _mem_cache is not None else 0,
        "cache_path": _cache_path if _file_cache else None,
        "ttl_seconds": _cache_ttl_seconds,
        "file_exists": None,
        "file_age_seconds": None,
        "file_is_fresh": None,
    }
    if _file_cache:
        cached = _read_file_cache()
        if cached is not None:
            age = time.time() - cached[1]
            info["file_exists"] = True
            info["file_age_seconds"] = round(age, 1)
            info["file_is_fresh"] = _is_file_cache_fresh(cached[1])
        else:
            info["file_exists"] = False
    return info
