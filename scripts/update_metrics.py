#!/usr/bin/env python3
"""Fetch citation metrics from OpenAlex and write them to metrics.json.

Run locally with:  python3 scripts/update_metrics.py
The GitHub Actions workflow .github/workflows/metrics.yml runs it on a schedule.

OpenAlex is used rather than Google Scholar because Scholar serves a captcha to
datacenter IPs, so a scheduled run from a CI runner can never reach it. OpenAlex
has an open API with no key, no rate limit worth worrying about at one call a
day, and the author is addressed by ORCID, so there is no name disambiguation to
get wrong. Its h-index and i10-index match Scholar; its citation count is lower,
because Scholar also counts preprints, theses and non-indexed venues.

Only the standard library is used, and the request carries no personal data.

metrics.json is only rewritten with numbers that parsed cleanly, so the site
keeps showing the last good values otherwise. Two kinds of failure are told
apart on purpose:

  * OpenAlex is unreachable or rate-limits the request — transient, so the
    script warns and exits 0 and the scheduled run stays green.
  * The response parses but the numbers are missing or implausible — that means
    the API changed and this script needs fixing, so it exits non-zero.

Set METRICS_STRICT=1 to fail on an unreachable API too.
"""

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ORCID = os.environ.get("ORCID", "0000-0001-7179-1901")
API_URL = "https://api.openalex.org/authors/https://orcid.org/{orcid}"
OUT_FILE = pathlib.Path(__file__).resolve().parent.parent / "metrics.json"

# Treat an unreachable API as a hard failure (default: warn and exit 0).
STRICT = os.environ.get("METRICS_STRICT", "") == "1"

USER_AGENT = "saimir-bala-portfolio (+https://github.com/s41m1r/saimir-bala-portfolio)"


class Unavailable(Exception):
    """OpenAlex could not be reached (network trouble, rate limit, outage)."""


def fetch(url, attempts=3):
    last_error = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            last_error = f"HTTP {error.code}"
            if error.code not in (429, 500, 502, 503, 504):
                raise Unavailable(f"OpenAlex answered {last_error}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            last_error = error
        time.sleep(5 * (attempt + 1))
    raise Unavailable(f"OpenAlex did not answer: {last_error}")


def main():
    try:
        return update()
    except Unavailable as unavailable:
        message = f"{unavailable} — keeping the numbers already in metrics.json"
        print(f"::warning::{message}" if os.environ.get("GITHUB_ACTIONS") else message)
        return 1 if STRICT else 0


def update():
    author = fetch(API_URL.format(orcid=ORCID))

    stats = author.get("summary_stats") or {}
    citations = author.get("cited_by_count")
    h_index = stats.get("h_index")
    i10_index = stats.get("i10_index")
    works = author.get("works_count")

    if None in (citations, h_index, i10_index, works):
        raise SystemExit("OpenAlex response is missing the metrics — this script needs updating")
    if citations <= 0 or h_index <= 0 or works <= 0:
        raise SystemExit(f"implausible values from OpenAlex ({citations}/{h_index}/{works})")

    previous = {}
    if OUT_FILE.exists():
        previous = json.loads(OUT_FILE.read_text())

    openalex_id = (author.get("id") or "").rsplit("/", 1)[-1]
    data = {
        "name": author.get("display_name", ""),
        "source": "OpenAlex",
        "orcid": ORCID,
        "openalex_id": openalex_id,
        "profile_url": author.get("id", ""),
        "citations": citations,
        "h_index": h_index,
        "i10_index": i10_index,
        "works": works,
        # Set by scripts/build_publications.py from the .bib files.
        "publications": previous.get("publications", works),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    OUT_FILE.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data, indent=2))

    # Never regress on numbers that only ever grow — a sign of a partial answer.
    for key in ("citations", "h_index"):
        if key in previous and data[key] < previous[key]:
            print(f"warning: {key} dropped from {previous[key]} to {data[key]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
