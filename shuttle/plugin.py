from bdh_sdk import PluginSpec, Setting, Tile
from bdh_sdk.alerts import CHIMES
from bdh_sdk.plugins import CHOICE, INT, LIFECYCLE_ALWAYS, MULTI, NETWORK_OUTBOUND

CHIME_CHOICES = (("none", "No chime"),) + tuple(
    (c, c.replace("-", " ").capitalize()) for c in CHIMES)

spec = PluginSpec(
    id="shuttle",
    app="shuttle",
    urls="shuttle.urls",
    status="shuttle.homepage.status",
    tiles=(
        Tile(
            title="Shuttle",
            url_name="shuttle",
            icon="shuttle/hub/icon.svg",
            section="mountain",
            subtitle="Free bus to the Village",
            order=45,
        ),
    ),
    settings=(
        Setting("season_start", "First day of winter service", default="2026-11-21",
                help="YYYY-MM-DD."),
        Setting("season_end", "Last day of winter service", default="2027-04-04",
                help="YYYY-MM-DD."),
        Setting("speakers", "Speakers for reminders", type=MULTI, default=None,
                choices_from="bdh_sdk.speakers.list_speakers",
                help="Guests pick from these (or all of them) when they set a reminder. "
                     "Leave empty to turn reminders off."),
        Setting("volume", "Reminder volume", type=INT, default=50, minimum=0, maximum=100, unit="%",
                help="Sonos only. 0 leaves each speaker's volume alone. "
                     "Speakers return to their own volume afterwards."),
        Setting("chime", "Reminder chime", type=CHOICE, default="soft-bell", choices=CHIME_CHOICES,
                help="Played before the announcement."),
        Setting("skikrumb_key", "skiKrumb anon key", default="",
                help="Empty: read it from skiKrumb's public embed page."),
    ),
    api_version=2,
    # A screen alert and a speaker announcement when a reminder comes due.
    permissions=("alerts", "speakers"),
    # The live map reads skiKrumb over the internet.
    network=NETWORK_OUTBOUND,
    # The ticker plays reminders on time, and the tile shows "Next 4:10 · 3 min",
    # which would go stale at an on_demand app's 5-minute minimum wake interval.
    lifecycle=LIFECYCLE_ALWAYS,
    memory_mb=96,
)