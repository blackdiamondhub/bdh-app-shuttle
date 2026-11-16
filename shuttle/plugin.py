from bdh_sdk import PluginSpec, Setting, Tile
from bdh_sdk.plugins import LIFECYCLE_ALWAYS, NETWORK_LAN, SECRET

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
        Setting("reminders_url", "Reminder service URL", default="",
                help="The bdl_shuttle AppDaemon endpoint, e.g. "
                     "http://192.168.10.217:5050/api/appdaemon/bdl_shuttle. Empty hides reminders."),
        Setting("reminders_api_key", "Reminder service key", type=SECRET,
                help="The bdl_shuttle api_key from AppDaemon's secrets.yaml."),
        Setting("skikrumb_key", "skiKrumb anon key", default="",
                help="Empty: read it from skiKrumb's public embed page."),
    ),
    api_version=2,
    # Nothing from the hub beyond the app's own settings and secrets.
    permissions=(),
    # Reminders go to AppDaemon on the lodge LAN (outbound refuses private addresses);
    # the live map reads skiKrumb over the internet.
    network=NETWORK_LAN,
    # The tile shows "Next 4:10 · 3 min" and pending reminders, which go stale at the
    # 5-minute minimum wake interval of an on_demand app.
    lifecycle=LIFECYCLE_ALWAYS,
    memory_mb=96,
)