# bdh-app-shuttle

BlackDiamondHub plugin app: **Shuttle** — the free Sun Peaks in-resort shuttle
(East loop) from the Lookout Ridge stop (E7, out front of the lodge): next bus,
the day's timetable both ways, a live map of the bus from skiKrumb, and
"remind me" announcements on the dining room speaker.

## Configuration

| Setting | Env var | Default |
|---|---|---|
| First day of winter service | `BDH_SHUTTLE_SEASON_START` | `2026-11-21` |
| Last day of winter service | `BDH_SHUTTLE_SEASON_END` | `2027-04-04` |
| Reminder service URL | `BDH_SHUTTLE_REMINDERS_URL` | empty (no reminder bells) |
| Reminder service key (secret) | `BDH_SHUTTLE_REMINDERS_API_KEY` | empty |
| skiKrumb anon key | `BDH_SHUTTLE_SKIKRUMB_KEY` | empty (read from their public embed page) |

All are on the hub's Settings page (the key is a write-only secret).

The timetable is the resort's East loop schedule in `shuttle/schedule.py`;
update it when the resort posts a new season's times. All times are Sun Peaks
wall-clock time, fixed UTC−7.

## Live location

The resort's live shuttle page embeds skiKrumb's map, which reads
`supabase.skikrumb.ca/functions/v1/embed` with a public anon key. The app reads
the same feed (cached 8 s, last good answer kept for an hour), shows fixes newer
than 5 minutes, and names the nearest stop. Off-season the feed returns the
tracker with no position and the map just shows the route.

## Reminders

Reminders are kept by the `bdl_shuttle` AppDaemon app
([appdaemon-bdl-shuttle](https://github.com/sslivins/appdaemon-bdl-shuttle)),
so they still play when nobody has the page open. The hub only validates the
request (a real departure from Lookout Ridge, a 5/10/15/20-minute lead that
hasn't passed) and forwards it with the API key; AppDaemon fires the existing
`announcer` app at the right time. Bells only appear on "From the lodge" — the
speaker is at the lodge.

For the hub, set:

```
BDH_SHUTTLE_REMINDERS_URL=http://192.168.10.217:5050/api/appdaemon/bdl_shuttle
BDH_SHUTTLE_REMINDERS_API_KEY=<bdl_shuttle api_key from AppDaemon secrets.yaml>
```

The key is a secret: set it on the hub's Settings page (it is stored encrypted and
read with `bdh_sdk.secrets`). A plain `reminders_api_key` setting still works as a fallback.

## Container (hub API 2)

Since 2.0.0 the app runs in its own container:

- **permissions**: none — it only reads its own settings and secret through the broker.
- **network**: `lan` (admin must approve). Reminders go to AppDaemon on the lodge LAN
  (`192.168.10.217:5050`), which `outbound` refuses as a private address; the live map
  reads skiKrumb (`app.skikrumb.ca`, `supabase.skikrumb.ca`) over the internet.
- **lifecycle**: `always`. There is no background thread, but the tile status ("Next 4:30 PM ·
  8 min", "Reminder set") is minute-accurate and would go stale under `on_demand`'s
  5-minute minimum wake interval.
- **memory_mb**: 96.

In the container the `BDH_SHUTTLE_*` environment variables on the hub are not visible, so the
admin must set these on the Settings page:

| Key | Kind | Was |
|---|---|---|
| `reminders_url` | setting | `BDH_SHUTTLE_REMINDERS_URL` |
| `reminders_api_key` | secret | `BDH_SHUTTLE_REMINDERS_API_KEY` |

Leave `reminders_url` empty to hide reminders. `season_start`, `season_end` and
`skikrumb_key` are optional (defaults above). The app keeps nothing in a database, so moving
between in-process and container needs no migration.

CI's `image` job (from bdh-sdk's `app-ci.yml`) locks the dependencies against the hub's app
runtime, builds the app's image and smoke-tests it the way the supervisor runs it.