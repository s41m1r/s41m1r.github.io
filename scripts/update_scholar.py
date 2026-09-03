#!/usr/bin/env python3
"""Fetch citation metrics from Google Scholar and write them to scholar.json.

Run locally with:  python3 scripts/update_scholar.py
The GitHub Actions workflow .github/workflows/scholar.yml runs it on a schedule.

Only the standard library is used, so no dependencies need installing.
If Scholar answers with a captcha / blocked page, the script exits non-zero and
leaves scholar.json untouched, so the site keeps showing the last good numbers.
"""

import html
import json
import os
import pathlib
import random
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

USER_ID = os.environ.get("SCHOLAR_USER_ID", "ZR0Bo_QAAAAJ")
PROFILE_URL = "https://scholar.google.com/citations?user={uid}&hl=en&cstart={start}&pagesize=100"
OUT_FILE = pathlib.Path(__file__).resolve().parent.parent / "scholar.json"

# "scholar" makes the Publications tile mirror Scholar's article count,
# "manual" keeps whatever publications value is already in scholar.json.
PUBLICATIONS_SOURCE = os.environ.get("SCHOLAR_PUBLICATIONS_SOURCE", "manual")

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

STATS_RE = re.compile(r'class="gsc_rsb_std">(\d+)</td>')
ROW_RE = re.compile(r'class="gsc_a_tr"')
NAME_RE = re.compile(r'id="gsc_prf_in">([^<]*)<')


def fetch(url, attempts=4):
    last_error = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
        time.sleep(5 * (attempt + 1))
    raise SystemExit(f"could not reach Google Scholar: {last_error}")


def main():
    page = fetch(PROFILE_URL.format(uid=USER_ID, start=0))

    name_match = NAME_RE.search(page)
    stats = [int(value) for value in STATS_RE.findall(page)]
    if name_match is None or len(stats) < 6:
        raise SystemExit("unexpected page (captcha or layout change) — not updating scholar.json")

    # Table order: citations all/recent, h-index all/recent, i10-index all/recent.
    citations, h_index, i10_index = stats[0], stats[2], stats[4]

    articles = len(ROW_RE.findall(page))
    start = 100
    while articles == start:  # profile is paginated 100 at a time
        more = fetch(PROFILE_URL.format(uid=USER_ID, start=start))
        articles += len(ROW_RE.findall(more))
        start += 100

    if citations <= 0 or h_index <= 0 or articles <= 0:
        raise SystemExit("implausible values scraped — not updating scholar.json")

    previous = {}
    if OUT_FILE.exists():
        previous = json.loads(OUT_FILE.read_text())

    if PUBLICATIONS_SOURCE == "scholar":
        publications = articles
    else:
        publications = previous.get("publications", articles)

    data = {
        "name": html.unescape(name_match.group(1)).strip(),
        "user_id": USER_ID,
        "citations": citations,
        "h_index": h_index,
        "i10_index": i10_index,
        "scholar_articles": articles,
        "publications": publications,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    OUT_FILE.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data, indent=2))

    # Never regress on numbers that only ever grow — a sign of a partial page.
    for key in ("citations", "h_index"):
        if key in previous and data[key] < previous[key]:
            print(f"warning: {key} dropped from {previous[key]} to {data[key]}", file=sys.stderr)


if __name__ == "__main__":
    main()
