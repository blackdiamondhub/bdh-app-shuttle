"""Background ticker: plays shuttle reminders when they come due.

A due reminder pops up on every screen (a hub alert) and is announced on its
speakers. Night hours don't apply: a guest asked for it. (The hub keeps the
screens' own chime quiet at night; the speakers still play.)
"""
import logging
import os
import sys
import threading

from bdh_sdk import alerts

from . import config, reminders

log = logging.getLogger(__name__)

VOICE = "AvaNeural"  # same voice the announcements app uses
ALERT_TTL = 300

_wake = threading.Event()
_started = False
_start_lock = threading.Lock()


def _is_runtime(path):
    """``python -m bdh_sdk.runtime`` (the app's container) runs ``bdh_sdk/runtime/__main__.py``."""
    parts = path.replace("\\", "/").split("/")
    return parts[-3:] == ["bdh_sdk", "runtime", "__main__.py"]


def should_start(argv=None):
    """Start only in the server process (the app's container, Daphne, or runserver's reloaded child)."""
    if os.environ.get("BDH_SHUTTLE_WORKER", "").lower() in ("0", "false", "off", "no"):
        return False
    argv = sys.argv if argv is None else argv
    if argv and _is_runtime(argv[0]):
        return True
    prog = os.path.basename(argv[0]) if argv else ""
    if "daphne" in prog:
        return True
    if len(argv) > 1 and prog in ("manage.py", "django-admin", "__main__.py") and argv[1] == "runserver":
        return os.environ.get("RUN_MAIN") == "true" or "--noreload" in argv
    return False


def alert_key(rid):
    return f"reminder:{rid}"


def fire(r, minutes):
    text = reminders.message(minutes)
    alerts.push(alerts.Alert(
        app=config.APP_ID, key=alert_key(r["id"]),
        title=f"Shuttle in {reminders.minutes_phrase(minutes)}",
        body=f"To {reminders.DESTINATION} from {reminders.STOP}",
        embed_url=f"/shuttle/popup/{minutes}/", ttl=ALERT_TTL, sound=config.chime() or alerts.SILENT,
        speak=text[:alerts.MAX_SPEAK]))
    targets = reminders.targets(r)
    if targets:
        threading.Thread(target=announce, args=(text, targets), daemon=True,
                         name="shuttle-announce").start()
    else:
        log.warning("shuttle reminder %s: no allowed speakers left, screens only", r["id"])


def announce(text, targets):
    from bdh_sdk import speakers as sp
    try:
        failed = sp.announce(text, targets, volume=config.volume(), chime=config.chime(), voice=VOICE)
        if failed:
            log.warning("shuttle reminder: these speakers failed: %s", ", ".join(failed))
    except Exception:
        log.exception("shuttle reminder: speaker announcement failed")


def handle(due):
    for r, minutes in due:
        try:
            fire(r, minutes)
        except Exception:
            log.exception("shuttle reminder %s failed", r.get("id"))


def poke():
    """Wake the ticker after a change so a new deadline is picked up."""
    _wake.set()


def _run():
    while True:
        try:
            handle(reminders.tick())
            wait = reminders.next_wake()
        except Exception:
            log.exception("shuttle: reminder tick failed")
            wait = 1
        _wake.wait(min(wait, 1.0) if wait is not None else 30)
        _wake.clear()


def start():
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_run, daemon=True, name="shuttle-worker").start()