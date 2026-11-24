"""Shuttle timetable for the Altitude stop (E6, near the hot tub).

All times are Sun Peaks wall-clock time. BC is on permanent UTC-7 from
November 2026, so a fixed offset is used rather than a tz database that may
still think BC changes clocks.

Times come from the reviewed 2026/27 East timetable in data/timetable.json.
E6 pickups and Village Day Lodge departures are published stop
times on the same loop. Return arrivals at the Village are not published.
"""
import datetime as dt

from django.utils import timezone

from . import config

LOCAL = dt.timezone(dt.timedelta(hours=-7))
LEADS = (5, 10, 15, 20)
MIN_LEAD = LEADS[0]
LODGE, VILLAGE = "lodge", "village"

TIMES = {direction: tuple(trip[key] for trip in config.TIMETABLE["trips"])
         for direction, key in ((LODGE, "lookout"), (VILLAGE, "village"))}
_BREAKS = config.TIMETABLE["breaks"]
_REVISED = {trip["previous_lookout"]: trip["lookout"] for trip in config.TIMETABLE["trips"]
            if trip.get("previous_lookout") and trip["previous_lookout"] != trip["lookout"]}


def _hm(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def label(t):
    """8:30 AM"""
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def short(t):
    """8:30 (for the break notes)"""
    return f"{t.hour % 12 or 12}:{t.minute:02d}"


def local_now(now=None):
    return (now or timezone.now()).astimezone(LOCAL)


def running_on(day):
    return config.season_start() <= day <= config.season_end()


def departures(day, direction=LODGE):
    return [dt.datetime.combine(day, _hm(s), LOCAL) for s in TIMES[direction]]


def relative(delta):
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "now"
    if mins < 60:
        return f"in {mins} min"
    h, m = divmod(mins, 60)
    return f"in {h} h {m} min" if m else f"in {h} h"


def service_day(now=None):
    """The day the board shows: today while buses are still to come, else
    the next day with service. None once the season is over."""
    now = local_now(now)
    day = now.date()
    if running_on(day) and departures(day)[-1] >= now:
        return day
    nxt = max(day + dt.timedelta(days=1), config.season_start())
    return nxt if running_on(nxt) else None


def in_season(now=None):
    """True from the first to the last day of service."""
    return running_on(local_now(now).date())


def next_departure(now=None):
    """The next bus from our pickup stop, as a dict, or None off-season."""
    now = local_now(now)
    day = service_day(now)
    if day is None:
        return None
    for t in departures(day):
        if t >= now:
            return {"at": t, "iso": t.isoformat(), "time": label(t),
                    "rel": relative(t - now),
                    "today": day == now.date()}
    return None


def board(direction=LODGE, now=None):
    """Departure rows for the list. Break notes are dicts with ``brk``.

    In season the list starts at the bus that most recently left (shown
    dimmed), off-season it is the whole day as a preview.
    """
    now = local_now(now)
    today = now.date()
    day = service_day(now)
    preview = day is None or day > today + dt.timedelta(days=1)
    if day is None:
        day = config.season_start()
    times = departures(day, direction)
    rows = []
    breaks = [(_hm(a), _hm(b)) for a, b in _BREAKS]
    for i, t in enumerate(times):
        for a, b in breaks:
            if i and times[i - 1].time() < b <= t.time():
                rows.append({"brk": f"No service {short(a)} – {short(b)}"})
        past = not preview and t < now
        row = {"time": label(t), "iso": t.isoformat(), "past": past,
               "last": i == len(times) - 1, "rel": "",
               "remind": False}
        if not preview and not past:
            row["rel"] = relative(t - now) if day == today else ""
            row["remind"] = direction == LODGE and \
                (t - now) > dt.timedelta(minutes=MIN_LEAD)
        if direction == VILLAGE:
            row["arrive"] = label(_hm(TIMES[LODGE][i]))
        rows.append(row)
    if not preview:
        last_past = max((i for i, r in enumerate(rows) if r.get("past")), default=None)
        if last_past is not None:
            rows = [r for i, r in enumerate(rows) if i >= last_past]
    return {"rows": rows, "preview": preview, "day": day,
            "tomorrow": not preview and day != today}


def is_departure(iso):
    """The aware datetime for a reminder request, if it is a real upcoming
    bus from our pickup stop; else None."""
    try:
        t = dt.datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if t.tzinfo is None:
        return None
    t = t.astimezone(LOCAL)
    if not running_on(t.date()) or t not in departures(t.date()):
        return None
    return t


def revised_departure(t):
    """Move a saved reminder from the superseded stop time to the same revised trip."""
    t = t.astimezone(LOCAL)
    replacement = _REVISED.get(t.strftime("%H:%M"))
    if replacement and not t.second and not t.microsecond and running_on(t.date()):
        return dt.datetime.combine(t.date(), _hm(replacement), LOCAL)
    return None


def season_note(now=None):
    """Short text for off-season: 'Starts Nov 21' / 'Season over'."""
    now = local_now(now)
    start = config.season_start()
    if now.date() < start:
        return f"Starts {start:%b} {start.day}"
    return "Done for the season"
