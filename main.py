from icscal.calendar_loader  import get_events_for_day
from datetime import date

def main():
    events = get_events_for_day(
        calendar_urls=[
            "https://outlook.office365.com/owa/calendar/47941498b2da421ea18bf81b66bdb562@avidi.tech/bdefaf6db48f49579e5e71ec2d3fc7bf17237130955837215194/calendar.ics",
            "https://calendar.google.com/calendar/ical/o.moiseenko%40takeprofittrader.com/private-337b571b6f4095b025534b19759e3ba1/basic.ics",
        ],
        user_timezone="Asia/Nicosia",  # IANA or Windows tz name
        target_date=date.fromisoformat("2026-03-04"),
    )

    for e in events:
        print(e["summary"], e["start_iso"],
              "current " if e["is_current"] else "",
              "next " if e["is_next"] else "",
              "next-overlapping " if e["is_next_overlapping"] else "",
              "next-non-overlapping " if e["is_next_non_overlapping"] else "")



if __name__ == '__main__':
    main()


