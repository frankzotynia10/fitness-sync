import os
import sys
import json
import datetime

import requests
import psycopg2
from dotenv import load_dotenv
from icalendar import Calendar
import recurring_ical_events

load_dotenv()

DB_HOST = os.environ["DB_HOST"]
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ["DB_PASSWORD"]

# How far back/forward to expand recurring events
WINDOW_PAST_DAYS = int(os.environ.get("WINDOW_PAST_DAYS", "7"))
WINDOW_FUTURE_DAYS = int(os.environ.get("WINDOW_FUTURE_DAYS", "90"))

# CALENDAR_FEEDS format: name1=url1,name2=url2,...
# e.g. fz_o365=https://outlook.office365.com/.../calendar.ics,google_frank=https://calendar.google.com/.../basic.ics
CALENDAR_FEEDS_RAW = os.environ["CALENDAR_FEEDS"]


def parse_feeds(raw):
    feeds = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        name, url = pair.split("=", 1)
        feeds[name.strip()] = url.strip()
    return feeds


def fetch_ics(url):
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def expand_events(ics_text, past_days, future_days):
    cal = Calendar.from_ical(ics_text)

    now = datetime.datetime.now(datetime.timezone.utc)
    start_window = now - datetime.timedelta(days=past_days)
    end_window = now + datetime.timedelta(days=future_days)

    events = recurring_ical_events.of(cal).between(start_window, end_window)
    return events


def normalize_event(component, source_cal):
    dtstart = component.get("dtstart").dt
    dtend_prop = component.get("dtend")
    dtend = dtend_prop.dt if dtend_prop else None

    all_day = not isinstance(dtstart, datetime.datetime)

    if all_day:
        start_time = datetime.datetime.combine(
            dtstart, datetime.time.min, tzinfo=datetime.timezone.utc
        )
        end_time = (
            datetime.datetime.combine(dtend, datetime.time.min, tzinfo=datetime.timezone.utc)
            if dtend
            else None
        )
    else:
        start_time = dtstart
        end_time = dtend

    base_uid = str(component.get("uid", ""))
    # Recurring instances share a UID across occurrences - disambiguate by start time
    event_uid = f"{base_uid}_{int(start_time.timestamp())}"

    title = str(component.get("summary", "")) or None
    location = str(component.get("location", "")) or None

    return {
        "source_cal": source_cal,
        "event_uid": event_uid,
        "title": title,
        "start_time": start_time,
        "end_time": end_time,
        "all_day": all_day,
        "location": location,
        "raw_ics": component.to_ical().decode("utf-8", errors="replace"),
    }


def sync_events(conn, events):
    with conn:
        with conn.cursor() as cur:
            for e in events:
                cur.execute(
                    """
                    insert into calendar.calendar_events (
                        source_cal,
                        event_uid,
                        title,
                        start_time,
                        end_time,
                        all_day,
                        location,
                        raw_ics,
                        last_synced
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    on conflict (source_cal, event_uid) do update set
                        title = excluded.title,
                        start_time = excluded.start_time,
                        end_time = excluded.end_time,
                        all_day = excluded.all_day,
                        location = excluded.location,
                        raw_ics = excluded.raw_ics,
                        last_synced = now();
                    """,
                    (
                        e["source_cal"],
                        e["event_uid"],
                        e["title"],
                        e["start_time"],
                        e["end_time"],
                        e["all_day"],
                        e["location"],
                        e["raw_ics"],
                    ),
                )


def main():
    try:
        feeds = parse_feeds(CALENDAR_FEEDS_RAW)
        print(f"Loaded {len(feeds)} calendar feed(s): {', '.join(feeds.keys())}")

        print("Connecting to Postgres...")
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )

        total_synced = 0

        for source_cal, url in feeds.items():
            print(f"Fetching {source_cal}...")
            ics_text = fetch_ics(url)

            print(f"Expanding events for {source_cal} "
                  f"(-{WINDOW_PAST_DAYS}d / +{WINDOW_FUTURE_DAYS}d)...")
            raw_events = expand_events(ics_text, WINDOW_PAST_DAYS, WINDOW_FUTURE_DAYS)

            normalized = [normalize_event(ev, source_cal) for ev in raw_events]
            print(f"  {len(normalized)} events in window")

            sync_events(conn, normalized)
            total_synced += len(normalized)

        conn.close()
        print(f"Calendar sync complete. {total_synced} total events synced.")

    except Exception as e:
        print(f"Calendar sync failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
    sys.exit(0)
