from datetime import date, datetime, time

import pytz
import requests
from dateutil.parser import parse as parse_dt
from flask import Flask, Response
from icalendar import Calendar, Event, vRecur

app = Flask(__name__)

EVENTS_API = "https://events-api.shackspace.de/events/"
SERIES_API = "https://events-api.shackspace.de/series/"
ROOMS_API = "https://events-api.shackspace.de/rooms/"
WIKI_BASE = "https://wiki.shackspace.de/plenum"
TZ = pytz.timezone("Europe/Berlin")


def is_plenum_today():
    """Check if today is a Plenum day.

    First checks the shackspace events API for an explicit Plenum event.
    Falls back to checking if today is Wednesday (Plenum is weekly on
    Wednesdays, generated client-side by the events-o-mat frontend).
    """
    today = date.today()
    today_str = today.isoformat()

    # Check API for explicit Plenum events
    try:
        resp = requests.get(EVENTS_API, timeout=5)
        resp.raise_for_status()
        events = resp.json()
        for event in events:
            if "plenum" in event.get("name", "").lower():
                if event["start"].startswith(today_str):
                    return True, today_str
    except (requests.RequestException, ValueError, KeyError):
        pass

    # Fallback: Plenum is every Wednesday (weekday 2)
    if today.weekday() == 2:
        return True, today_str

    return False, today_str


PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ist heute Plenum?</title>
<style>
  body {{
    margin: 0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: orange;
    font-family: sans-serif;
  }}
  .answer {{
    font-size: 4rem;
    font-weight: bold;
    text-align: center;
  }}
  .answer a {{
    color: inherit;
    text-decoration: underline;
  }}
</style>
</head>
<body>
<div class="answer">{content}</div>
</body>
</html>"""


@app.route("/")
def index():
    is_plenum, today_str = is_plenum_today()

    if is_plenum:
        link = f"{WIKI_BASE}/{today_str}"
        content = f'<a href="{link}">Ja, vermutlich</a>'
    else:
        content = "Nein"

    return PAGE.format(content=content)


def fetch_rooms():
    """Fetch room id -> name mapping."""
    try:
        resp = requests.get(ROOMS_API, timeout=5)
        resp.raise_for_status()
        return {r["id"]: r["name"] for r in resp.json()}
    except (requests.RequestException, ValueError, KeyError):
        return {}


def parse_rrule(raw):
    """Normalize rrule string to a clean RRULE property value."""
    # Some entries have 'RRULE:' prefix, some don't
    rule = raw.replace("RRULE:", "")
    return rule


@app.route("/calendar.ics")
def ical_export():
    """Export all shackspace events and series as iCal."""
    cal = Calendar()
    cal.add("prodid", "-//shackspace//events//DE")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "shackspace Events")
    cal.add("x-wr-timezone", "Europe/Berlin")

    rooms = fetch_rooms()

    # Individual events
    try:
        resp = requests.get(EVENTS_API, timeout=10)
        resp.raise_for_status()
        for ev in resp.json():
            if ev.get("deleted"):
                continue
            e = Event()
            e.add("uid", f"event-{ev['id']}@events.shackspace.de")
            e.add("summary", ev["name"])
            e.add("dtstart", parse_dt(ev["start"]))
            e.add("dtend", parse_dt(ev["end"]))
            if ev.get("description"):
                e.add("description", ev["description"])
            room_name = rooms.get(ev.get("room"))
            if room_name:
                e.add("location", f"shackspace {room_name}")
            cal.add_component(e)
    except (requests.RequestException, ValueError):
        pass

    # Recurring series
    try:
        resp = requests.get(SERIES_API, timeout=10)
        resp.raise_for_status()
        for s in resp.json():
            if s.get("deleted"):
                continue
            e = Event()
            e.add("uid", f"series-{s['id']}@events.shackspace.de")
            e.add("summary", s["name"])
            if s.get("description"):
                e.add("description", s["description"])

            # Parse start/end times, create a dtstart anchored to
            # series created date
            h, m, sec = (int(x) for x in s["start"].split(":"))
            eh, em, esec = (int(x) for x in s["end"].split(":"))
            created = parse_dt(s["created"])
            dtstart = TZ.localize(
                datetime.combine(created.date(), time(h, m, sec))
            )
            dtend = TZ.localize(
                datetime.combine(created.date(), time(eh, em, esec))
            )
            e.add("dtstart", dtstart)
            e.add("dtend", dtend)
            e.add("rrule", vRecur.from_ical(parse_rrule(s["rrule"])))

            room_name = rooms.get(s.get("room"))
            if room_name:
                e.add("location", f"shackspace {room_name}")
            cal.add_component(e)
    except (requests.RequestException, ValueError):
        pass

    return Response(
        cal.to_ical(),
        mimetype="text/calendar",
        headers={"Content-Disposition": "attachment; filename=shackspace.ics"},
    )


if __name__ == "__main__":
    app.run()
