"""Settings for the shuttle app (``BDH_SHUTTLE_*``)."""
import datetime as dt
import json
from pathlib import Path

from bdh_sdk import alerts
from bdh_sdk.plugins import as_list
from bdh_sdk.settings import app_setting

APP_ID = "shuttle"
TIMETABLE = json.loads((Path(__file__).parent / "data" / "timetable.json").read_text(encoding="utf-8"))


def _date(key, default):
    value = app_setting(APP_ID, key, "")
    if value:
        try:
            return dt.date.fromisoformat(str(value))
        except ValueError:
            pass
    return default


def season_start():
    """First day of winter service (2026/27 season)."""
    return _date("season_start", dt.date.fromisoformat(TIMETABLE["season_start"]))


def season_end():
    """Last day of winter service."""
    return _date("season_end", dt.date.fromisoformat(TIMETABLE["season_end"]))


def speakers():
    """Speakers reminders may use (entity ids). None set hides reminders."""
    return as_list(app_setting(APP_ID, "speakers", None))


def volume():
    """Announcement volume (0-100), or None to leave each speaker's volume alone."""
    v = app_setting(APP_ID, "volume", 0, cast=int)
    return v if v and 0 < v <= 100 else None

def chime():
    """Chime before the announcement, or None for none."""
    value = app_setting(APP_ID, "chime", "soft-bell")
    if value == alerts.SILENT:
        return None
    return value if value in alerts.CHIMES else "soft-bell"


def skikrumb_key():
    """skiKrumb's public (anon) API key. Empty: read it from their embed page."""
    return app_setting(APP_ID, "skikrumb_key", "")
