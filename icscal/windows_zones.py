"""
windows_zones.py
----------------
Windows timezone name → IANA timezone name mapping.

Primary source: CLDR windowsZones.xml (Unicode CLDR project)
  https://github.com/unicode-org/cldr/blob/main/common/supplemental/windowsZones.xml

The XML is fetched once at import time and cached in memory.
If the fetch fails (no network, timeout), a built-in fallback table is used.

Usage:
    from windows_zones import windows_to_iana

    iana = windows_to_iana("Eastern Standard Time")   # → "America/New_York"
    iana = windows_to_iana("Unknown Zone")             # → None
"""

from __future__ import annotations

import logging
import threading
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CLDR source URL
# ---------------------------------------------------------------------------
CLDR_URL = (
    "https://raw.githubusercontent.com/unicode-org/cldr/main"
    "/common/supplemental/windowsZones.xml"
)
_FETCH_TIMEOUT = 10  # seconds

# ---------------------------------------------------------------------------
# Built-in fallback table (subset — covers the most common Exchange / Outlook
# timezone names).  This is used when the CLDR fetch fails.
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
# Cache — populated on first use
# ---------------------------------------------------------------------------
_cache: Optional[dict[str, str]] = None
_cache_lock = threading.Lock()


def _parse_cldr_xml(xml_bytes: bytes) -> dict[str, str]:
    """Parse CLDR windowsZones.xml bytes → {windows_name: iana_name}."""
    root = ET.fromstring(xml_bytes)
    mapping: dict[str, str] = {}
    for mz in root.iter("mapZone"):
        # territory="001" is the primary / canonical mapping
        if mz.get("territory") != "001":
            continue
        windows_name = mz.get("other", "").strip()
        # type can be space-separated list; take the first (primary) zone
        iana_name = mz.get("type", "").split()[0].strip()
        if windows_name and iana_name:
            mapping[windows_name] = iana_name
    return mapping


def _load() -> dict[str, str]:
    """Fetch CLDR XML and parse it. Returns fallback dict on any error."""
    try:
        req = urllib.request.Request(
            CLDR_URL,
            headers={"User-Agent": "CalendarLoader/1.0"},
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            xml_bytes = resp.read()
        mapping = _parse_cldr_xml(xml_bytes)
        logger.info("Loaded %d Windows→IANA mappings from CLDR", len(mapping))
        return mapping
    except Exception as exc:
        logger.warning(
            "Could not fetch CLDR windowsZones.xml (%s) — using built-in fallback (%d entries)",
            exc,
            len(_FALLBACK),
        )
        return dict(_FALLBACK)


def _get_mapping() -> dict[str, str]:
    """Return cached mapping, loading it on first call (thread-safe)."""
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = _load()
    return _cache


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
    Force a reload of the CLDR mapping (e.g. after a network outage).

    Parameters
    ----------
    use_fallback : If True, skip the network fetch and reset to built-in table.

    Returns
    -------
    Number of entries loaded.
    """
    global _cache
    with _cache_lock:
        if use_fallback:
            _cache = dict(_FALLBACK)
        else:
            _cache = _load()
        return len(_cache)