#!/usr/bin/env python3
"""Fetch citation metrics from Google Scholar and write them to metrics.json.

Run locally with:  python3 scripts/update_metrics.py
The GitHub Actions workflow .github/workflows/metrics.yml runs it on a schedule.

Only the standard library is used, so no dependencies need installing.

NOTE ON AUTOMATION: Google Scholar serves a captcha to datacenter IPs, so a run
from a GitHub Actions runner is usually turned away and the numbers stay as they
are. Running this script from a normal network (your machine) works, and so does
a run with a SerpAPI key if one is ever wired in. The scheduled run is therefore
best-effort: it refreshes the numbers when Scholar lets it through and leaves
them untouched otherwise.

metrics.json is only rewritten with numbers that parsed cleanly. Two kinds of
failure are told apart on purpose:

  * Scholar is unreachable or answers with a captcha / "unusual traffic" page —
    expected on CI, so the script warns and exits 0 and the run stays green.
  * The profile loads but cannot be parsed, or the numbers look implausible —
    that means the page layout changed and this script needs fixing, so it
    exits non-zero and the run fails loudly.

Set METRICS_STRICT=1 to fail on a block too.
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
PUBLIC_URL = "https://scholar.google.com/citations?user={uid}&hl=en"
OUT_FILE = pathlib.Path(__file__).resolve().parent.parent / "metrics.json"

# Treat a block as a hard failure (default: warn and exit 0).
STRICT = os.environ.get("METRICS_STRICT", "") == "1"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

# Markers of Google's "unusual traffic" / captcha interstitial.
BLOCK_MARKERS = ("unusual traffic", "/sorry/index", "CaptchaRedirect", "captcha-form", "not a robot")

STATS_RE = re.compile(r'class="gsc_rsb_std">(\d+)</td>')
ROW_RE = re.compile(r'class="gsc_a_tr"')
NAME_RE = re.compile(r'id="gsc_prf_in">([^<]*)<')


class Blocked(Exception):
    """Scholar refused to serve the profile (captcha, rate limit, network)."""


def is_block_page(page, final_url=""):
    if "/sorry/" in final_url:
        return True
    lowered = page[:4000].lower()
    return any(marker.lower() in lowered for marker in BLOCK_MARKERS)


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
                page = response.read().decode("utf-8", "replace")
                if is_block_page(page, response.geturl()):
                    last_error = "captcha / unusual-traffic page"
                else:
                    return page
        except urllib.error.HTTPError as error:
            last_error = f"HTTP {error.code}"
            if error.code not in (403, 429, 503):
                raise Blocked(f"could not reach Google Scholar: {last_error}")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
        time.sleep(5 * (attempt + 1))
    raise Blocked(f"Google Scholar did not serve the profile: {last_error}")


def main():
    try:
        return update()
    except Blocked as blocked:
        message = f"{blocked} — keeping the numbers already in metrics.json"
        print(f"::warning::{message}" if os.environ.get("GITHUB_ACTIONS") else message)
        return 1 if STRICT else 0


def update():
    page = fetch(PROFILE_URL.format(uid=USER_ID, start=0))

    name_match = NAME_RE.search(page)
    stats = [int(value) for value in STATS_RE.findall(page)]
    if name_match is None or len(stats) < 6:
        # Not a block (fetch already ruled that out), so the layout changed.
        raise SystemExit("the profile page no longer parses — scripts/update_metrics.py needs updating")

    # Table order: citations all/recent, h-index all/recent, i10-index all/recent.
    citations, h_index, i10_index = stats[0], stats[2], stats[4]

    articles = len(ROW_RE.findall(page))
    start = 100
    while articles == start:  # the profile is paginated 100 at a time
        articles += len(ROW_RE.findall(fetch(PROFILE_URL.format(uid=USER_ID, start=start))))
        start += 100

    if citations <= 0 or h_index <= 0 or articles <= 0:
        raise SystemExit(f"implausible values scraped ({citations}/{h_index}/{articles})")

    previous = {}
    if OUT_FILE.exists():
        previous = json.loads(OUT_FILE.read_text())

    data = {
        "name": html.unescape(name_match.group(1)).strip(),
        "source": "Google Scholar",
        "user_id": USER_ID,
        "profile_url": PUBLIC_URL.format(uid=USER_ID),
        "citations": citations,
        "h_index": h_index,
        "i10_index": i10_index,
        "works": articles,
        # Set by scripts/build_publications.py from the .bib files.
        "publications": previous.get("publications", articles),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    OUT_FILE.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data, indent=2))

    # Never regress on numbers that only ever grow — a sign of a partial page.
    for key in ("citations", "h_index"):
        if key in previous and data[key] < previous[key]:
            print(f"warning: {key} dropped from {previous[key]} to {data[key]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
