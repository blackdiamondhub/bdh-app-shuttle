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

The reviewed 2026/27 East loop timetable is in `shuttle/data/timetable.json`,
including its official image URL, SHA-256, service dates, paired Village Day
Lodge departures / E7 Lookout Ridge pickups, and driver breaks. For example,
the Village departure is 1:40 PM and the Lookout Ridge pickup is about 1:45 PM.
The resort does not publish a return arrival at the Village: the bus continues
around the remaining East loop stops, so the app does not invent one.
All displayed times are Sun Peaks wall-clock time, fixed UTC−7.

### Schedule maintenance

`Check official shuttle timetable` runs daily at 14:00 UTC (7 AM Sun Peaks)
and can be run manually in GitHub Actions. It discovers the current East image
on the official page, normalizes responsive image URLs to the original, and
compares both its URL and SHA-256 with the reviewed baseline. A change opens
one issue assigned to `sslivins`; another image change comments on that issue.
An unchanged fingerprint does not create daily duplicate issues or comments.
Missing/ambiguous images, download errors, and GitHub errors fail the workflow.

The workflow does **not** automatically accept OCR or update the timetable.
Review the linked image, update its approved URL/hash and `verified_on` together
with the trips, breaks, and service dates in the JSON, and close the issue with
the reviewed PR. Check any saved season-date overrides on the hub too.
`previous_lookout` pairs superseded pickups with their revised trips, so existing
reminders retain their IDs, lead minutes and speaker choices when retimed.
Revised reminders are persisted before use and are not shifted again on restart.

The checking job has only contents-read/issues-write permissions. A separate,
checkout-free job re-enables this workflow through GitHub's API to prevent
public-repository schedules being disabled after 60 quiet days; it has only
actions-write permission and creates no dummy commits.

To inspect a change without writing GitHub issues:
`python -m shuttle.schedule_monitor --dry-run`.

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
play.) Bells only appear on "Lookout Ridge pickup".

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