# bdh-app-shuttle

BlackDiamondHub plugin app: **Shuttle** — the free Sun Peaks in-resort shuttle
(East loop) from the Lookout Ridge stop (E7, near the hot tub): next bus,
the day's timetable both ways, a live map of the bus from skiKrumb, and
"remind me" alerts on the hub's screens and speakers.

## Configuration

| Setting | Env var | Default |
|---|---|---|
| First day of winter service | `BDH_SHUTTLE_SEASON_START` | `2026-11-21` |
| Last day of winter service | `BDH_SHUTTLE_SEASON_END` | `2027-04-04` |
| Speakers reminders may use | `BDH_SHUTTLE_SPEAKERS` | none (no reminder bells) |
| Reminder volume (0 = leave as is) | `BDH_SHUTTLE_VOLUME` | `50` |
| Reminder chime | `BDH_SHUTTLE_CHIME` | `soft-bell` |
| skiKrumb anon key | `BDH_SHUTTLE_SKIKRUMB_KEY` | empty (read from their public embed page) |

All are on the hub's Settings page.

While the Shuttle page is open, it keeps the display awake, like Cameras.
Leaving the page restores the display's normal idle behavior.

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

The hub keeps and fires reminders itself (since 2.1.0; before that they lived in
the `bdl_shuttle` AppDaemon app). A reminder is "N minutes before this bus
reaches Lookout Ridge": a 5/10/15/20-minute lead for a real departure that
hasn't passed, at most five at a time, one per bus. They are kept in the app's
state, so they survive a restart; one that came due while the hub was down
still plays (with the minutes recomputed) unless the bus has already gone.

When one comes due, a background thread:

- pops an alert up on every hub screen (`bdh_sdk.alerts`, `popup/<minutes>/`), and
- announces it on its speakers (`bdh_sdk.speakers`) with the configured volume and chime.

The admin chooses which speakers reminders may use (`speakers`); a guest picks
"All speakers" or some of them when setting one. Leave `speakers` empty to hide
reminders. Night hours don't apply: a guest asked for this one. (The hub still
keeps the screens' own alert sound quiet during its night window; the speakers
play.) Bells only appear on "From the lodge".

When upgrading from AppDaemon reminders, set `speakers` to the speakers the
old announcer used. The old `reminders_url` setting does not enable native
reminders; without a saved speaker selection, notification controls stay hidden.

## Container (hub API 2)

Since 2.0.0 the app runs in its own container:

- **permissions**: `alerts` and `speakers`, to fire reminders.
- **network**: `outbound`; the live map reads skiKrumb (`app.skikrumb.ca`,
  `supabase.skikrumb.ca`) over the internet.
- **lifecycle**: `always`: the reminder thread must keep running.
- **memory_mb**: 96.

In the container the `BDH_SHUTTLE_*` environment variables on the hub are not visible; set
`speakers` (and optionally `volume`, `chime`, `season_start`, `season_end`, `skikrumb_key`) on
the Settings page. Reminders are kept in the app's state through the broker.
CI's `image` job (from bdh-sdk's `app-ci.yml`) locks the dependencies against the hub's app
runtime, builds the app's image and smoke-tests it the way the supervisor runs it.