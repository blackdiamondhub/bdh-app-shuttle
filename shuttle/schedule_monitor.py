"""Detect changes to the official image without automatically accepting new times."""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import urlopen

BASELINE = Path(__file__).parent / "data" / "timetable.json"
MARKER = "<!-- shuttle-timetable-monitor -->"
MAX_BYTES = 8 * 1024 * 1024


class MonitorError(RuntimeError):
    pass


class _Images(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        alt = (attrs.get("alt") or "").lower()
        if tag == "img" and "east" in alt and ("schedule" in alt or "timetable" in alt):
            src = attrs.get("data-src") or attrs.get("src")
            if src:
                self.urls.add(src)


def image_url(html, page):
    parser = _Images()
    parser.feed(html)
    urls = set()
    for src in parser.urls:
        url = urlsplit(urljoin(page, src))
        if url.scheme != "https" or url.hostname != urlsplit(page).hostname:
            raise MonitorError("Official timetable image is not on the expected HTTPS site")
        path = re.sub(r"/sites/default/files/styles/[^/]+/public/", "/sites/default/files/", url.path)
        urls.add(urlunsplit((url.scheme, url.netloc, path, "", "")))
    if len(urls) != 1:
        raise MonitorError(f"Expected one East timetable image; found {len(urls)}")
    return urls.pop()


def download(url):
    with urlopen(url, timeout=30) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise MonitorError("Official timetable response exceeds the download limit")
    return data


def observe(baseline):
    page = download(baseline["source_page"]).decode("utf-8")
    url = image_url(page, baseline["source_page"])
    image = download(url)
    if not (image.startswith(b"\x89PNG\r\n\x1a\n") or image.startswith(b"\xff\xd8\xff")
            or (image.startswith(b"RIFF") and image[8:12] == b"WEBP")):
        raise MonitorError("Official timetable response is not a supported image")
    return {"image_url": url, "image_sha256": hashlib.sha256(image).hexdigest()}


def gh(*args, payload=None):
    command = ["gh", *args]
    data = None
    if payload is not None:
        command += ["--input", "-"]
        data = json.dumps(payload)
    result = subprocess.run(command, input=data, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def check(baseline, repo, assignee, dry_run=False):
    current = observe(baseline)
    if all(current[key] == baseline[key] for key in current):
        print(f"Official East timetable matches the baseline verified {baseline['verified_on']}.")
        return "unchanged"
    fingerprint = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
    stamp = f"<!-- shuttle-image:{fingerprint} -->"
    body = (
        f"{MARKER}\n{stamp}\n"
        "The official East shuttle timetable image changed. Review the stop times and service dates; "
        "**do not automatically apply extracted times to reminders.**\n\n"
        f"- Detected: {dt.datetime.now(dt.timezone.utc).isoformat()}\n"
        f"- [Official page]({baseline['source_page']})\n"
        f"- [Current timetable image]({current['image_url']})\n"
        f"- Reviewed baseline: {baseline['season']}, verified {baseline['verified_on']}\n"
        f"- Previous SHA-256: `{baseline['image_sha256']}`\n"
        f"- Current SHA-256: `{current['image_sha256']}`\n\n"
        "After review, update `shuttle/data/timetable.json` with the approved image URL/hash, "
        "service dates, trips and break times together, and close this issue with that PR."
    )
    if dry_run:
        print(body)
        return "changed"
    pages = gh("api", "--paginate", "--slurp", f"repos/{repo}/issues?state=open&per_page=100")
    issues = [issue for page in pages for issue in page
              if "pull_request" not in issue and MARKER in (issue.get("body") or "")]
    if len(issues) > 1:
        raise MonitorError("Multiple open timetable-review issues; combine them before retrying")
    if issues:
        issue = issues[0]
        pages = gh("api", "--paginate", "--slurp",
                   f"repos/{repo}/issues/{issue['number']}/comments?per_page=100")
        messages = [issue.get("body") or ""] + [comment.get("body") or "" for page in pages for comment in page]
        stamps = [found for message in messages for found in
                  re.findall(r"<!-- shuttle-image:([0-9a-f]{64}) -->", message)]
        if stamps and stamps[-1] == fingerprint:
            print(f"Change already tracked in issue #{issue['number']}.")
            return "tracked"
        gh("api", "-X", "POST", f"repos/{repo}/issues/{issue['number']}/comments", payload={"body": body})
        print(f"Updated timetable review issue #{issue['number']}.")
        return "updated"
    issue = gh("api", "-X", "POST", f"repos/{repo}/issues", payload={
        "title": "Sun Peaks East shuttle timetable changed",
        "body": body,
        "assignees": [assignee],
    })
    print(f"Opened timetable review issue #{issue['number']}.")
    return "created"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--assignee", default="sslivins")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.repo and not args.dry_run:
        parser.error("--repo or GITHUB_REPOSITORY is required")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    check(baseline, args.repo, args.assignee, args.dry_run)


if __name__ == "__main__":
    main()
