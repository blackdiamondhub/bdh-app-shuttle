"""Assistant tool: the next Sun Peaks shuttle, for the hub's voice/chat assistant.

One read-only lookup for every mode, from the reviewed East loop timetable:
the next few buses from the lodge's stop (Altitude, E6) to the Village Day
Lodge, or back from the Village, with the first and last bus of the day.
Answers are small dicts of speech-ready strings in lodge time ("4:45 pm").
"""
from __future__ import annotations

import datetime as dt

from bdh_sdk import ALL_MODES
from bdh_sdk.tools import Tool, ToolError

from . import config, schedule

NEXT_SHUTTLE = "next_shuttle"
TO_VILLAGE, TO_LODGE = "to_village", "to_lodge"
TODAY, TOMORROW = "today", "tomorrow"
COUNT = 3
OUR_STOP = "Altitude stop (E6, by the hot tub)"
VILLAGE_STOP = "Village Day Lodge"
NO_ARRIVAL = "not published; the bus loops past the other East stops before the Village"


def tools(mode=None):
    return [
        Tool(NEXT_SHUTTLE, "Next shuttle",
             "When the free Sun Peaks shuttle leaves: the next buses from the lodge's stop to the Village "
             "(or back), and the first and last bus of the day. Use for 'when's the next shuttle?', 'when "
             "does the shuttle stop running?', 'first bus tomorrow?'.",
             params={"type": "object", "properties": {
                 "direction": {"type": "string", "enum": [TO_VILLAGE, TO_LODGE],
                               "description": "to_village: from the lodge's stop (Altitude) to the Village "
                                              "(the default). to_lodge: from the Village Day Lodge back "
                                              "to the lodge."},
                 "day": {"type": "string", "enum": [TODAY, TOMORROW],
                         "description": "tomorrow: tomorrow's whole timetable. Default: the next buses from now."},
             }},
             modes=ALL_MODES, readonly=True),
    ]


# ── words ──

def _clock(t):
    if t.hour == 12 and t.minute == 0:
        return "noon"
    minutes = f":{t:%M}" if t.minute else ""
    return f"{t.hour % 12 or 12}{minutes} {'am' if t.hour < 12 else 'pm'}"


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _until(delta):
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "leaving now"
    h, m = divmod(mins, 60)
    if not h:
        return f"in {_plural(m, 'minute')}"
    return f"in {_plural(h, 'hour')}" + (f" {_plural(m, 'minute')}" if m else "")


def _date(day, today):
    if day == today:
        return "today"
    if day == today + dt.timedelta(days=1):
        return "tomorrow"
    return f"{day:%A}, {day:%B} {day.day}"


# ── the timetable ──

def _buses(day, direction):
    """``[(leaves, arrives or None)]`` from the direction's stop on ``day``."""
    pickups = schedule.departures(day, schedule.LODGE)
    if direction == TO_LODGE:
        return list(zip(schedule.departures(day, schedule.VILLAGE), pickups))
    return [(t, None) for t in pickups]


def _bus(leaves, arrives, now=None):
    text = _clock(leaves)
    if now is not None:
        text += f", {_until(leaves - now)}"
    if arrives is not None:
        text += f", reaching the lodge about {_clock(arrives)}"
    return text


def _run(args, now):
    direction = args.get("direction") or TO_VILLAGE
    today = now.date()
    out = {"from": OUR_STOP, "to": VILLAGE_STOP} if direction == TO_VILLAGE else \
        {"from": VILLAGE_STOP, "to": OUR_STOP}
    start, end = config.season_start(), config.season_end()
    day = today + dt.timedelta(days=1) if args.get("day") == TOMORROW else today
    if day > end:
        raise ToolError(f"The shuttle is done for the season; its last day was {end:%A}, {end:%B} {end.day}.")
    if day < start:
        out["note"] = f"The shuttle isn't running yet this season; it starts {_date(start, today)}."
        day = start
    buses = _buses(day, direction)
    out["day"] = _date(day, today)
    out["first_bus"] = _clock(buses[0][0])
    out["last_bus"] = _clock(buses[-1][0])
    if day != today:
        out["times"] = [_bus(*b) for b in buses]
    else:
        later = [b for b in buses if b[0] >= now]
        if later:
            out["next"] = [_bus(*b, now=now) for b in later[:COUNT]]
            if len(later) <= COUNT:
                out["note"] = f"The last bus today is at {_clock(later[-1][0])}."
        else:
            tomorrow = today + dt.timedelta(days=1)
            if not schedule.running_on(tomorrow):
                raise ToolError(f"No more shuttles today; the last one left at {_clock(buses[-1][0])}, "
                                f"and that was the last day of the season.")
            nxt = _buses(tomorrow, direction)
            out.update(day=TOMORROW, first_bus=_clock(nxt[0][0]), last_bus=_clock(nxt[-1][0]),
                       next=[_bus(*b) for b in nxt[:COUNT]],
                       note=f"No more shuttles today; the last one left at {_clock(buses[-1][0])}.")
    if direction == TO_VILLAGE:
        out["arrival_at_village"] = NO_ARRIVAL
    return out


def run_tool(tool_id, args, mode=None):
    if tool_id != NEXT_SHUTTLE:
        raise ToolError(f"Unknown tool {tool_id!r}")
    return _run(args or {}, schedule.local_now())
