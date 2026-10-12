#!/usr/bin/env python3
"""Fetch public contributions and render static HTML with the standard library.

Run `python3 scripts/contributions.py --refresh` to fetch both sources, or omit
--refresh to render the checked-in snapshot offline. GH_TOKEN is optional locally.
Only the marker-delimited section of index.html is generated.

The Pages workflow refreshes daily at 04:17 UTC and on pushes to master. Enable
Settings > Pages > Source > GitHub Actions when publishing it. A failed refresh
stops deployment, leaving the last published site intact; it never commits files.

Scope: public merged PRs outside the user's repositories, plus lore messages sent
by EMAIL. Forwarded authorship and credits in other people's messages are excluded.
Kernel revisions with matching subjects are collapsed; changed subjects may need
manual reconciliation. Lore alone cannot establish whether a patch was merged.
"""

import argparse
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
USER = "mqqz"
EMAIL = "mo@sdhn.cc"
GITHUB_QUERY = f"is:pr is:merged author:{USER} -user:{USER} is:public"
GITHUB_SEARCH = "https://github.com/search?" + urlencode(
    {"q": GITHUB_QUERY, "type": "pullrequests"}
)
LORE_QUERY = f"f:{EMAIL}"
LORE_SEARCH = "https://lore.kernel.org/all/?" + urlencode({"q": LORE_QUERY})
LORE_HOSTS = ("lore.kernel.org", "lore-kernel.gnuweeb.org")
ATOM = "{http://www.w3.org/2005/Atom}"
THREAD = "{http://purl.org/syndication/thread/1.0}"
START = "    <!-- contributions:start -->"
END = "    <!-- contributions:end -->"


def fetch(url):
    headers = {"User-Agent": "mqqz-contributions/1.0"}
    if urlsplit(url).hostname == "api.github.com":
        headers["Accept"] = "application/vnd.github+json"
        if token := os.environ.get("GH_TOKEN"):
            headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return response.read()


def github_contributions():
    entries = []
    for page in range(1, 11):
        url = "https://api.github.com/search/issues?" + urlencode(
            {"q": GITHUB_QUERY, "per_page": 100, "page": page,
             "sort": "created", "order": "desc"}
        )
        result = json.loads(fetch(url))
        if result["incomplete_results"] or result["total_count"] > 1000:
            raise ValueError("GitHub search is incomplete; split the query before publishing")
        for item in result["items"]:
            # Search is scoped to public, external, merged PRs, not the events feed.
            project = item["repository_url"].removeprefix("https://api.github.com/repos/")
            entries.append({"project": project, "title": item["title"],
                            "url": item["html_url"], "date": item["closed_at"][:10],
                            "kind": "Merged PR"})
        if len(entries) >= result["total_count"]:
            return sorted(entries, key=lambda entry: entry["date"], reverse=True)
        if not result["items"]:
            break
    raise ValueError("GitHub pagination stopped before all results were fetched")


def lore_url(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc not in LORE_HOSTS:
        raise ValueError("Unexpected lore link")
    if not parts.path.startswith("/all/"):
        raise ValueError("Unexpected lore archive path")
    return urlunsplit(("https", LORE_HOSTS[0], parts.path, "", ""))


def parse_atom(raw):
    # public-inbox can emit this bare closing tag for an empty search page.
    if raw.strip() == b"</feed>":
        return []
    root = ET.fromstring(raw)
    if root.tag != ATOM + "feed":
        raise ValueError("Expected an Atom feed, not an archive error or bot challenge")
    messages = []
    for entry in root.findall(ATOM + "entry"):
        author = entry.findtext(ATOM + "author/" + ATOM + "email", "")
        title_element = entry.find(ATOM + "title")
        title = title_element.text or ""
        if title_element.get("type") == "html":
            title = html.unescape(title)
        parent = entry.find(THREAD + "in-reply-to")
        content = entry.find(ATOM + "content")
        link = entry.find(ATOM + "link")
        messages.append({"author": author, "title": title,
                         "url": lore_url(link.attrib["href"]),
                         "parent": lore_url(parent.attrib["href"]) if parent is not None else None,
                         "date": entry.findtext(ATOM + "updated"),
                         "body": "".join(content.itertext()) if content is not None else ""})
    return messages


def fetch_lore(host):
    messages = {}
    offset = 0
    for _ in range(20):
        url = f"https://{host}/all/?" + urlencode(
            {"q": LORE_QUERY, "x": "A", "o": offset}
        )
        page = parse_atom(fetch(url))
        if not page:
            return list(messages.values())
        previous_count = len(messages)
        messages.update((entry["url"], entry) for entry in page)
        if len(messages) == previous_count:
            raise ValueError("Lore pagination repeated a page")
        offset += len(page)
    raise ValueError("Lore pagination limit reached; refusing a partial refresh")


def subject(title):
    return re.sub(r"^(?:(?:Re:\s*)|(?:\[[^\]]+\]\s*))+", "", title,
                  flags=re.IGNORECASE).strip()


def kernel_contributions(messages):
    # Exact sender check: public-inbox's f: search is tokenized.
    messages = [m for m in messages if m["author"].casefold() == EMAIL.casefold()]
    patches = {m["url"]: m for m in messages
               if re.match(r"^\[PATCH\b", m["title"], re.IGNORECASE)}
    own_subjects = {subject(m["title"]).casefold() for m in patches.values()}
    grouped = {}
    for message in sorted(messages, key=lambda m: m["date"]):
        title = subject(message["title"])
        key = title.casefold()
        if message["url"] in patches:
            # A cover letter represents its entire series, across revisions.
            parent = patches.get(message["parent"])
            if parent and re.search(r"\b0/\d+\]", parent["title"]):
                continue
            kind = "Submitted series" if re.search(r"\b0/\d+\]", message["title"]) else "Submitted patch"
        else:
            # Replies to our own patches are conversation, not new contributions.
            if key in own_subjects:
                continue
            review = re.search(r"^Reviewed-by:\s*[^\n]*<" + re.escape(EMAIL) + r">\s*$",
                               message["body"], re.MULTILINE | re.IGNORECASE)
            kind = "Review" if review else "Discussion"
        grouped[(key, kind)] = {"project": "Linux kernel", "title": title,
                                "url": message["url"], "date": message["date"][:10],
                                "kind": kind}
    return sorted(grouped.values(), key=lambda entry: entry["date"], reverse=True)


def refresh():
    github = github_contributions()
    failures = []
    for host in LORE_HOSTS:
        try:
            kernel = kernel_contributions(fetch_lore(host))
            return {"updated": datetime.now(timezone.utc).date().isoformat(),
                    "kernel_archive": host, "github": github, "kernel": kernel}
        except (urllib.error.URLError, TimeoutError, ValueError, ET.ParseError) as error:
            failures.append(f"{host}: {error}")
            print(f"Archive unavailable: {host}: {error}", file=sys.stderr)
    raise RuntimeError("Kernel refresh failed: " + "; ".join(failures))


def render_entry(entry):
    url = urlsplit(entry["url"])
    if url.scheme != "https" or url.netloc not in ("github.com", "lore.kernel.org"):
        raise ValueError("Unexpected contribution URL")
    date = datetime.strptime(entry["date"], "%Y-%m-%d")
    project = "" if entry["project"] == "Linux kernel" else html.escape(entry["project"]) + " · "
    return (f'<li><a href="{html.escape(entry["url"], quote=True)}">'
            f'{html.escape(entry["title"])}</a><br />'
            f'<span class="contribution-meta">{project}{html.escape(entry["kind"])} · '
            f'<time datetime="{date:%Y-%m-%d}">{date:%d %b %Y}</time></span></li>')


def render(data):
    lines = ['    <section id="contributions" aria-labelledby="contributions-heading">',
             '      <h2 id="contributions-heading" class="section-heading">Open source</h2>']
    github = sorted(data["github"], key=lambda entry: entry["date"], reverse=True)
    kernel = sorted(data["kernel"], key=lambda entry: entry["date"], reverse=True)

    def entries_list(heading, entries):
        lines.extend([f"      <h3>{heading}</h3>", '      <ul class="contribution-list">'])
        lines.extend("        " + render_entry(entry) for entry in entries)
        lines.append("      </ul>")

    if github:
        entries_list("GitHub", github[:3])
    else:
        lines.append("      <p>No merged pull requests to other projects yet.</p>")
    if kernel:
        entries_list("Linux kernel / LKML", kernel[:1])
    else:
        lines.append("      <p>No mailing-list contributions found yet.</p>")
    remaining = len(github[3:]) + len(kernel[1:])
    if remaining:
        lines.extend(['      <details class="contributions-more">',
                      f'        <summary><span class="when-closed">Show all contributions ({len(github) + len(kernel)})</span>'
                      '<span class="when-open">Show fewer contributions</span></summary>'])
        if github[3:]:
            entries_list("More on GitHub", github[3:])
        if kernel[1:]:
            entries_list("More on LKML", kernel[1:])
        lines.append("      </details>")
    lines.append(f'      <p class="contribution-sources"><a href="{html.escape(GITHUB_SEARCH, quote=True)}">All merged PRs on GitHub ↗</a>'
                 f' · <a href="{html.escape(LORE_SEARCH, quote=True)}">All messages on lore ↗</a></p>')
    updated = datetime.strptime(data["updated"], "%Y-%m-%d")
    lines.extend([f'      <p class="contributions-updated">Updated {updated:%d %b %Y}</p>', "    </section>"])
    return "\n".join(lines)


def replace_section(page, section):
    if page.count(START) != 1 or page.count(END) != 1:
        raise ValueError("Expected exactly one contributions marker pair in index.html")
    before, remaining = page.split(START)
    _, after = remaining.split(END)
    return before + START + "\n" + section + "\n" + END + after


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Fetch both sources before rendering")
    args = parser.parse_args()
    snapshot = ROOT / "data/contributions.json"
    data = refresh() if args.refresh else json.loads(snapshot.read_text())
    index = ROOT / "index.html"
    page = replace_section(index.read_text(), render(data))
    # Fetch and validate everything before touching either file.
    if args.refresh:
        snapshot.parent.mkdir(exist_ok=True)
        snapshot.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    index.write_text(page)
    print(f"Rendered {len(data['github'])} GitHub PRs and {len(data['kernel'])} kernel topics")


if __name__ == "__main__":
    main()
