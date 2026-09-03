#!/usr/bin/env python3
"""Render the BibTeX sources into the publication sections of index.html.

Run locally with:  python3 scripts/build_publications.py
The GitHub Actions workflow .github/workflows/publications.yml runs it whenever
a .bib file changes and commits the regenerated index.html.

Sources (only the standard library is used, no dependencies to install):

  mypapers.bib            the DBLP export — replace it with a fresh export any
                          time, nothing in it needs hand-editing
  publications-site.bib   overlay: entries whose key matches one in the DBLP
                          export patch that entry (short venue names, badges,
                          awards, selection); entries with a new key are added
                          to the list. See its header for the field reference.

DBLP artefacts are handled automatically: LaTeX accents and {brace} protection
are decoded, series names are abbreviated (LNBIP, LNCS, CCIS, CEUR-WS), and a
CoRR/arXiv entry that duplicates a published paper becomes a "Preprint" badge on
that paper instead of a second list item.

The generated HTML is written between the marker comments in index.html:

    <!-- publications:filters:start -->  ... <!-- publications:filters:end -->
    <!-- publications:selected:start --> ... <!-- publications:selected:end -->
    <!-- publications:full:start -->     ... <!-- publications:full:end -->

Everything outside those markers is left untouched.
"""

import html
import json
import pathlib
import re
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE_FILE = ROOT / "mypapers.bib"
OVERLAY_FILE = ROOT / "publications-site.bib"
HTML_FILE = ROOT / "index.html"
SCHOLAR_FILE = ROOT / "scholar.json"

# Author whose name is highlighted in the author lists.
OWNER_LAST_NAME = "Bala"
OWNER_INITIAL = "S"

# Fields that only drive the website, stripped from the BibTeX copy button.
SITE_FIELDS = {"pubkind", "venue", "shortvenue", "oa", "preprint", "award", "selected", "category"}
# DBLP bookkeeping, likewise not worth copying.
DBLP_FIELDS = {"timestamp", "biburl", "bibsource"}

# Entry type -> (category, default caption, word used in Selected cards)
ENTRY_TYPES = {
    "article": ("journal", "Journal Article", "Journal"),
    "inproceedings": ("conference", "Conference Proceedings", "Conference"),
    "conference": ("conference", "Conference Proceedings", "Conference"),
    "incollection": ("chapter", "Book Chapter", "Book Chapter"),
    "inbook": ("chapter", "Book Chapter", "Book Chapter"),
    "misc": ("dataset", "Dataset", "Dataset"),
}
GROUPS = [
    # category, heading in the full list, label prefix, filter button label
    ("journal", "JOURNAL ARTICLES", "J", "Journals"),
    ("conference", "CONFERENCE &amp; WORKSHOP PROCEEDINGS", "C", "Conferences &amp; Workshops"),
    ("chapter", "BOOK CHAPTERS", "Ch", "Book Chapters"),
    ("preprint", "PREPRINTS", "P", "Preprints"),
    ("dataset", "DATASETS &amp; SOFTWARE", "D", "Datasets"),
]
CATEGORY_WORD = {"preprint": "Preprint", "dataset": "Dataset"}

SERIES_SHORT = {
    "Lecture Notes in Business Information Processing": "LNBIP",
    "Lecture Notes in Computer Science": "LNCS",
    "Communications in Computer and Information Science": "CCIS",
    "CEUR Workshop Proceedings": "CEUR-WS",
}
# Publishers that only repeat what the series already says.
SKIP_PUBLISHERS = {"CEUR-WS.org"}
MONTHS = {
    "jan": "Jan.", "feb": "Feb.", "mar": "Mar.", "apr": "Apr.", "may": "May", "jun": "Jun.",
    "jul": "Jul.", "aug": "Aug.", "sep": "Sep.", "oct": "Oct.", "nov": "Nov.", "dec": "Dec.",
}

LINK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
    'style="display:inline;vertical-align:middle"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
    '<polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>'
)
LOCK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" '
    'style="display:inline;vertical-align:middle"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>'
    '<path d="M7 11V7a5 5 0 0 1 9.9-1"/></svg>'
)
STAR_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-yellow-600 flex-shrink-0" viewBox="0 0 24 24" '
    'fill="currentColor"><path d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 '
    '1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 '
    '00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 '
    '00.951-.69l1.519-4.674z"/></svg>'
)
PREPRINT_STYLE = 'style="background:#fff7ed;color:#c2410c;border-color:#fed7aa;"'

ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", re.DOTALL)
FIELD_RE = re.compile(
    r"(\w+)\s*=\s*(?:\{(?P<braced>.*?)\}|\"(?P<quoted>.*?)\"|(?P<bare>[^,\n]*))\s*,?\s*"
    r"(?=\n\s*\w+\s*=|\s*$)",
    re.DOTALL,
)

# LaTeX escapes seen in DBLP exports.
COMBINING = {
    "'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303",
    "c": "\u0327", "v": "\u030c", ".": "\u0307", "=": "\u0304", "u": "\u0306",
    "H": "\u030b", "r": "\u030a", "k": "\u0328",
}
LATIN_ESCAPES = {
    r"\ss": "ß", r"\o": "ø", r"\O": "Ø", r"\aa": "å", r"\AA": "Å", r"\ae": "æ",
    r"\AE": "Æ", r"\l": "ł", r"\L": "Ł", r"\i": "i", r"\j": "j",
}
ACCENT_RE = re.compile(r"\\([`'^\"~=.uvHrck])\s*\{?\s*(?:\\([ij])|([A-Za-z]))\s*\}?")


def detex(value):
    """Turn a DBLP/BibTeX value into plain text."""
    if not value:
        return value

    def accent(match):
        mark = COMBINING[match.group(1)]
        letter = match.group(2) or match.group(3)
        return unicodedata.normalize("NFC", letter + mark)

    for _ in range(3):  # nested forms such as {\'{\i}}
        new = ACCENT_RE.sub(accent, value)
        if new == value:
            break
        value = new
    for escape, char in LATIN_ESCAPES.items():
        value = re.sub(re.escape(escape) + r"(?![A-Za-z])\s*\{?\}?", char, value)
    value = value.replace("{-}", "-").replace("\\&", "&").replace("\\_", "_")
    value = re.sub(r"\\[%$#]", lambda m: m.group(0)[1], value)
    value = value.replace("{", "").replace("}", "")
    return " ".join(value.split())


def parse_bib(path):
    entries = []
    if not path.exists():
        return entries
    text = re.sub(r"^\s*%.*$", "", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    for entry_type, key, body in ENTRY_RE.findall(text):
        fields = {"_type": entry_type.lower(), "_key": key.strip()}
        for match in FIELD_RE.finditer(body):
            raw = match.group("braced")
            if raw is None:
                raw = match.group("quoted")
            if raw is None:
                raw = match.group("bare") or ""
            fields[match.group(1).lower()] = detex(raw)
        entries.append(fields)
    return entries


def load_entries():
    entries = parse_bib(SOURCE_FILE)
    if not entries:
        raise SystemExit(f"no entries found in {SOURCE_FILE.name}")
    by_key = {entry["_key"]: entry for entry in entries}

    for patch in parse_bib(OVERLAY_FILE):
        target = by_key.get(patch["_key"])
        if target is None:  # a publication DBLP does not have
            entries.append(patch)
            by_key[patch["_key"]] = patch
            continue
        for name, value in patch.items():
            if name in ("_type", "_key"):
                continue
            if value == "":  # empty override removes the field
                target.pop(name, None)
            else:
                target[name] = value

    for entry in entries:
        if entry["_type"] not in ENTRY_TYPES:
            raise SystemExit(f"{entry['_key']}: unsupported entry type @{entry['_type']}")
        for required in ("title", "author"):
            if not entry.get(required):
                raise SystemExit(f"{entry['_key']}: missing required field '{required}'")
    return merge_preprints(entries)


def normalized_title(entry):
    return re.sub(r"[^a-z0-9]+", " ", entry["title"].lower()).strip()


def arxiv_url(entry):
    if entry.get("eprint"):
        return f"https://arxiv.org/abs/{entry['eprint']}"
    return entry.get("url", "")


def merge_preprints(entries):
    """Fold a CoRR entry into the published paper of the same title."""
    published = {}
    for entry in entries:
        if entry.get("journal") != "CoRR":
            published.setdefault(normalized_title(entry), entry)

    kept = []
    for entry in entries:
        if entry.get("journal") == "CoRR":
            target = published.get(normalized_title(entry))
            if target is not None:
                target.setdefault("preprint", arxiv_url(entry))
                continue
            entry["category"] = entry.get("category", "preprint")
        kept.append(entry)
    return kept


def category_of(entry):
    return entry.get("category") or ENTRY_TYPES[entry["_type"]][0]


def split_authors(raw):
    return [name.strip() for name in re.split(r"\s+and\s+", raw) if name.strip()]


def name_parts(name):
    if "," in name:  # "Bala, Saimir"
        last, first = name.split(",", 1)
        return first.strip(), last.strip()
    tokens = name.split()
    return " ".join(tokens[:-1]), tokens[-1]


def is_owner(name):
    first, last = name_parts(name)
    return last == OWNER_LAST_NAME and first.lstrip().startswith(OWNER_INITIAL)


def abbreviate(name):
    """"Thanh Nguyen" -> "T. Nguyen"; names already abbreviated stay as they are."""
    first, last = name_parts(name)
    if not first:
        return last
    initials = " ".join(
        token if token.endswith(".") else token[0] + "." for token in first.split() if token
    )
    return f"{initials} {last}"


def render_authors(entry, short=False):
    rendered = []
    for name in split_authors(entry["author"]):
        shown = html.escape(abbreviate(name) if short else name)
        rendered.append(f'<span class="font-bold">{shown}</span>' if is_owner(name) else shown)
    return ", ".join(rendered)


def venue(entry):
    """Display name of the journal, proceedings or repository."""
    if entry.get("venue"):
        return entry["venue"]
    name = entry.get("journal") or entry.get("booktitle") or entry.get("publisher") or ""
    name = re.split(r"\s+-\s+", name)[0]  # DBLP appends the edition and dates
    return re.sub(r",\s*Proceedings$", "", name)


def detail_parts(entry):
    """The comma-separated bits that follow the bold venue name."""
    parts = []
    if entry["_type"] == "article" and entry.get("volume"):
        number = f"({entry['number']})" if entry.get("number") else ""
        parts.append(entry["volume"] + number)
    elif entry.get("series") or entry.get("volume"):
        series = SERIES_SHORT.get(entry.get("series", ""), entry.get("series", ""))
        volume = entry.get("volume", "")
        if series == "CEUR-WS" and volume and not volume.startswith("Vol."):
            volume = "Vol. " + volume
        joined = " ".join(part for part in (series, volume) if part)
        if joined:
            parts.append(joined)
    publisher = entry.get("publisher")
    if publisher and publisher not in SKIP_PUBLISHERS and entry["_type"] != "article":
        parts.append(publisher)
    if entry.get("note"):
        parts.append(entry["note"])
    if entry.get("address"):
        parts.append(entry["address"])
    if entry.get("year"):
        month = MONTHS.get(entry.get("month", "").lower().rstrip("."), entry.get("month", ""))
        parts.append(" ".join(part for part in (month, entry["year"]) if part))
    if entry.get("pages"):
        pages = entry["pages"].replace("--", "–")
        parts.append(("pp. " if "–" in pages else "p. ") + pages)
    return parts


def detail_html(entry):
    detail = ", ".join(detail_parts(entry))
    bold_venue = f'<span class="font-bold">{html.escape(venue(entry))}</span>'
    return bold_venue + (f", {html.escape(detail)}." if detail else ".")


def main_link(entry):
    if entry.get("url"):
        return entry["url"]
    if entry.get("doi"):
        doi = entry["doi"]
        return doi if doi.startswith("http") else "https://doi.org/" + doi
    return ""


def badges(entry):
    """External-link icon plus Open Access / Preprint badges."""
    out = []
    link = main_link(entry)
    if link:
        out.append(
            f'<a href="{html.escape(link)}" target="_blank" class="pub-ext-link" title="Read paper">{LINK_SVG}</a>'
        )
    if entry.get("oa"):
        url = entry["oa"]
        title = "Open Access — CEUR-WS" if "ceur-ws.org" in url else "Open Access"
        out.append(
            f'<a href="{html.escape(url)}" target="_blank" class="oa-badge" title="{title}">{LOCK_SVG} Open Access</a>'
        )
    if entry.get("preprint") and entry["preprint"] != link:
        url = entry["preprint"]
        if "arxiv.org" in url:
            title = "Free preprint on arXiv"
        elif "researchsquare.com" in url:
            title = "Freely available preprint on Research Square"
        else:
            title = "Freely available preprint"
        out.append(
            f'<a href="{html.escape(url)}" target="_blank" class="oa-badge" {PREPRINT_STYLE} '
            f'title="{title}">{LOCK_SVG} Preprint</a>'
        )
    return out


def bibtex_source(entry):
    """The entry as clean BibTeX, for the copy button."""
    lines = [f"@{entry['_type']}{{{entry['_key']},"]
    for name, value in entry.items():
        if name.startswith("_") or name in SITE_FIELDS or name in DBLP_FIELDS:
            continue
        lines.append(f"  {name} = {{{value}}},")
    lines[-1] = lines[-1].rstrip(",")
    lines.append("}")
    return "\n".join(lines)


def award_block(entry, indent):
    if not entry.get("award"):
        return ""
    return (
        f'{indent}<div class="flex items-center gap-1.5 mb-2">\n'
        f'{indent}  {STAR_SVG}\n'
        f'{indent}  <span class="text-xs font-bold text-yellow-700 uppercase tracking-wide">'
        f'{html.escape(entry["award"])}</span>\n'
        f'{indent}</div>\n'
    )


def item_classes(entry):
    classes = "publication-item"
    if entry.get("award"):
        classes += " border-l-4 border-yellow-500 bg-yellow-50"
    return classes


def link_lines(entry, indent):
    links = badges(entry)
    if not links:
        return ""
    return "\n" + "\n".join(f"{indent}    {link}" for link in links)


def caption(entry):
    default = ENTRY_TYPES[entry["_type"]][1]
    if category_of(entry) in CATEGORY_WORD:
        default = CATEGORY_WORD[category_of(entry)]
    return entry.get("pubkind", default)


def render_full_item(entry, label):
    indent = "    "
    return (
        f'{indent}<div class="{item_classes(entry)}" data-type="{category_of(entry)}">\n'
        + award_block(entry, indent + "  ")
        + f'{indent}  <p class="text-xs font-bold uppercase text-primary mb-1">'
        f'{html.escape(caption(entry))} ({label})</p>\n'
        f'{indent}  <p class="font-semibold text-lg text-gray-800">\n'
        f'{indent}    {html.escape(entry["title"])}.{link_lines(entry, indent)}\n'
        f'{indent}  </p>\n'
        f'{indent}  <p class="text-sm text-gray-600 italic">\n'
        f'{indent}    {render_authors(entry)}.\n'
        f'{indent}    {detail_html(entry)}\n'
        f'{indent}  </p>\n'
        f'{indent}  <button class="bibtex-btn" type="button" '
        f'data-bibtex="{html.escape(bibtex_source(entry), quote=True)}">BibTeX</button>\n'
        f'{indent}</div>\n'
    )


def render_selected_item(entry):
    indent = "                "
    category = category_of(entry)
    word = CATEGORY_WORD.get(category) or ENTRY_TYPES[entry["_type"]][2]
    if "Workshop" in entry.get("pubkind", ""):
        word = "Workshop"
    header_parts = [entry.get("shortvenue") or venue(entry)]
    if entry["_type"] != "article" and entry.get("publisher") not in (None, "") \
            and entry["publisher"] not in SKIP_PUBLISHERS:
        header_parts.append(entry["publisher"])
    if entry.get("year"):
        header_parts.append(entry["year"])
    header = f"{word} &mdash; " + html.escape(", ".join(header_parts))

    return (
        f'{indent}<div class="{item_classes(entry)}">\n'
        + award_block(entry, indent + "  ")
        + f'{indent}  <p class="text-xs font-bold uppercase text-primary mb-1">{header}</p>\n'
        f'{indent}  <p class="font-semibold text-lg text-gray-800">\n'
        f'{indent}    {html.escape(entry["title"])}.{link_lines(entry, indent)}\n'
        f'{indent}  </p>\n'
        f'{indent}  <p class="text-sm text-gray-600 italic">'
        f'{render_authors(entry, short=True)}. {detail_html(entry)}</p>\n'
        f'{indent}</div>\n'
    )


def sort_key(entry):
    # Newest first; entries without a year (forthcoming) come first.
    return -int(entry.get("year") or 9999)


def replace_block(page, marker, body):
    pattern = re.compile(
        rf"(<!-- publications:{marker}:start -->\n).*?([ \t]*<!-- publications:{marker}:end -->)",
        re.DOTALL,
    )
    if not pattern.search(page):
        raise SystemExit(f"marker publications:{marker}:start/end not found in index.html")
    return pattern.sub(lambda m: m.group(1) + body + m.group(2), page, count=1)


def main():
    entries = load_entries()

    groups = {category: [] for category, _, _, _ in GROUPS}
    for entry in entries:
        category = category_of(entry)
        if category not in groups:
            raise SystemExit(f"{entry['_key']}: unknown category '{category}'")
        groups[category].append(entry)

    full_html = []
    for category, heading, prefix, _ in GROUPS:
        items = sorted(groups[category], key=sort_key)
        if not items:
            continue
        full_html.append(f"    <!-- ================= {heading} ================= -->\n")
        for index, entry in enumerate(items, start=1):
            full_html.append(render_full_item(entry, f"{prefix}{index}"))

    filters = ['    <button class="pub-filter-btn" data-filter="all" data-label="All">All</button>\n']
    for category, _, _, label in GROUPS:
        if groups[category]:
            filters.append(
                f'    <button class="pub-filter-btn" data-filter="{category}" '
                f'data-label="{label}">{label}</button>\n'
            )

    selected = sorted((e for e in entries if e.get("selected") == "true"), key=sort_key)
    if not selected:
        raise SystemExit("no entry is marked selected = {true}")

    page = HTML_FILE.read_text(encoding="utf-8")
    page = replace_block(page, "filters", "".join(filters))
    page = replace_block(page, "selected", "\n".join(render_selected_item(e) for e in selected))
    page = replace_block(page, "full", "\n".join(full_html))
    HTML_FILE.write_text(page, encoding="utf-8")

    # The publication count in the metrics tile follows the list.
    if SCHOLAR_FILE.exists():
        scholar = json.loads(SCHOLAR_FILE.read_text())
        if scholar.get("publications") != len(entries):
            scholar["publications"] = len(entries)
            SCHOLAR_FILE.write_text(json.dumps(scholar, indent=2) + "\n")

    counts = ", ".join(f"{len(groups[c])} {c}" for c, _, _, _ in GROUPS if groups[c])
    print(f"index.html updated: {len(entries)} entries ({counts}), {len(selected)} selected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
