# Reaper

**OSINT File & Email Harvester**

Reaper runs targeted search engine queries against a domain to:

- Discover publicly indexed files (PDF, XLSX, DOCX, PPT, CSV, etc.) and extract metadata (authors, company names, software versions) via `exiftool`
- Harvest email addresses from DDG/Bing snippets, contact pages, LinkedIn, GitHub, Pastebin, and job boards

Inspired by [theHarvester](https://github.com/laramies/theHarvester) and [Pymeta](https://github.com/m8r0wn/pymeta).

---

## Features

- Multi-engine support: **DuckDuckGo** (default) + optional **Bing Search API**
- Email harvesting across 12 query templates (on-domain, third-party mentions, social, job boards)
- File discovery with 3 query variants per type to maximise index coverage (direct, subdomain, off-domain references)
- Auto-download discovered files with configurable output directory
- Exiftool metadata extraction — surfaces usernames, authors, company names, and software
- Rate-limited requests to avoid DDG throttling
- `--all` deep-dive mode

---

## Requirements

```
pip install ddgs
```

[exiftool](https://exiftool.org/) must be on PATH for metadata extraction (optional — Reaper will warn and skip if missing).

---

## Usage

```
python3 reaper.py -d <domain> [options]
```

### Options

| Flag | Description |
|------|-------------|
| `-d`, `--domain` | Target domain *(required)* |
| `-f`, `--filetypes` | Space-separated list of file types to search (default: `pdf xls xlsx csv doc docx ppt pptx`) |
| `-o`, `--output` | Output directory for downloads (default: `<domain>_files/`) |
| `--max N` | Max results per query (default: 50, or 100 with `--all`) |
| `--no-download` | Print URLs only, skip downloading |
| `--no-meta` | Skip exiftool metadata extraction |
| `--emails-only` | Run email harvesting only, skip file searches |
| `--no-emails` | Skip email harvesting |
| `--bing-key KEY` | Bing Search API key — merges Bing results on top of DDG |
| `--all` | Deep dive: all types, all queries, download everything, full metadata |
| `--version` | Show version |

---

## Examples

```bash
# Standard run — emails + all default file types
python3 reaper.py -d company.com

# Deep dive — maximise coverage, download everything
python3 reaper.py -d target.org --all

# Email harvesting only
python3 reaper.py -d example.com --emails-only

# Custom file types, no downloads
python3 reaper.py -d target.com -f pdf docx --no-download

# Add Bing on top of DDG
python3 reaper.py -d target.com --all --bing-key YOUR_KEY_HERE
```

---

## Output

| File | Contents |
|------|----------|
| `<domain>_reaper_results.txt` | All discovered emails and file URLs |
| `<domain>_emails.txt` | Email addresses only (sorted) |
| `<domain>_files/<type>/` | Downloaded files, organized by type |

---

## Disclaimer

This tool is intended for **authorized security assessments and OSINT research only**. Only run against domains you own or have explicit written permission to test. The author is not responsible for misuse.

---

## Author

Philip Burnham [@Lokii-git](https://github.com/Lokii-git)
