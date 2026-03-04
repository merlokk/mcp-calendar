from icscal.calendar_loader  import get_events_for_day
from datetime import date, datetime
import pytz

from icscal.windows_zones import configure, cache_info
configure(
    file_cache=True,
    cache_path="./windows_zones.json",  # опционально
    cache_ttl_seconds=86_400,           # 24ч по умолчанию
)

def _to_local(iso_utc: str, tz: pytz.BaseTzInfo) -> datetime:
    """
    ISO 8601 -> aware datetime -> convert to tz.
    start_iso/end_iso приходят как ISO строки с +00:00 (UTC).
    """
    dt_utc = datetime.fromisoformat(iso_utc)
    if dt_utc.tzinfo is None:
        # на всякий случай, если строка вдруг без смещения
        dt_utc = dt_utc.replace(tzinfo=pytz.utc)
    return dt_utc.astimezone(tz)


def main():
    user_tz_name = "Asia/Nicosia"
    tz = pytz.timezone(user_tz_name)

    events = get_events_for_day(
        calendar_urls=[
            "https://outlook.office365.com/owa/calendar/47941498b2da421ea18bf81b66bdb562@avidi.tech/bdefaf6db48f49579e5e71ec2d3fc7bf17237130955837215194/calendar.ics",
            "https://calendar.google.com/calendar/ical/o.moiseenko%40takeprofittrader.com/private-337b571b6f4095b025534b19759e3ba1/basic.ics",
        ],
        user_timezone="Asia/Nicosia",  # IANA or Windows tz name
        # target_date=datetime.fromisoformat("2026-02-13T00:00:01+02:00"),
        target_date=datetime.fromisoformat("2026-03-02T15:00:01+02:00"),
    )

    for e in events:
        print(e["calendar_id"],e["summary"],
              _to_local(e["start_iso"], tz), _to_local(e["end_iso"], tz),
              "current " if e["is_current"] else "",
              "next " if e["is_next"] else "",
              "next-overlapping " if e["is_next_overlapping"] else ""
              )


if __name__ == '__main__':
    main()


