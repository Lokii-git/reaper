#!/usr/bin/env python3
"""
Reaper - OSINT File & Email Harvester
Runs site:domain filetype:ext searches via DDG API, downloads results, and extracts metadata.
Harvests email addresses via targeted DDG queries and snippet parsing.

Inspired by theHarvester (https://github.com/laramies/theHarvester)
Author: Philip Burnham @Lokii-git

Usage: python3 reaper.py -d domain.com
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from ddgs import DDGS
except ImportError:
    print("[!] ddgs not installed. Run: pip install ddgs")
    sys.exit(1)


VERSION = "1.2.0"
DEFAULT_FILETYPES = ["pdf", "xls", "xlsx", "csv", "doc", "docx", "ppt", "pptx"]
EXIFTOOL = "exiftool"
REQUEST_DELAY = 1.5  # seconds between DDG queries to avoid rate limiting

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Email queries ordered HIGH -> MEDIUM -> LOW (inspired by CorpINT)
# Each query pulls a different slice of results from the index
EMAIL_QUERIES = [
    # HIGH — direct on-domain
    'site:{domain} "@{domain}"',
    'site:{domain} intitle:"contact"',
    'site:{domain} intitle:"team" OR intitle:"staff" OR intitle:"about"',
    # MEDIUM — third-party mentions
    '"@{domain}"',
    '"{domain}" email OR contact OR mail',
    'intext:"@{domain}"',
    '"@{domain}" -site:{domain}',
    # MEDIUM — social/professional platforms
    'site:linkedin.com "{domain}"',
    'site:github.com "{domain}"',
    'site:twitter.com "{domain}"',
    # LOW — job boards & leaked data
    'site:pastebin.com "@{domain}"',
    'site:indeed.com "{domain}" OR site:glassdoor.com "{domain}"',
]

# File query variants per type — each hits a different part of the index
FILE_QUERY_TEMPLATES = [
    'site:{domain} filetype:{ext}',
    'site:*.{domain} filetype:{ext}',       # subdomains
    '"{domain}" filetype:{ext}',             # files hosted elsewhere referencing domain
]


W = 70  # output width


def banner():
    print("=" * W)
    print(f" REAPER v{VERSION}  |  OSINT File & Email Harvester")
    print(" Inspired by theHarvester  (github.com/laramies/theHarvester)")
    print("=" * W)


def section(title: str):
    print(f"\n{'=' * W}")
    print(f"  {title.upper()}")
    print("=" * W)


def info(label: str, value: str, indent: int = 0):
    pad = " " * indent
    print(f"{pad}  {label:<22} {value}")


def result_line(url: str, indent: int = 4):
    pad = " " * indent
    print(f"{pad}{url}")


def divider():
    print("-" * W)


def _extract_emails_from_results(results: list[dict], domain: str) -> set[str]:
    """Parse email addresses from DDG/Bing result dicts."""
    found = set()
    for r in results:
        for field in (r.get("href", ""), r.get("body", ""), r.get("title", "")):
            for email in EMAIL_RE.findall(field):
                if email.lower().endswith(f"@{domain}"):
                    found.add(email.lower())
    return found


def search_ddg(query: str, max_results: int) -> list[dict]:
    """Run a single DDG text search and return result dicts."""
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                if r.get("href"):
                    results.append(r)
    except Exception as e:
        print(f"    [!] DDG error: {e}")
    return results


def search_bing(query: str, api_key: str, max_results: int = 50) -> list[dict]:
    """Search Bing via API (requires Ocp-Apim-Subscription-Key)."""
    endpoint = "https://api.bing.microsoft.com/v7.0/search"
    results = []
    offset = 0
    batch = min(50, max_results)

    while offset < max_results:
        params = urllib.parse.urlencode({"q": query, "count": batch, "offset": offset})
        req = urllib.request.Request(
            f"{endpoint}?{params}",
            headers={"Ocp-Apim-Subscription-Key": api_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            pages = data.get("webPages", {}).get("value", [])
            if not pages:
                break
            for p in pages:
                results.append({
                    "href": p.get("url", ""),
                    "title": p.get("name", ""),
                    "body": p.get("snippet", ""),
                })
            offset += len(pages)
            if len(pages) < batch:
                break
        except Exception as e:
            print(f"    [!] Bing API error: {e}")
            break

    return results


def _run_query(query: str, max_results: int, bing_key: str | None) -> list[dict]:
    """Run query against DDG and optionally Bing, merge and deduplicate results."""
    results = search_ddg(query, max_results)
    seen_hrefs = {r["href"] for r in results}

    if bing_key:
        for r in search_bing(query, bing_key, max_results):
            if r["href"] not in seen_hrefs:
                results.append(r)
                seen_hrefs.add(r["href"])

    return results


def search_emails(domain: str, max_results: int = 100, bing_key: str | None = None) -> set[str]:
    """Harvest emails using all EMAIL_QUERIES against DDG (+ Bing if key provided)."""
    found: set[str] = set()

    for template in EMAIL_QUERIES:
        query = template.format(domain=domain)
        print(f"  [*] {query}")
        results = _run_query(query, max_results, bing_key)
        found |= _extract_emails_from_results(results, domain)
        time.sleep(REQUEST_DELAY)

    return found


def search_files(domain: str, file_type: str, max_results: int = 50, bing_key: str | None = None) -> list[dict]:
    """Search for files using multiple query variants against DDG (+ Bing if key provided)."""
    all_results: list[dict] = []
    seen_hrefs: set[str] = set()

    for template in FILE_QUERY_TEMPLATES:
        query = template.format(domain=domain, ext=file_type)
        results = _run_query(query, max_results, bing_key)
        for r in results:
            if r["href"] not in seen_hrefs:
                all_results.append(r)
                seen_hrefs.add(r["href"])
        time.sleep(REQUEST_DELAY)

    return all_results


def filter_file_urls(results: list[dict], file_type: str) -> list[str]:
    """Filter results to only URLs that actually end in the target extension."""
    urls = []
    ext = f".{file_type.lower()}"
    for r in results:
        href = r.get("href", "").lower()
        # Accept URLs ending in the extension or with it before a query string
        parsed = urllib.parse.urlparse(href)
        if parsed.path.endswith(ext):
            urls.append(r["href"])
        elif ext in parsed.path:
            urls.append(r["href"])
    return list(dict.fromkeys(urls))  # deduplicate preserving order


def download_file(url: str, dest_dir: Path) -> Path | None:
    """Download a file to dest_dir. Returns local path or None on failure."""
    # Unquote FIRST, then reduce to a bare basename. Doing it in this order
    # neutralizes percent-encoded traversal (e.g. %2e%2e%2f -> ../) and
    # absolute-path tricks (%2fetc%2fpasswd -> /etc/passwd) in hostile URLs.
    raw = urllib.parse.unquote(url.split("?")[0])
    filename = os.path.basename(raw).replace("\x00", "").lstrip(".") or "unknown"
    dest = dest_dir / filename

    # Avoid re-downloading
    if dest.exists():
        return dest

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            dest.write_bytes(resp.read())
        return dest
    except Exception as e:
        print(f"    [!] Download failed: {e}")
        return None


def extract_metadata(file_path: Path) -> dict:
    """Run exiftool on a file and return key metadata fields."""
    if not file_path.exists():
        return {}
    try:
        out = subprocess.check_output(
            [EXIFTOOL, "-Author", "-Creator", "-Producer", "-Company",
             "-LastModifiedBy", "-Subject", "-Title", "-CreateDate",
             str(file_path)],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        meta = {}
        for line in out.strip().splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                val = val.strip()
                if val and val not in ("-", ""):
                    meta[key.strip()] = val
        return meta
    except Exception:
        return {}


def save_emails(domain: str, emails: set[str]) -> Path:
    """Write sorted emails to <domain>_emails.txt and return the path."""
    path = Path(f"{domain}_emails.txt")
    path.write_text("\n".join(sorted(emails)) + "\n")
    return path


def discover_files(args, domain: str, out_dir: Path):
    """Search, download, and extract metadata for each filetype.

    Returns (all_urls, metadata_results, downloaded_count). A Ctrl-C stops
    discovery early but preserves whatever was gathered so the caller can
    still write out partial results.
    """
    all_urls: list[tuple[str, str]] = []  # (filetype, url)
    metadata_results: list[dict] = []
    downloaded_count = 0

    try:
        for ft in args.filetypes:
            print(f"\n  [ {ft.upper()} ]")
            results = search_files(domain, ft, max_results=args.max, bing_key=args.bing_key)
            urls = filter_file_urls(results, ft)

            if not urls:
                # Fallback: domain-matching hrefs without extension filter
                already_seen = {u for _, u in all_urls}
                fallback = [
                    r["href"] for r in results
                    if domain in r.get("href", "") and r["href"] not in already_seen
                ]
                if fallback:
                    print("  Possible matches (extension not confirmed):")
                    for href in fallback:
                        result_line(href)
                else:
                    print("  No results.")
                continue

            print(f"  Found {len(urls)} file(s):")
            divider()
            for url in urls:
                result_line(url)
                all_urls.append((ft, url))

                if not args.no_download:
                    ft_dir = out_dir / ft
                    ft_dir.mkdir(exist_ok=True)
                    local = download_file(url, ft_dir)
                    if local:
                        downloaded_count += 1
                        print(f"      Downloaded : {local.name}")
                        if not args.no_meta:
                            meta = extract_metadata(local)
                            if meta:
                                for k, v in meta.items():
                                    print(f"      {k:<20} {v}")
                                metadata_results.append({"file": str(local), "url": url, **meta})
                    else:
                        print("      Download failed.")
    except KeyboardInterrupt:
        print("\n  [!] Interrupted -- writing partial results collected so far.")

    return all_urls, metadata_results, downloaded_count


def main():
    parser = argparse.ArgumentParser(
        description="Reaper - OSINT File & Email Harvester | Inspired by theHarvester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 reaper.py -d company.com --all
  python3 reaper.py -d target.org -f pdf docx
  python3 reaper.py -d example.com --no-download
        """,
    )
    parser.add_argument("--version", action="version", version=f"Reaper v{VERSION}")
    parser.add_argument("-d", "--domain", required=True, help="Target domain")
    parser.add_argument(
        "-f", "--filetypes", nargs="+", default=DEFAULT_FILETYPES,
        help=f"File types to search (default: {' '.join(DEFAULT_FILETYPES)})",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="Output directory for downloaded files (default: <domain>_files/)",
    )
    parser.add_argument(
        "--max", type=int, default=None,
        help="Max results per query (default: 50, or 100 with --all)",
    )
    parser.add_argument(
        "--no-download", action="store_true",
        help="Only print URLs, do not download files",
    )
    parser.add_argument(
        "--no-meta", action="store_true",
        help="Skip exiftool metadata extraction",
    )
    parser.add_argument(
        "--emails-only", action="store_true",
        help="Only run email harvesting, skip file searches",
    )
    parser.add_argument(
        "--no-emails", action="store_true",
        help="Skip email harvesting",
    )
    parser.add_argument(
        "--bing-key", default=None, metavar="KEY",
        help="Bing Search API key (optional -- adds Bing results on top of DDG)",
    )
    parser.add_argument(
        "--all", dest="deep", action="store_true",
        help="Deep dive: all file types, all queries, download everything, full metadata",
    )
    args = parser.parse_args()

    # --all overrides individual flags
    if args.deep:
        args.filetypes = DEFAULT_FILETYPES
        args.no_download = False
        args.no_meta = False
        args.no_emails = False
        args.emails_only = False

    if args.max is None:
        args.max = 100 if args.deep else 50

    # A missing exiftool would otherwise look identical to "file has no
    # metadata" (extract_metadata swallows the error). Warn once and disable.
    if not args.no_meta and shutil.which(EXIFTOOL) is None:
        print(f"[!] {EXIFTOOL} not found on PATH -- metadata extraction disabled.")
        args.no_meta = True

    domain = args.domain.lower().strip()
    for prefix in ("https://", "http://", "www."):
        domain = domain.removeprefix(prefix)

    out_dir = Path(args.output) if args.output else Path(f"{domain}_files")
    if not args.no_download:
        out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    engines = "DDG" + (" + Bing" if args.bing_key else "")
    mode = "DEEP DIVE (--all)" if args.deep else "standard"
    banner()
    info("Target:", domain)
    info("Engines:", engines)
    info("Mode:", mode)
    info("Max results/query:", str(args.max))
    if not args.emails_only:
        info("File types:", ", ".join(args.filetypes))
    info("Email reaping:", "yes" if not args.no_emails else "disabled")
    info("Download files:", "yes" if not args.no_download else "disabled")
    info("Metadata extraction:", "yes" if not args.no_meta else "disabled")
    info("Output directory:", str(out_dir) if not args.no_download else "disabled")
    print("=" * W)

    emails_found: set[str] = set()

    # ------------------------------------------------------------------
    # EMAIL HARVESTING
    # ------------------------------------------------------------------
    if not args.no_emails:
        section(f"Email Reaping  [{len(EMAIL_QUERIES)} queries]")
        emails_found = search_emails(domain, max_results=args.max, bing_key=args.bing_key)
        if emails_found:
            print(f"  Found {len(emails_found)} address(es):")
            divider()
            for e in sorted(emails_found):
                result_line(e)
        else:
            print("  No email addresses found.")

    if args.emails_only:
        section("Summary")
        info("Emails found:", str(len(emails_found)))
        if emails_found:
            info("Saved to:", str(save_emails(domain, emails_found)))
        print("=" * W)
        return

    # ------------------------------------------------------------------
    # FILE DISCOVERY
    # ------------------------------------------------------------------
    section(f"File Discovery  [{len(FILE_QUERY_TEMPLATES)} query variants per type]")

    all_urls, metadata_results, downloaded_count = discover_files(args, domain, out_dir)

    # ------------------------------------------------------------------
    # SUMMARY
    # ------------------------------------------------------------------
    section("Summary")
    info("Target:", domain)
    info("Engines:", engines)
    divider()
    info("Emails found:", str(len(emails_found)))
    info("File URLs found:", str(len(all_urls)))
    if not args.no_download:
        info("Files downloaded:", str(downloaded_count))
    if not args.no_meta and metadata_results:
        divider()
        print("  Metadata Highlights:")
        seen: set[str] = set()
        for m in metadata_results:
            for k in ("Author", "Creator", "Company", "LastModifiedBy"):
                v = m.get(k)
                if v and v not in seen:
                    seen.add(v)
                    info(f"  {k}:", v, indent=2)

    # Save results
    divider()
    url_list_path = Path(f"{domain}_reaper_results.txt")
    with open(url_list_path, "w") as f:
        f.write(f"Reaper Results  |  {domain}\n")
        f.write("=" * 50 + "\n\n")
        if emails_found:
            f.write("EMAILS\n" + "-" * 30 + "\n")
            for e in sorted(emails_found):
                f.write(f"{e}\n")
            f.write("\n")
        if all_urls:
            f.write("FILES\n" + "-" * 30 + "\n")
            for ft, url in all_urls:
                f.write(f"{ft.upper():<8} {url}\n")

    info("Results saved to:", str(url_list_path))
    if emails_found:
        info("Emails saved to:", str(save_emails(domain, emails_found)))
    print("=" * W)


if __name__ == "__main__":
    main()
