import hashlib
from unittest import TestCase
from unittest.mock import patch

from shuttle import schedule_monitor as monitor

PAGE = "https://www.sunpeaksresort.com/shuttle"
IMAGE = "https://www.sunpeaksresort.com/sites/default/files/2026-11/east.png"
PNG = b"\x89PNG\r\n\x1a\nreviewed image"
BASE = {"source_page": PAGE, "image_url": IMAGE, "image_sha256": hashlib.sha256(PNG).hexdigest(),
        "season": "2026/27", "verified_on": "2026-11-23"}
REPO = "blackdiamondhub/bdh-app-shuttle"


def html(src):
    return f'<img alt="Sun Peaks Shuttle Schedule East" data-src="{src}">'


class ScheduleMonitorTests(TestCase):
    def test_discovers_new_filename_and_normalizes_lazy_responsive_images(self):
        src = "/sites/default/files/styles/1536/public/2027-11/new-east.png?itok=abc"
        same = "/sites/default/files/2027-11/new-east.png"
        self.assertEqual(monitor.image_url(html(src) + html(same), PAGE),
                         "https://www.sunpeaksresort.com" + same)

    def test_rejects_missing_ambiguous_or_external_images(self):
        for page in ('<img alt="West schedule" src="/west.png">', html(IMAGE) + html("/other.png"),
                     html("https://other.example/east.png")):
            with self.subTest(page=page), self.assertRaises(monitor.MonitorError):
                monitor.image_url(page, PAGE)

    @patch.object(monitor, "gh")
    def test_unchanged_image_needs_no_github_write(self, gh):
        with patch.object(monitor, "download", side_effect=[html(IMAGE).encode(), PNG]):
            self.assertEqual(monitor.check(BASE, REPO, "sslivins"), "unchanged")
        gh.assert_not_called()

    @patch.object(monitor, "gh")
    def test_changed_url_or_bytes_are_detected_without_advancing_baseline(self, gh):
        for url, image in ((IMAGE.replace("2026-11", "2027-11"), PNG), (IMAGE, PNG + b"changed")):
            with self.subTest(url=url), patch.object(monitor, "download",
                                                     side_effect=[html(url).encode(), image]):
                self.assertEqual(monitor.check(BASE, REPO, "sslivins", dry_run=True), "changed")
        self.assertEqual(BASE["image_sha256"], hashlib.sha256(PNG).hexdigest())
        gh.assert_not_called()

    @patch.object(monitor, "gh")
    def test_download_and_non_image_failures_are_not_reported_as_unchanged(self, gh):
        with patch.object(monitor, "download", side_effect=OSError("network down")), self.assertRaises(OSError):
            monitor.check(BASE, REPO, "sslivins")
        with patch.object(monitor, "download", side_effect=[html(IMAGE).encode(), b"<html>error</html>"]), \
                self.assertRaises(monitor.MonitorError):
            monitor.check(BASE, REPO, "sslivins")
        gh.assert_not_called()

    @patch.object(monitor, "gh")
    def test_one_assigned_issue_then_no_daily_duplicates_and_comment_on_next_change(self, gh):
        current = {"image_url": IMAGE, "image_sha256": "a" * 64}
        gh.side_effect = [[[]], {"number": 9}]
        with patch.object(monitor, "observe", return_value=current):
            self.assertEqual(monitor.check(BASE, REPO, "sslivins"), "created")
        payload = gh.call_args.kwargs["payload"]
        self.assertEqual(payload["assignees"], ["sslivins"])
        issue = {"number": 9, "body": payload["body"] + "\nMaintainer notes must survive."}
        gh.reset_mock()
        gh.side_effect = [[[issue]], [[]]]
        with patch.object(monitor, "observe", return_value=current):
            self.assertEqual(monitor.check(BASE, REPO, "sslivins"), "tracked")
        self.assertEqual(gh.call_count, 2)  # reads only

        gh.reset_mock()
        gh.side_effect = [[[issue]], [[]], {"id": 10}]
        changed = {**current, "image_sha256": "b" * 64}
        with patch.object(monitor, "observe", return_value=changed):
            self.assertEqual(monitor.check(BASE, REPO, "sslivins"), "updated")
        self.assertTrue(gh.call_args.args[-1].endswith("/issues/9/comments"))
        comment = {"body": gh.call_args.kwargs["payload"]["body"]}
        self.assertTrue(issue["body"].endswith("Maintainer notes must survive."))

        gh.reset_mock()
        gh.side_effect = [[[issue]], [[comment]]]
        with patch.object(monitor, "observe", return_value=changed):
            self.assertEqual(monitor.check(BASE, REPO, "sslivins"), "tracked")
        self.assertEqual(gh.call_count, 2)
        gh.side_effect = [[[issue]], [[comment]], {"id": 11}]
        with patch.object(monitor, "observe", return_value=current):
            self.assertEqual(monitor.check(BASE, REPO, "sslivins"), "updated")  # reversion is a new event

    @patch.object(monitor, "gh", side_effect=RuntimeError("GitHub unavailable"))
    def test_github_failure_surfaces(self, gh):
        with patch.object(monitor, "observe", return_value={"image_url": IMAGE, "image_sha256": "changed"}), \
                self.assertRaises(RuntimeError):
            monitor.check(BASE, REPO, "sslivins")

    def test_oversized_response_fails(self):
        with patch.object(monitor, "urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = b"x" * (monitor.MAX_BYTES + 1)
            with self.assertRaises(monitor.MonitorError):
                monitor.download(IMAGE)
