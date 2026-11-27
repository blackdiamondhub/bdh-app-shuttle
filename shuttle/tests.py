import datetime as dt
import json
import os
from unittest.mock import MagicMock, patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from bdh_sdk.storage import app_state

from shuttle import live, reminders, schedule, stops, worker
from shuttle.homepage import status as homepage_status

UTC = dt.timezone.utc
LOCAL = schedule.LOCAL


def local(y, mo, d, h, mi=0):
    """A UTC instant for the given wall-clock time in Sun Peaks (UTC-7)."""
    return dt.datetime(y, mo, d, h, mi, tzinfo=LOCAL).astimezone(UTC)


def at(when):
    return patch("shuttle.schedule.timezone.now", return_value=when)


def times(b):
    return [r.get("time") or r["brk"] for r in b["rows"]]


DINING, DECK = "media_player.dining_room", "media_player.deck"
SPK = {"BDH_SHUTTLE_SPEAKERS": f"{DINING},{DECK}"}
LABELS = [(DINING, "Dining Room (Sonos)"), (DECK, "Deck (Sonos) - offline")]


def speakers_on(**extra):
    return patch.dict(os.environ, {**SPK, **extra})


class Fresh:
    """Start every test with no reminders, in memory or in app state."""

    def setUp(self):
        super().setUp()
        cache.clear()
        app_state("shuttle").delete("reminders")
        reminders.reset_cache()
        self.addCleanup(reminders.reset_cache)


class ScheduleTests(TestCase):
    def test_board_starts_at_last_departed_bus(self):
        with at(local(2026, 12, 1, 16, 37)):
            b = schedule.board(schedule.LODGE)
        self.assertFalse(b["preview"] or b["tomorrow"])
        rows = b["rows"]
        self.assertEqual(rows[0]["time"], "3:45 PM")
        self.assertTrue(rows[0]["past"])
        self.assertFalse(rows[0]["remind"])
        self.assertEqual(rows[1]["rel"], "in 8 min")
        self.assertTrue(rows[1]["remind"])
        self.assertEqual(rows[2]["rel"], "in 1 h 28 min")
        self.assertTrue(rows[-1]["last"])
        self.assertIn("No service 7:30 – 8:10", times(b))

    def test_no_reminder_inside_the_minimum_lead(self):
        with at(local(2026, 12, 1, 16, 42)):
            row = schedule.board(schedule.LODGE)["rows"][1]
        self.assertEqual(row["time"], "4:45 PM")
        self.assertFalse(row["remind"])

    def test_breaks_sit_between_the_right_buses(self):
        with at(local(2026, 12, 1, 6)):
            got = times(schedule.board(schedule.LODGE))
        self.assertEqual(got[:4], ["8:45 AM", "9:45 AM", "No service 10:20 – 11:00", "11:45 AM"])
        self.assertEqual(got.index("No service 2:20 – 3:00"), got.index("1:45 PM") + 1)

    def test_village_tab_has_arrival_and_no_reminders(self):
        with at(local(2026, 12, 1, 6)):
            rows = [r for r in schedule.board(schedule.VILLAGE)["rows"] if "time" in r]
        self.assertEqual((rows[0]["time"], rows[0]["arrive"]), ("8:40 AM", "8:45 AM"))
        self.assertFalse(any(r["remind"] for r in rows))

    def test_afternoon_pickup_and_departure_are_not_return_arrivals(self):
        with at(local(2026, 12, 1, 13, 35)):
            nxt = schedule.next_departure()
            village = schedule.board(schedule.VILLAGE)["rows"]
        self.assertEqual(nxt["time"], "1:45 PM")
        self.assertNotIn("village", nxt)
        row = next(r for r in village if r.get("time") == "1:40 PM")
        self.assertEqual(row["arrive"], "1:45 PM")
        with patch.dict(schedule.TIMES, {schedule.VILLAGE: ("08:40",), schedule.LODGE: ("08:57",)}):
            row = schedule.board(schedule.VILLAGE, now=local(2026, 12, 1, 6))["rows"][0]
        self.assertEqual(row["arrive"], "8:57 AM")  # paired stop time, not a fixed ride estimate

    def test_after_last_bus_shows_tomorrow(self):
        with at(local(2026, 12, 1, 22)):
            b = schedule.board(schedule.LODGE)
            nxt = schedule.next_departure()
        self.assertTrue(b["tomorrow"])
        self.assertEqual(b["day"], dt.date(2026, 12, 2))
        self.assertEqual(b["rows"][0]["rel"], "")
        self.assertTrue(b["rows"][0]["remind"])
        self.assertEqual((nxt["time"], nxt["today"]), ("8:45 AM", False))

    def test_off_season_is_a_preview(self):
        with at(local(2026, 11, 3, 12)):
            b = schedule.board(schedule.LODGE)
            self.assertFalse(schedule.in_season())
            self.assertEqual(schedule.season_note(), "Starts Nov 21")
            self.assertEqual(schedule.next_departure()["at"].date(), dt.date(2026, 11, 21))
        self.assertTrue(b["preview"])
        self.assertFalse(any(r.get("past") or r.get("remind") or r.get("rel") for r in b["rows"]))

    def test_eve_of_season_shows_tomorrow_not_preview(self):
        with at(local(2026, 11, 20, 12)):
            b = schedule.board(schedule.LODGE)
        self.assertFalse(b["preview"])
        self.assertTrue(b["tomorrow"])

    def test_after_season(self):
        with at(local(2027, 4, 5, 12)):
            self.assertIsNone(schedule.next_departure())
            self.assertTrue(schedule.board(schedule.LODGE)["preview"])
            self.assertEqual(schedule.season_note(), "Done for the season")

    def test_is_departure(self):
        self.assertEqual(schedule.is_departure("2026-12-01T16:45:00-07:00"),
                         dt.datetime(2026, 12, 1, 16, 45, tzinfo=LOCAL))
        self.assertIsNotNone(schedule.is_departure("2026-12-01T23:45:00+00:00"))
        for bad in ("2026-12-01T16:30:00-07:00", "2026-12-01T16:46:00-07:00",
                    "2026-12-01T16:45:00", "2026-11-01T16:45:00-07:00",
                    "nope", None):
            self.assertIsNone(schedule.is_departure(bad), bad)

    @override_settings(BDH_SHUTTLE_SEASON_START="2027-11-20", BDH_SHUTTLE_SEASON_END="bad")
    def test_season_settings(self):
        self.assertEqual(schedule.config.season_start(), dt.date(2027, 11, 20))
        self.assertEqual(schedule.config.season_end(), dt.date(2027, 4, 4))


class LiveTests(TestCase):
    def setUp(self):
        cache.clear()

    def marker(self, lat=50.88300, lon=-119.87130, secs_ago=10):
        t = (timezone.now() - dt.timedelta(seconds=secs_ago)).isoformat().replace("+00:00", "Z")
        return {"serial_number": "X1", "registration_data": [{}],
                "device_data": [{"latitude": lat, "longitude": lon, "recorded_at": t, "heading": 90}]}

    def test_parse_skips_trackers_without_positions(self):
        got = live.parse([self.marker(), {"device_data": []}, {"device_data": [{"latitude": "x"}]}, None])
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["lat"], got[0]["heading"]), (50.883, 90))

    def test_status_names_nearby_stop_and_drops_old_fixes(self):
        with patch.object(live, "fetch", return_value=[self.marker(), self.marker(secs_ago=900)]):
            s = live.status()
        self.assertTrue(s["ok"])
        self.assertFalse(s["stale"])
        self.assertEqual(len(s["buses"]), 1)
        self.assertEqual(s["buses"][0]["near"], "E3 Orient Chair")
        self.assertFalse(s["buses"][0]["at_our_stop"])

    def test_at_our_stop(self):
        with patch.object(live, "fetch", return_value=[self.marker(50.884889981815334, -119.8647617402362)]):
            self.assertTrue(live.status()["buses"][0]["at_our_stop"])

    def test_corrected_official_stop_positions_and_e7_is_not_our_stop(self):
        points = (("E1", 50.884250910130966, -119.8781202809673),
                  ("E2", 50.8838549240345, -119.87570532056989),
                  ("E3", 50.882981745393366, -119.87266369552958),
                  ("E4", 50.882554, -119.866334), ("E5", 50.882704, -119.862778),
                  ("E6", 50.884889981815334, -119.8647617402362),
                  ("E7", 50.88345, -119.86200), ("E8", 50.883206, -119.867700),
                  ("E9", 50.88367244784997, -119.87362588960312),
                  ("E10", 50.88465648796544, -119.8783275104009))
        names = {"E1": "Village Walk", "E2": "McGillvray Creek", "E3": "Orient Chair",
                 "E4": "P5 Entrance", "E5": "Switchback Creek", "E6": "Altitude",
                 "E7": "Lookout Ridge", "E8": "Stones Throw", "E9": "Trappers Landing", "E10": "Ice Rink"}
        for code, lat, lon in points:
            with self.subTest(code=code):
                stop, distance = stops.nearest(lat, lon)
                self.assertEqual(stop["id"], code)
                self.assertLess(distance, 1)
                cache.clear()
                with patch.object(live, "fetch", return_value=[self.marker(lat, lon)]):
                    bus = live.status()["buses"][0]
                self.assertEqual(bus["near"], f"{code} {names[code]}")
                self.assertEqual(bus["at_our_stop"], code == "E6")

    def test_falls_back_to_last_good_when_feed_fails(self):
        with patch.object(live, "fetch", return_value=[self.marker()]):
            live.status()
        cache.delete(live.LIVE_CACHE)
        with patch.object(live, "fetch", side_effect=live.LiveError("down")):
            s = live.status()
        self.assertTrue(s["stale"])
        self.assertEqual(len(s["buses"]), 1)
        cache.clear()
        with patch.object(live, "fetch", side_effect=live.LiveError("down")):
            self.assertEqual(live.status(), {"ok": False, "stale": True, "buses": []})

    def test_fetch_rediscovers_key_once_on_401(self):
        denied, ok = MagicMock(status_code=401), MagicMock(status_code=200)
        ok.json.return_value = []
        with patch.object(live, "_discover_key", side_effect=["old", "new"]) as disc, \
                patch.object(live.requests, "post", side_effect=[denied, ok]) as post:
            self.assertEqual(live.fetch(), [])
        self.assertEqual(disc.call_count, 2)
        self.assertEqual(post.call_args.kwargs["headers"]["apikey"], "new")

    def test_route_and_stops_line_up(self):
        route = stops.route()
        self.assertGreater(len(route), 50)
        for s in stops.STOPS:
            near = min(stops.distance_m(s["lat"], s["lon"], a, b) for a, b in route)
            self.assertLess(near, 50, s["id"])  # curbside pins need not lie on the road centre line

    def test_route_skips_settlers_crossing_and_northern_village_place(self):
        route = stops.route()
        self.assertEqual(route[0], route[-1])
        self.assertNotIn([50.88471, -119.877593], route)
        self.assertNotIn([50.885722, -119.878521], route)
        self.assertNotIn([50.883319, -119.867698], route)
        at_e8 = [i for i, point in enumerate(route) if point == [50.883206, -119.8677]]
        self.assertEqual(len(at_e8), 2)
        for i in at_e8:
            self.assertEqual({tuple(route[i - 1]), tuple(route[i + 1])},
                             {(50.883196, -119.868128), (50.883096, -119.867149)})

    def test_route_connects_village_walk_and_skips_mcgillvray_townhomes(self):
        route = stops.route()
        west = route.index([50.884581, -119.878774])
        east = route.index([50.884372, -119.876682], west + 1)
        self.assertIn([50.884261, -119.878125], route[west:east + 1])
        self.assertIn([50.884202, -119.87728], route[west:east + 1])
        self.assertNotIn([50.884637, -119.877855], route[west:east + 1])
        self.assertIn([50.884637, -119.877855], route[east + 1:])  # return via Village Way past E10
        for point in ([50.88354, -119.875229], [50.883446, -119.874702],
                      [50.883306, -119.873965], [50.883247, -119.872998]):
            self.assertNotIn(point, route)
        self.assertEqual(route.count([50.883655, -119.873832]), 2)


class PageTests(Fresh, TestCase):
    def test_shuttle_opts_out_of_screensaver_in_and_out_of_season(self):
        for now in (local(2026, 12, 1, 16, 37), local(2026, 11, 3, 12)):
            with at(now):
                r = self.client.get(reverse("shuttle"))
            self.assertContains(r, '<meta name="bdh-screensaver" content="off">')

    def test_in_season_page(self):
        with at(local(2026, 12, 1, 16, 37)):
            r = self.client.get(reverse("shuttle"))
        self.assertContains(r, "Next shuttle")
        self.assertContains(r, 'data-t="2026-12-01T16:45:00-07:00"')
        self.assertContains(r, "To the Village via the remaining East loop stops")
        self.assertContains(r, "Altitude pickup")
        self.assertNotContains(r, "arrives Village Day Lodge")
        self.assertContains(r, "Shuttle (live)")
        self.assertContains(r, "Altitude stop (E6, near hot tub)")
        self.assertNotContains(r, "Free in-resort shuttle")
        self.assertNotContains(r, "Remind me")  # no speakers allowed yet
        geo = json.loads(r.content.decode().split('id="sh-geo" type="application/json">')[1].split("</script>")[0])
        self.assertEqual(geo["our"], "E6")
        self.assertEqual([s["id"] for s in geo["stops"]], ["VDL"] + [f"E{i}" for i in range(1, 11)])

    def test_reminder_bells_only_from_the_lodge(self):
        with at(local(2026, 12, 1, 16, 37)), speakers_on():
            r = self.client.get(reverse("shuttle"))
        self.assertContains(r, "Remind me")
        self.assertContains(r, 'class="rb" data-dep="2026-12-01T16:45:00-07:00"')
        self.assertNotContains(r, 'data-dep="2026-12-01T16:40:00-07:00"')
        self.assertContains(r, 'id="sh-ov"')
        self.assertContains(r, 'id="sh-spk"')

    def test_off_season_page(self):
        with at(local(2026, 11, 3, 12)):
            r = self.client.get(reverse("shuttle"))
        self.assertContains(r, "Starts Nov 21")
        self.assertContains(r, "Shuttle not running")
        self.assertNotContains(r, "Shuttle (live)")
        self.assertNotContains(r, "data-t=")
        self.assertContains(r, 'id="sh-find" disabled')
        self.assertContains(r, "Show route")

    def test_live_api(self):
        with patch.object(live, "status", return_value={"ok": True, "stale": False, "buses": []}):
            r = self.client.get(reverse("shuttle_live"))
        self.assertEqual(r.json(), {"ok": True, "stale": False, "buses": []})

    def test_popup(self):
        r = self.client.get(reverse("shuttle_popup", args=[10]))
        self.assertContains(r, "Shuttle in 10 minutes")
        self.assertContains(r, "To the Village from Altitude")
        self.assertContains(self.client.get(reverse("shuttle_popup", args=[1])), "Shuttle in 1 minute<")


class ReminderStoreTests(Fresh, TestCase):
    DEP = local(2026, 12, 1, 18, 5)
    NOW = local(2026, 12, 1, 16, 0)

    def setUp(self):
        super().setUp()
        p = speakers_on()
        p.start()
        self.addCleanup(p.stop)

    def test_add_update_cancel_and_survive_restart(self):
        r = reminders.add(self.DEP, 10, now=self.NOW)
        self.assertEqual((r["departure"], r["fire_at"], r["speakers"]),
                         ("2026-12-01T18:05:00-07:00", "2026-12-01T17:55:00-07:00", None))
        again = reminders.add(self.DEP, 5, [DECK], now=self.NOW)  # same bus: updated, not added
        self.assertEqual(again["id"], r["id"])
        reminders.reset_cache()  # as if the hub restarted
        self.assertEqual(reminders.pending(), [again])
        self.assertEqual(again["speakers"], [DECK])
        self.assertTrue(reminders.cancel(r["id"]))
        self.assertFalse(reminders.cancel(r["id"]))
        reminders.reset_cache()
        self.assertEqual(reminders.pending(), [])

    def test_rejects_too_late_too_many_and_bad_speakers(self):
        with self.assertRaisesRegex(ValueError, "Too late for a 10-minute"):
            reminders.add(self.DEP, 10, now=local(2026, 12, 1, 17, 55))
        for bad in ([], ["media_player.garage"], "kitchen"):
            with self.assertRaises(ValueError, msg=bad):
                reminders.add(self.DEP, 10, bad, now=self.NOW)
        for n in range(reminders.MAX_ACTIVE):
            reminders.add(self.DEP + dt.timedelta(hours=n), 10, now=self.NOW)
        with self.assertRaisesRegex(ValueError, "up to 5"):
            reminders.add(self.DEP + dt.timedelta(hours=9), 10, now=self.NOW)

    def test_tick_plays_due_late_and_drops_missed(self):
        a = reminders.add(self.DEP, 10, now=self.NOW)
        b = reminders.add(self.DEP + dt.timedelta(hours=1), 10, now=self.NOW)
        c = reminders.add(self.DEP + dt.timedelta(hours=2), 10, now=self.NOW)
        self.assertEqual(reminders.next_wake(self.NOW), 115 * 60)
        self.assertEqual(reminders.tick(local(2026, 12, 1, 17, 54)), [])
        # Both came due while the hub was down: a's bus has gone (dropped), b's is 3 minutes out.
        with self.assertLogs("shuttle.reminders", "WARNING"):
            due = reminders.tick(local(2026, 12, 1, 19, 2))
        self.assertEqual([(r["id"], m) for r, m in due], [(b["id"], 3)])
        self.assertNotIn(a["id"], [r["id"] for r in reminders.pending()])
        self.assertEqual([r["id"] for r in reminders.pending()], [c["id"]])
        # Missed by more than the bus: dropped, not played.
        with self.assertLogs("shuttle.reminders", "WARNING"):
            self.assertEqual(reminders.tick(local(2026, 12, 1, 20, 5)), [])
        reminders.reset_cache()
        self.assertEqual(reminders.pending(), [])

    def test_targets_follow_the_allowed_list(self):
        self.assertEqual(reminders.targets({"speakers": None}), [DINING, DECK])
        self.assertEqual(reminders.targets({"speakers": [DECK]}), [DECK])
        with patch.dict(os.environ, {"BDH_SHUTTLE_SPEAKERS": DINING}):
            self.assertEqual(reminders.targets({"speakers": [DECK]}), [])  # deck no longer allowed

    def test_old_reminders_are_retimed_once_preserving_choices(self):
        old = {"id": "saved", "departure": local(2026, 12, 1, 17, 50).timestamp(),
               "lead_min": 15, "speakers": [DECK], "created": self.NOW.timestamp()}
        current = {**old, "id": "current", "departure": local(2026, 12, 1, 18, 45).timestamp()}
        app_state("shuttle").set("reminders", [old, current])
        with self.assertLogs("shuttle.reminders", "INFO"):
            pending = reminders.pending()
        self.assertEqual([r["id"] for r in pending], ["saved", "current"])
        self.assertEqual([r["departure"] for r in pending],
                         ["2026-12-01T18:05:00-07:00", "2026-12-01T18:45:00-07:00"])
        self.assertEqual([r["fire_at"] for r in pending],
                         ["2026-12-01T17:50:00-07:00", "2026-12-01T18:30:00-07:00"])
        self.assertTrue(all(r["speakers"] == [DECK] for r in pending))
        reminders.reset_cache()
        self.assertEqual(reminders.pending(), pending)
        self.assertEqual(reminders.tick(local(2026, 12, 1, 17, 49)), [])
        self.assertEqual(len(reminders.tick(local(2026, 12, 1, 17, 50))), 1)

    def test_migration_does_not_shift_unknown_times_and_retries_failed_save(self):
        old = {"id": "saved", "departure": local(2026, 12, 1, 17, 50).timestamp(),
               "lead_min": 10, "speakers": None}
        odd = {**old, "id": "odd", "departure": old["departure"] + 30}
        app_state("shuttle").set("reminders", [old, odd])
        store = MagicMock()
        store.get.return_value = [old, odd]
        store.set.side_effect = RuntimeError("state unavailable")
        with patch.object(reminders, "_state", return_value=store), self.assertRaises(RuntimeError):
            reminders.pending()
        self.assertIsNone(reminders._recs)
        self.assertEqual(old["departure"], local(2026, 12, 1, 17, 50).timestamp())
        pending = reminders.pending()
        self.assertEqual(next(r for r in pending if r["id"] == "saved")["departure"],
                         "2026-12-01T18:05:00-07:00")
        self.assertEqual(next(r for r in pending if r["id"] == "odd")["departure"],
                         "2026-12-01T17:50:30-07:00")


class WorkerTests(Fresh, TestCase):
    @patch("bdh_sdk.alerts.push")
    def test_fire_pops_up_and_announces_even_at_night(self, push):
        with speakers_on(BDH_SHUTTLE_VOLUME="40", BDH_SHUTTLE_CHIME="kalimba"), \
                patch("shuttle.worker.threading.Thread") as thread:
            r = reminders.add(local(2026, 12, 2, 2, 10), 10, [DECK], now=local(2026, 12, 2, 1, 0))
            [(rec, minutes)] = reminders.tick(local(2026, 12, 2, 2, 0))  # 2 am
            worker.handle([(rec, minutes)])
        alert = push.call_args.args[0]
        self.assertEqual((alert.key, alert.title, alert.embed_url, alert.sound),
                         (f"reminder:{r['id']}", "Shuttle in 10 minutes", "/shuttle/popup/10/", "kalimba"))
        self.assertIn("in about 10 minutes", alert.speak)
        self.assertIn("reaches Altitude", alert.speak)
        self.assertEqual(alert.body, "To the Village from Altitude")
        kw = thread.call_args.kwargs
        self.assertEqual((kw["target"], kw["args"][1]), (worker.announce, [DECK]))
        with speakers_on(BDH_SHUTTLE_VOLUME="40", BDH_SHUTTLE_CHIME="kalimba"), \
                patch("bdh_sdk.speakers.announce", return_value=[]) as ann:
            worker.announce(*kw["args"])
        self.assertEqual(ann.call_args.args[1], [DECK])
        self.assertEqual((ann.call_args.kwargs["volume"], ann.call_args.kwargs["chime"]), (40, "kalimba"))

    @patch("bdh_sdk.alerts.push")
    def test_no_speakers_left_is_screens_only(self, push):
        with patch("shuttle.worker.threading.Thread") as thread, \
                patch.dict(os.environ, {"BDH_SHUTTLE_SPEAKERS": DINING}), \
                self.assertLogs("shuttle.worker", "WARNING"):
            worker.fire({"id": "x", "speakers": [DECK]}, 5)
        push.assert_called_once()
        thread.assert_not_called()

    def test_announce_failures_are_logged_not_raised(self):
        with patch("bdh_sdk.speakers.announce", side_effect=RuntimeError("HA down")), \
                self.assertLogs("shuttle.worker", "ERROR"):
            worker.announce("hi", [DECK])

    def test_should_start(self):
        self.assertFalse(worker.should_start(["manage.py", "test"]))
        self.assertTrue(worker.should_start(["/x/bdh_sdk/runtime/__main__.py"]))
        with patch.dict(os.environ, {"BDH_SHUTTLE_WORKER": "0"}):
            self.assertFalse(worker.should_start(["/x/bdh_sdk/runtime/__main__.py"]))


class ReminderApiTests(Fresh, TestCase):
    def setUp(self):
        super().setUp()
        p = speakers_on()
        p.start()
        self.addCleanup(p.stop)
        self.url = reverse("shuttle_reminders")

    def post(self, body):
        return self.client.post(self.url, json.dumps(body), content_type="application/json")

    def test_add_list_cancel(self):
        with at(local(2026, 12, 1, 16, 0)), patch.object(worker, "poke") as poke:
            r = self.post({"action": "add", "departure": "2026-12-01T18:05:00-07:00", "lead_min": 10,
                           "speakers": [DECK]})
            self.assertEqual(r.status_code, 200, r.content)
            [rec] = r.json()["reminders"]
            self.assertEqual((rec["lead_min"], rec["speakers"], rec["fire_at"]),
                             (10, [DECK], "2026-12-01T17:55:00-07:00"))
            self.assertEqual(self.client.get(self.url).json()["reminders"], [rec])
            r = self.post({"action": "cancel", "id": rec["id"]})
            self.assertEqual(r.json()["reminders"], [])
            self.assertEqual(self.post({"action": "cancel", "id": rec["id"]}).status_code, 404)
        self.assertEqual(poke.call_count, 2)

    def test_rejects_non_departures_bad_leads_speakers_and_too_late(self):
        dep = "2026-12-01T18:05:00-07:00"
        with at(local(2026, 12, 1, 17, 57)):
            for body in ({"action": "add", "departure": "2026-12-01T18:06:00-07:00", "lead_min": 10},
                         {"action": "add", "departure": "2026-12-01T17:50:00-07:00", "lead_min": 10},
                         {"action": "add", "departure": dep, "lead_min": 7},
                         {"action": "add", "departure": dep, "lead_min": "x"},
                         {"action": "add", "departure": dep, "lead_min": 5, "speakers": []},
                         {"action": "add", "departure": dep, "lead_min": 5, "speakers": ["media_player.x"]},
                         {"action": "nope"}, {"action": "cancel"}, ["add"]):
                self.assertEqual(self.post(body).status_code, 400, body)
            r = self.post({"action": "add", "departure": dep, "lead_min": 10})
            self.assertEqual(r.status_code, 400)
            self.assertIn("Too late", r.json()["error"])
            self.assertEqual(self.post({"action": "add", "departure": dep, "lead_min": 5,
                                        "speakers": "all"}).status_code, 200)
        self.assertEqual(len(reminders.pending()), 1)

    def test_speakers_lists_only_allowed_ones(self):
        with patch("bdh_sdk.speakers.list_speakers", return_value=LABELS + [("media_player.x", "X")]):
            r = self.client.get(reverse("shuttle_speakers"))
        self.assertEqual(r.json()["speakers"], [list(s) for s in LABELS])
        with patch("bdh_sdk.speakers.list_speakers", side_effect=RuntimeError):
            r = self.client.get(reverse("shuttle_speakers"))
        self.assertEqual(r.json()["speakers"], [[DINING, "Dining Room"], [DECK, "Deck"]])

    def test_disabled_without_speakers(self):
        with patch.dict(os.environ, {"BDH_SHUTTLE_SPEAKERS": ""}):
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertEqual(self.client.get(reverse("shuttle_speakers")).status_code, 404)

    def test_csrf_required(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        r = c.post(self.url, "{}", content_type="application/json")
        self.assertEqual(r.status_code, 403)


class ManifestTests(TestCase):
    def test_hub_api_2_manifest(self):
        from shuttle.plugin import spec
        m = spec.manifest()
        self.assertEqual(m["api_version"], 2)
        self.assertEqual(sorted(m["permissions"]), ["alerts", "speakers"])
        self.assertEqual((m["network"], m["lifecycle"], m["memory_mb"]), ("outbound", "always", 96))
        types = {s.key: s.type for s in spec.settings}
        self.assertEqual((types["speakers"], types["volume"], types["chime"]), ("multi", "int", "choice"))
        self.assertNotIn("reminders_url", types)


class BrokerTests(Fresh, TestCase):
    """In its container the app reads settings and keeps reminders through the broker."""

    def setUp(self):
        from bdh_sdk.testing import FakeBroker

        super().setUp()
        # events.subscribe: the hub test process itself subscribes once the RPC is installed.
        self.broker = FakeBroker(grants=("settings.get", "state.get", "state.set", "events.subscribe"),
                                 install=True, app_id="shuttle")
        self.broker.start()
        self.addCleanup(self.broker.stop)
        self.broker.settings[("shuttle", "speakers")] = [DINING]

    def test_saved_speaker_configuration_restores_guest_controls(self):
        with at(local(2026, 12, 1, 16, 37)):
            r = self.client.get(reverse("shuttle"))
        self.assertContains(r, "Remind me")
        self.assertContains(r, 'id="sh-spk"')
        with patch("bdh_sdk.speakers.list_speakers", return_value=LABELS):
            r = self.client.get(reverse("shuttle_speakers"))
        self.assertEqual(r.json()["speakers"], [list(LABELS[0])])

    def test_reminders_kept_in_broker_state(self):
        with at(local(2026, 12, 1, 16, 0)):
            r = self.client.post(reverse("shuttle_reminders"), json.dumps(
                {"action": "add", "departure": "2026-12-01T18:05:00-07:00", "lead_min": 10}),
                content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        reminders.reset_cache()
        self.assertEqual(len(reminders.pending()), 1)
        self.assertTrue(self.broker.ops("state.set"))


class HomepageTests(Fresh, TestCase):
    def test_next_bus(self):
        with at(local(2026, 12, 1, 16, 37)):
            s = homepage_status()
        self.assertEqual(s.text, "Next 4:45 PM · 8 min")

    def test_first_bus_tomorrow_and_off_season(self):
        with at(local(2026, 12, 1, 22)):
            self.assertEqual(homepage_status().text, "First bus 8:45 AM")
        with at(local(2026, 11, 20, 12)):
            self.assertEqual(homepage_status().text, "First bus 8:45 AM")
        with at(local(2026, 11, 3, 12)):
            self.assertEqual(homepage_status().text, "Not running · back Nov 21")
        with at(local(2027, 5, 1, 12)):
            self.assertIsNone(homepage_status())

    def test_pending_reminder(self):
        with speakers_on(), at(local(2026, 12, 1, 16, 37)):
            reminders.add(local(2026, 12, 1, 18, 5), 10)
            s = homepage_status()
        self.assertEqual(s.text, "Reminder set · 5:55 PM")
        self.assertEqual(s.level, "on")


@tag("selenium")
class BrowserTests(StaticLiveServerTestCase):
    NOW = local(2026, 12, 1, 16, 37)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from selenium import webdriver
        options = webdriver.ChromeOptions()
        for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1920,1080"):
            options.add_argument(arg)
        cls.driver = webdriver.Chrome(options=options)
        # Freeze the browser's clock at the same instant as the server's.
        cls.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": """
            (() => { const RD = Date, off = %d - RD.now();
              class FD extends RD { constructor(...a) { a.length ? super(...a) : super(RD.now() + off); }
                                    static now() { return RD.now() + off; } }
              window.Date = FD; })();""" % int(cls.NOW.timestamp() * 1000)})

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
        super().tearDownClass()

    def test_activity_heartbeat_survives_missing_map_library(self):
        script = """
            window.shActivity = [];
            window.shIntervals = [];
            window.addEventListener('DOMContentLoaded', () => {
              window.bdh = {send: type => window.shActivity.push(type)};
            });
            const interval = window.setInterval;
            window.setInterval = (fn, delay, ...args) => {
              if (delay === 30000) window.shIntervals.push(fn);
              return interval(fn, delay, ...args);
            };
        """
        d = self.driver
        hook = d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": script})
        d.execute_cdp_cmd("Network.enable", {})
        d.execute_cdp_cmd("Network.setBlockedURLs", {"urls": ["*unpkg.com*"]})
        try:
            with at(self.NOW), patch.object(live, "status", return_value={"ok": True, "buses": []}):
                d.get(self.live_server_url + reverse("shuttle"))
                self.assertTrue(d.execute_script("return typeof L === 'undefined'"))
                self.assertEqual(d.execute_script("return window.shActivity"), ["activity"])
                d.execute_script("window.shIntervals.forEach(fn => fn())")
                self.assertEqual(d.execute_script("return window.shActivity"), ["activity", "activity"])
        finally:
            d.execute_cdp_cmd("Network.setBlockedURLs", {"urls": []})
            d.execute_cdp_cmd("Page.removeScriptToEvaluateOnNewDocument", {"identifier": hook["identifier"]})

    def test_live_bus_and_reminder_flow(self):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        cache.clear()
        self.addCleanup(cache.clear)
        reminders.reset_cache()
        self.addCleanup(reminders.reset_cache)
        fix = {"latitude": 50.88300, "longitude": -119.87130, "heading": 0,
               "recorded_at": self.NOW.isoformat()}
        with at(self.NOW), speakers_on(), patch.object(live, "fetch", return_value=[{"device_data": [fix]}]), \
                patch.object(live, "time") as clock, \
                patch("bdh_sdk.speakers.list_speakers", return_value=LABELS):
            clock.time.return_value = self.NOW.timestamp()
            d = self.driver
            d.get(self.live_server_url + reverse("shuttle"))
            wait = WebDriverWait(d, 10)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#sh-map .sh-bus")))
            wait.until(EC.text_to_be_present_in_element((By.ID, "sh-ls"), "Near E3 Orient Chair"))
            self.assertEqual(len(d.find_elements(By.CSS_SELECTOR, "#sh-map .sh-our")), 1)
            # Nothing on the page scrolls at 1920x1080.
            self.assertFalse(d.execute_script(
                "return document.scrollingElement.scrollHeight > innerHeight"))

            # 4:45 bus, 8 minutes out: only the 5-minute heads-up is still possible.
            d.find_element(By.CSS_SELECTOR, "#sh-next .bell").click()
            wait.until(EC.visibility_of_element_located((By.ID, "sh-ov")))
            enabled = [b.get_attribute("data-lead") for b in d.find_elements(By.CSS_SELECTOR, "#sh-leads button")
                       if b.is_enabled()]
            self.assertEqual(enabled, ["5"])
            self.assertIn("in about 5 minutes", d.find_element(By.ID, "sh-say").text)
            self.assertIn("reaches Altitude", d.find_element(By.ID, "sh-say").text)
            self.assertIn("4:40 PM", d.find_element(By.CSS_SELECTOR, '#sh-leads [data-lead="5"]').text)
            # Speaker picker: all by default; pick just the deck.
            wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, '#sh-spk [data-spk="all"]')))
            self.assertIn("on", d.find_element(By.CSS_SELECTOR, '#sh-spk [data-spk="all"]').get_attribute("class"))
            self.assertEqual(d.find_element(By.CSS_SELECTOR, f'#sh-spk [data-spk="{DECK}"]').text, "Deck")
            d.find_element(By.CSS_SELECTOR, f'#sh-spk [data-spk="{DECK}"]').click()
            self.assertNotIn("on", d.find_element(By.CSS_SELECTOR, '#sh-spk [data-spk="all"]').get_attribute("class"))
            self.assertIn("plays once on Deck", d.find_element(By.ID, "sh-where").text)
            d.find_element(By.ID, "sh-ok").click()
            wait.until(lambda drv: not drv.find_element(By.ID, "sh-ov").is_displayed()
                       or drv.find_element(By.ID, "sh-err").text)
            self.assertEqual(d.find_element(By.ID, "sh-err").text, "")
            wait.until(EC.invisibility_of_element_located((By.ID, "sh-ov")))
            wait.until(EC.text_to_be_present_in_element((By.ID, "sh-rems"), "4:45 PM shuttle · 5 min heads-up"))
            self.assertIn("Deck at 4:40 PM", d.find_element(By.ID, "sh-rems").text)
            self.assertEqual(reminders.pending()[0]["speakers"], [DECK])
            self.assertIn("Reminder set", d.find_element(By.CSS_SELECTOR, "#sh-next .bell").text)
            self.assertIn("on", d.find_element(By.CSS_SELECTOR, '.rb[data-dep$="16:45:00-07:00"]')
                          .get_attribute("class"))

            # A later bus defaults to a 10-minute heads-up on all speakers.
            d.find_element(By.CSS_SELECTOR, '.rb[data-dep$="18:05:00-07:00"]').click()
            wait.until(EC.visibility_of_element_located((By.ID, "sh-ov")))
            self.assertIn("on", d.find_element(By.CSS_SELECTOR, '#sh-leads [data-lead="10"]').get_attribute("class"))
            self.assertIn("on", d.find_element(By.CSS_SELECTOR, '#sh-spk [data-spk="all"]').get_attribute("class"))
            d.find_element(By.ID, "sh-close").click()

            # Reopening the first keeps its speaker and offers to remove it; the village tab has no bells.
            d.find_element(By.CSS_SELECTOR, "#sh-next .bell").click()
            wait.until(EC.visibility_of_element_located((By.ID, "sh-remove")))
            self.assertIn("on", d.find_element(By.CSS_SELECTOR, f'#sh-spk [data-spk="{DECK}"]').get_attribute("class"))
            d.find_element(By.ID, "sh-remove").click()
            wait.until(EC.invisibility_of_element_located((By.ID, "sh-ov")))
            wait.until(lambda drv: not drv.find_elements(By.CSS_SELECTOR, "#sh-rems .sh-rem"))
            self.assertEqual(reminders.pending(), [])
            d.find_element(By.CSS_SELECTOR, '.sh-tabs [data-tab="village"]').click()
            self.assertFalse(d.find_elements(By.CSS_SELECTOR, '[data-list="village"] .rb'))
            self.assertTrue(d.find_element(By.CSS_SELECTOR, '[data-list="village"]').is_displayed())

    def live_probe(self):
        script = """
            (() => {
            window.shLive = {ok: true, stale: false, buses: [{
              lat: 50.883, lon: -119.8713, at: %s, age_s: 1,
              near: 'E3 Orient Chair', at_our_stop: false
            }]};
            window.shElapsed = 0;
            window.shHttpError = false;
            Date.now = () => %s + window.shElapsed;
            window.shTimers = {};
            const interval = window.setInterval;
            window.setInterval = (fn, delay, ...args) => {
              if (document.currentScript && document.currentScript.src.includes('/shuttle/shuttle.js') &&
                  [1000, 10000].includes(delay)) {
                (window.shTimers[delay] ||= []).push(fn);
                return 0;
              }
              return interval(fn, delay, ...args);
            };
            const fetch = window.fetch;
            window.fetch = (url, options) => {
              if (new URL(url, location.href).pathname === %s)
                return Promise.resolve({ok: !window.shHttpError, status: window.shHttpError ? 503 : 200,
                  json: async () => structuredClone(window.shLive)});
              return fetch(url, options);
            };
            })();
        """ % (self.NOW.timestamp() - 1, int(self.NOW.timestamp() * 1000),
               json.dumps(reverse("shuttle_live")))
        hook = self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": script})
        self.addCleanup(lambda: self.driver.execute_cdp_cmd("Page.removeScriptToEvaluateOnNewDocument",
                                                           {"identifier": hook["identifier"]}))

    def poll_live_probe(self):
        self.assertIs(self.driver.execute_async_script("""
            const done = arguments[0];
            window.shTimers[10000][0]().then(() => done(true), e => done(String(e)));
        """), True)

    def test_map_buttons_center_without_follow_and_restore_route(self):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        self.live_probe()
        d = self.driver
        with at(self.NOW):
            d.get(self.live_server_url + reverse("shuttle"))
            wait = WebDriverWait(d, 10)
            wait.until(lambda drv: drv.find_elements(By.CSS_SELECTOR, ".sh-bus") and
                       drv.find_element(By.ID, "sh-find").is_enabled())
            self.assertIn("E6 Altitude · our stop",
                          d.find_element(By.CSS_SELECTOR, ".sh-our .sh-slab").text)
            labels = [e.text for e in d.find_elements(By.CSS_SELECTOR, ".sh-slab")]
            for code in ("E2", "E8", "E9"):
                self.assertIn(code, labels)
            self.assertIn("E4 P5 Entrance", labels)
            self.assertIn("E5 Switchback Creek", labels)
            self.assertIn("E10 Ice Rink", labels)
            self.assertTrue(d.execute_script("""
                const labels = [...document.querySelectorAll('.sh-slab')];
                const first = labels.find(el => el.textContent === 'E1 Village Walk').getBoundingClientRect();
                const last = labels.find(el => el.textContent === 'E10 Ice Rink').getBoundingClientRect();
                return first.right <= last.left || last.right <= first.left ||
                       first.bottom <= last.top || last.bottom <= first.top;
            """), "The relocated E1 and E10 labels overlap")

            def offset(selector):
                return d.execute_script("""
                    const map = document.getElementById('sh-map').getBoundingClientRect();
                    const marker = document.querySelector(arguments[0]).getBoundingClientRect();
                    return [marker.x + marker.width / 2 - map.x - map.width / 2,
                            marker.y + marker.height / 2 - map.y - map.height / 2];
                """, selector)

            original = offset(".sh-our")
            d.find_element(By.ID, "sh-find").click()
            for axis in offset(".sh-bus"):
                self.assertAlmostEqual(axis, 0, delta=2)
            centered = offset(".sh-our")
            d.execute_script("window.shLive.buses[0].lat = 50.88; window.shLive.buses[0].lon = -119.86")
            self.poll_live_probe()
            for a, b in zip(centered, offset(".sh-our")):
                self.assertAlmostEqual(a, b, delta=2)  # new fixes must not pan the user's map
            self.assertGreater(max(map(abs, offset(".sh-bus"))), 100)
            d.find_element(By.ID, "sh-route").click()
            for a, b in zip(original, offset(".sh-our")):
                self.assertAlmostEqual(a, b, delta=2)

            d.execute_script("""
                window.shLive.buses.push({...window.shLive.buses[0], lat: 50.883, lon: -119.8713});
            """)
            self.poll_live_probe()
            self.assertEqual(d.find_element(By.ID, "sh-find").text, "Find shuttles")
            d.find_element(By.ID, "sh-find").click()
            self.assertTrue(d.execute_script("""
                const map = document.getElementById('sh-map').getBoundingClientRect();
                return [...document.querySelectorAll('.sh-bus')].every(el => {
                  const r = el.getBoundingClientRect();
                  return r.left >= map.left && r.right <= map.right &&
                         r.top >= map.top && r.bottom <= map.bottom;
                });
            """))
            d.execute_script("window.shLive.buses = []")
            self.poll_live_probe()
            self.assertFalse(d.find_element(By.ID, "sh-find").is_enabled())
            self.assertFalse(d.find_elements(By.CSS_SELECTOR, ".sh-bus"))
            self.assertTrue(d.find_element(By.ID, "sh-route").is_enabled())
            d.find_element(By.ID, "sh-route").click()
            for a, b in zip(original, offset(".sh-our")):
                self.assertAlmostEqual(a, b, delta=2)

            d.set_window_size(390, 844)
            try:
                self.assertTrue(d.execute_script("""
                    const map = document.querySelector('.sh-mapc').getBoundingClientRect();
                    return [...document.querySelectorAll('.sh-map-actions button')].every(el => {
                      const r = el.getBoundingClientRect();
                      return r.height >= 44 && r.left >= map.left && r.right <= map.right &&
                             r.top >= map.top && r.bottom <= map.bottom;
                    }) && document.scrollingElement.scrollWidth <= innerWidth;
                """))
            finally:
                d.set_window_size(1920, 1080)

    def test_gps_age_ticks_without_cached_polls_resetting_it_or_failed_feed_claiming_live(self):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        self.live_probe()
        d = self.driver
        with at(self.NOW):
            d.get(self.live_server_url + reverse("shuttle"))
            WebDriverWait(d, 10).until(lambda drv: drv.find_element(By.ID, "sh-ls").text.endswith(" · Live"))
            d.execute_script("window.shElapsed = 14000; window.shTimers[1000][0]()")
            self.assertTrue(d.find_element(By.ID, "sh-ls").text.endswith(" · Live"))  # 15 seconds old
            d.execute_script("window.shElapsed = 15000; window.shTimers[1000][0]()")
            self.assertIn("Last position 16 s ago", d.find_element(By.ID, "sh-ls").text)
            self.assertIn("off", d.find_element(By.ID, "sh-live").get_attribute("class"))
            self.assertTrue(d.find_elements(By.CSS_SELECTOR, ".sh-bus.stale"))
            self.poll_live_probe()  # same GPS timestamp, even though age_s is still 1 in cached JSON
            self.assertIn("Last position 16 s ago", d.find_element(By.ID, "sh-ls").text)
            d.execute_script("window.shElapsed = 30000; window.shTimers[1000][0]()")
            self.assertIn("Last position 31 s ago", d.find_element(By.ID, "sh-ls").text)
            d.execute_script("window.shLive.buses[0].at = Date.now() / 1000 - 1")
            self.poll_live_probe()
            self.assertTrue(d.find_element(By.ID, "sh-ls").text.endswith(" · Live"))
            self.assertFalse(d.find_elements(By.CSS_SELECTOR, ".sh-bus.stale"))
            d.execute_script("window.shLive.stale = true")
            self.poll_live_probe()
            self.assertIn("Last position 1 s ago", d.find_element(By.ID, "sh-ls").text)
            self.assertIn("off", d.find_element(By.ID, "sh-live").get_attribute("class"))
            d.execute_script("""
                window.shLive.stale = false;
                window.shLive.buses.push({...window.shLive.buses[0], at: Date.now() / 1000 - 25});
            """)
            self.poll_live_probe()
            self.assertIn("Oldest position 25 s ago", d.find_element(By.ID, "sh-ls").text)
            self.assertEqual(len(d.find_elements(By.CSS_SELECTOR, ".sh-bus.stale")), 1)
            d.execute_script("window.shHttpError = true")
            self.poll_live_probe()
            self.assertEqual(d.find_element(By.ID, "sh-lt").text, "Live location unavailable")
            self.assertFalse(d.find_element(By.ID, "sh-find").is_enabled())


class HubFrameLayoutTests(TestCase):
    """The app renders inside the hub's iframe, already below the hub navbar, so its
    CSS must not reserve space for that navbar (top:62px / 100vh-62px / phone 118px)
    nor scope rules under the hub's `.navbar`, which doesn't exist in the iframe."""

    def test_no_hub_navbar_offsets(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent
        self.assertTrue((root / "templates").is_dir(), root)
        self.assertTrue((root / "static").is_dir(), root)
        pattern = re.compile(r"top\s*:\s*(6[0-9]|118)px|100vh\s*-\s*(6[0-9]|118)px|\.navbar(?![-\w])")
        hits = []
        for path in list(root.glob("templates/**/*.html")) + list(root.glob("static/**/*.css")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path.relative_to(root)}:{n}: {line.strip()}")
        self.assertEqual(hits, [])


class AssistantToolTests(Fresh, TestCase):
    """The assistant's shuttle lookup, run the way the hub runs it (FakeBroker sessions)."""

    def setUp(self):
        from bdh_sdk import Stay, settings
        from bdh_sdk.testing import FakeBroker

        super().setUp()
        saved = settings._store
        self.broker = FakeBroker(grants=("tools.*", "settings.get", "events.subscribe"), install=True,
                                 app_id="shuttle")
        self.broker.start()
        self.addCleanup(settings.set_store, saved)
        self.addCleanup(self.broker.stop)
        self.broker.stay = Stay(guest="Smith")  # a voice session during a stay is in guest mode

    def call(self, when, args=None):
        from bdh_sdk import tools as sdk_tools
        from shuttle import assistant

        self.broker.assistant_tools.clear()
        for tool in assistant.tools(mode="guest"):
            self.broker.add_tool(tool, lambda a, t=tool.id: assistant.run_tool(t, a, mode="guest"))
        with at(when):
            return sdk_tools.call_tool(sdk_tools.open_session(), "next_shuttle", args)

    def ok(self, when, args=None):
        result = self.call(when, args)
        self.assertTrue(result["ok"], result)
        return result["result"]

    def test_every_mode_gets_one_readonly_lookup(self):
        from bdh_sdk import ALL_MODES, hooks
        from shuttle import assistant
        from shuttle.plugin import spec
        for mode in sorted(ALL_MODES):
            with self.subTest(mode=mode):
                tools = assistant.tools(mode=mode)
                self.assertEqual([t.id for t in tools], ["next_shuttle"])
                self.assertEqual((tools[0].modes, tools[0].readonly, tools[0].confirm), (ALL_MODES, True, False))
                self.assertLessEqual(len(tools[0].description), 300)
        self.assertEqual([t["id"] for t in hooks.run(spec, "tools", mode="guest")], ["next_shuttle"])

    def test_next_buses_to_the_village(self):
        self.assertEqual(self.ok(local(2026, 12, 1, 16, 33)), {
            "from": "Altitude stop (E6, by the hot tub)", "to": "Village Day Lodge", "day": "today",
            "first_bus": "8:45 am", "last_bus": "9:55 pm",
            "next": ["4:45 pm, in 12 minutes", "6:05 pm, in 1 hour 32 minutes", "6:45 pm, in 2 hours 12 minutes"],
            "arrival_at_village": assistant_no_arrival(),
        })
        self.assertEqual(self.ok(local(2026, 12, 1, 17, 45))["next"][0], "6:05 pm, in 20 minutes")
        self.assertEqual(self.ok(local(2026, 12, 1, 18, 5))["next"][0], "6:05 pm, leaving now")

    def test_back_from_the_village_says_when_it_reaches_the_lodge(self):
        result = self.ok(local(2026, 12, 1, 16, 33), {"direction": "to_lodge"})
        self.assertEqual((result["from"], result["to"]), ("Village Day Lodge", "Altitude stop (E6, by the hot tub)"))
        self.assertEqual(result["next"][:2], ["4:40 pm, in 7 minutes, reaching the lodge about 4:45 pm",
                                              "6 pm, in 1 hour 27 minutes, reaching the lodge about 6:05 pm"])
        self.assertEqual((result["first_bus"], result["last_bus"]), ("8:40 am", "9:50 pm"))
        self.assertNotIn("arrival_at_village", result)

    def test_last_bus_of_the_day(self):
        result = self.ok(local(2026, 12, 1, 21, 30))
        self.assertEqual(result["next"], ["9:55 pm, in 25 minutes"])
        self.assertEqual(result["note"], "The last bus today is at 9:55 pm.")

    def test_after_the_last_bus_gives_tomorrows_first(self):
        result = self.ok(local(2026, 12, 1, 22, 30))
        self.assertEqual(result["day"], "tomorrow")
        self.assertEqual(result["next"], ["8:45 am", "9:45 am", "11:45 am"])
        self.assertEqual(result["note"], "No more shuttles today; the last one left at 9:55 pm.")

    def test_tomorrows_timetable(self):
        result = self.ok(local(2026, 12, 1, 9), {"day": "tomorrow"})
        self.assertEqual(result["day"], "tomorrow")
        self.assertEqual(len(result["times"]), 13)
        self.assertEqual((result["times"][0], result["times"][-1]), ("8:45 am", "9:55 pm"))
        self.assertNotIn("next", result)

    def test_before_and_after_the_season(self):
        result = self.ok(local(2026, 11, 15, 10))
        self.assertEqual(result["note"], "The shuttle isn't running yet this season; it starts Saturday, November 21.")
        self.assertEqual((result["day"], result["first_bus"]), ("Saturday, November 21", "8:45 am"))
        for when, args in ((local(2027, 4, 10, 10), None), (local(2027, 4, 4, 9), {"day": "tomorrow"})):
            with self.subTest(when=when):
                result = self.call(when, args)
                self.assertEqual(result, {"ok": False, "error": "The shuttle is done for the season; "
                                                                "its last day was Sunday, April 4."})
        result = self.call(local(2027, 4, 4, 23))
        self.assertFalse(result["ok"])
        self.assertIn("last day of the season", result["error"])

    def test_season_dates_follow_the_hub_settings(self):
        self.broker.settings[("shuttle", "season_start")] = "2026-12-05"
        self.assertEqual(self.ok(local(2026, 12, 1, 10))["day"], "Saturday, December 5")


def assistant_no_arrival():
    from shuttle import assistant
    return assistant.NO_ARRIVAL