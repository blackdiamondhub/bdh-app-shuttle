"""Shuttle reminders, kept by the hub itself.

A reminder is one-off: "``lead_min`` minutes before the <departure> bus
reaches our pickup stop, say so". The hub runs one server process, so a
module-level list under a lock is the source of truth; every change is written
through to ``app_state`` so reminders survive a restart. The ticker in
:mod:`shuttle.worker` calls :func:`tick` and plays whatever came due.

A reminder that comes due late (e.g. after a restart) still plays, with the
minutes recomputed, as long as the bus is at least ``MIN_LEFT_S`` away;
otherwise it is dropped. Fired reminders are forgotten.

``speakers`` is ``None`` for every speaker the admin allows (worked out when
the reminder fires) or a list of entity ids picked from them.
"""
import datetime as dt
import logging
import secrets
import threading

from bdh_sdk.storage import app_state

from . import config, schedule, stops

log = logging.getLogger(__name__)

STOP = next(s["name"] for s in stops.STOPS if s["id"] == stops.OUR_STOP)
DESTINATION = "the Village"
MESSAGE = ("Heads up! The shuttle to {destination} reaches {stop} "
           "in about {minutes}. Time to put your boots on!")
MAX_ACTIVE = 5
MIN_LEFT_S = 60

_lock = threading.RLock()
_recs = None


def enabled():
    """Reminders are on once the admin has allowed at least one speaker."""
    return bool(config.speakers())


def _state():
    return app_state(config.APP_ID)


def _load():
    global _recs
    if _recs is None:
        raw = _state().get("reminders") or []
        loaded = [dict(r) for r in raw if isinstance(r, dict) and r.get("id") and r.get("departure")]
        changed = False
        for r in loaded:
            if r.get("timetable_revision") == config.TIMETABLE["image_sha256"]:
                continue
            old = dt.datetime.fromtimestamp(r["departure"], schedule.LOCAL)
            revised = schedule.revised_departure(old)
            if revised is not None:
                r["departure"] = revised.timestamp()
                changed = True
                log.info("Retimed shuttle reminder from %s to %s after timetable revision",
                         old.isoformat(), revised.isoformat())
            r["timetable_revision"] = config.TIMETABLE["image_sha256"]
            changed = True
        if changed:
            _state().set("reminders", loaded)
        _recs = loaded
    return _recs


def _save():
    _state().set("reminders", _recs)


def reset_cache():
    """Forget the in-memory copy (tests, or after the state store changes)."""
    global _recs
    with _lock:
        _recs = None


def _now(now):
    return (now or schedule.local_now()).timestamp()


def _fire_ts(r):
    return r["departure"] - r["lead_min"] * 60


def _iso(ts):
    return dt.datetime.fromtimestamp(ts, schedule.LOCAL).isoformat(timespec="seconds")


def view(r):
    return {"id": r["id"], "departure": _iso(r["departure"]), "fire_at": _iso(_fire_ts(r)),
            "lead_min": r["lead_min"], "speakers": r.get("speakers")}


def minutes_phrase(n):
    return "1 minute" if n == 1 else f"{n} minutes"


def message(minutes):
    return MESSAGE.format(destination=DESTINATION, stop=STOP, minutes=minutes_phrase(minutes))


def clean_speakers(value, allowed):
    """``None`` (all allowed speakers) or the picked ones, which must be allowed."""
    if value is None or value == "all":
        return None
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("Pick at least one speaker.")
    picked = list(dict.fromkeys(value))
    if any(s not in allowed for s in picked):
        raise ValueError("That speaker isn't available for reminders.")
    return picked


def targets(r, allowed=None):
    """Speakers to announce ``r`` on: its own picks that are still allowed, or all allowed."""
    allowed = config.speakers() if allowed is None else allowed
    own = r.get("speakers")
    return list(allowed) if own is None else [s for s in own if s in allowed]


def pending():
    """Pending reminders as dicts for the page and tile, soonest first."""
    with _lock:
        return [view(r) for r in sorted(_load(), key=_fire_ts)]


def add(departure, lead_min, speakers=None, now=None):
    """Remind ``lead_min`` minutes before ``departure`` (an aware datetime).

    Setting one for a bus that already has a reminder updates it. Raises
    ValueError with a message for the guest.
    """
    picked = clean_speakers(speakers, config.speakers())
    now_ts = _now(now)
    dep = departure.timestamp()
    if dep - lead_min * 60 <= now_ts:
        raise ValueError(f"Too late for a {lead_min}-minute heads-up.")
    with _lock:
        recs = _load()
        rec = next((r for r in recs if r["departure"] == dep), None)
        if rec is None:
            if len(recs) >= MAX_ACTIVE:
                raise ValueError(f"You can have up to {MAX_ACTIVE} reminders at once.")
            rec = {"id": secrets.token_hex(4), "departure": dep, "created": now_ts}
            recs.append(rec)
        rec.update(lead_min=lead_min, speakers=picked,
                   timetable_revision=config.TIMETABLE["image_sha256"])
        _save()
        log.info("shuttle reminder %s set: %s bus, %d min before", rec["id"], _iso(dep), lead_min)
        return view(rec)


def cancel(rid):
    """Cancel reminder ``rid``; False if there is no such reminder."""
    with _lock:
        recs = _load()
        keep = [r for r in recs if r["id"] != rid]
        if len(keep) == len(recs):
            return False
        recs[:] = keep
        _save()
        return True


def tick(now=None):
    """Take the reminders that are due. Returns ``[(reminder, minutes_left)]`` to play."""
    now_ts = _now(now)
    due, out = [], []
    with _lock:
        recs = _load()
        for r in recs:
            if _fire_ts(r) <= now_ts:
                due.append(r)
        if not due:
            return []
        recs[:] = [r for r in recs if r not in due]
        _save()
    for r in due:
        left = r["departure"] - now_ts
        if left < MIN_LEFT_S:
            log.warning("shuttle reminder %s skipped: the %s bus already left", r["id"], _iso(r["departure"]))
        else:
            out.append((r, max(1, round(left / 60))))
    return out


def next_wake(now=None):
    """Seconds until the next reminder is due, or None if there are none."""
    with _lock:
        recs = _load()
        if not recs:
            return None
        return max(0.0, min(_fire_ts(r) for r in recs) - _now(now))
