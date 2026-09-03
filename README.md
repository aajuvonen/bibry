# Bibry

Bibry is a lightweight web interface for browsing and editing flat BibTeX/BibLaTeX bibliographies. It is designed for personal research libraries where the data lives in normal `.bib` files plus an optional directory of PDFs.

The project stays deliberately simple: one local SQLite file, no accounts, and no heavy frontend framework. The default runtime is now Docker, so Bibry can run locally, on a home server, or behind a VPN without needing an interactive shell session.

## Features

* Browse a bibliography as cards or as a list
* Search and sort entries by year, author, or title
* Edit raw BibTeX directly in the browser
* Add, save, delete, copy, and undo entries
* Import `.bib` files from the toolbar or by drag and drop
* Preview import conflicts with entry-level diffs
* Run Crossref and WorldCat metadata scans from a shared review workflow
* Run a PDF coverage report with priority grouping and direct attach/suppress actions
* Scan the global PDF directory for orphan files and review safe rename/ignore actions
* Export selected entries to `export.bib` or to a ZIP with matching PDFs plus a static HTML index
* Keep bounded per-file history with restore support
* Share identical entries between bibliographies with one catalogue-wide citation key and PDF name
* Search the global library and add a shared entry to the active bibliography with a local citation key
* Switch between multiple `.bib` files in `bib/`
* Create and safely delete bibliography projections without deleting shared database entries
* Open the entire SQLite database, merge corrected duplicates, and remove unreferenced entries
* Load large bibliographies progressively in small pages
* Show DOI, URL, arXiv, and PDF links when available
* Work reasonably well on mobile as well as desktop

Entry selection supports Ctrl-click/Cmd-click for multi-selection. Cmd/Ctrl+K focuses the main search field. Outside text fields, Ctrl/Cmd+A selects all loaded entries, Ctrl/Cmd+C copies the selected BibLaTeX, Ctrl/Cmd+Z performs Undo, and Ctrl/Cmd+V opens the normal import review for clipboard BibLaTeX.

Import and export both pass the resulting bibliography through the sort/dedupe routine before writing or downloading it. Small toast notifications confirm actions such as save, add, import, export, undo, and restore.

## Scan Workflows

Bibry includes a `Scan` launcher in the toolbar. Scans never mutate the `.bib` file automatically. Every suggested change is reviewed first.

The Scan launcher includes **Database Orphans**, which lists database entries not referenced by any bibliography. Deletion requires selecting entries, confirming the count, and typing the requested confirmation text. It also includes **Catalogue Integrity**: a guided repair for legacy citation-key aliases plus a review-first same-work variant cleanup. For each candidate group, choose the one record to retain; Bibry moves bibliography memberships to it and deletes only the explicitly merged database variants. PDFs are never renamed or deleted by this cleanup.

### Crossref Scan

The Crossref scan resolves entries by DOI first, then title/author/year. It proposes BibLaTeX amendments for core citation fields, highlights field-level diffs, and can flag retracted or withdrawn records. Each suggestion can be:

* Accepted and applied directly
* Loaded into the editor for manual adjustment
* Rejected, optionally with suppression so it does not reappear unchanged

When a reviewed amendment changes the leading author or year in a way that matches the existing `AuthorYear` key pattern, Bibry also updates the citation key and carries a matching `pdf/<key>.pdf` file forward to the renamed key when possible.

### WorldCat Scan

The WorldCat scan targets book-like entries such as books, in-collection items, and theses. It prefers ISBN matching and falls back to title/author lookup, then proposes conservative updates for fields such as ISBN, publisher, year, title, and edition using the same Accept / Edit / Reject review flow.

### PDF Coverage Report

The PDF coverage scan is a local analysis pass. It checks for PDFs via `pdf/<citationKey>.pdf` and explicit `file` / `pdf` fields, then groups missing coverage into:

* High priority: articles, proceedings, theses, reports
* Medium priority: books and in-collection material
* Low priority: misc, online, software, datasets, multimedia, and similar items

The report includes counts per category plus actions to open the entry, attach a PDF, or mark `No PDF Expected` so intentionally non-PDF items stay out of future scans.

### Orphan PDF Report

The Orphan PDFs report compares every PDF filename in `pdf/` with citation keys from the global catalogue, including bibliographies other than the active one. It reports exact matches, normalized filename matches, ambiguous matches, and files with no candidate.

The scan is read-only. For a confident normalized match, Bibry can rename the file to `pdf/<citationKey>.pdf` after collision checks. Any result can also be ignored; ignores are tied to the file fingerprint and will reappear if the file changes. Ambiguous and unmatched files require manual review.

## Export Workflows

Bibry's export picker lets you choose between:

* `BibLaTeX Only`, which downloads `export.bib`
* `ZIP with PDFs`, which downloads `export.zip`

ZIP exports include:

* `export.bib`
* `index.html`
* `pdf/<citationKey>.pdf` for selected entries that have local PDFs

The HTML index can be rendered in either a list-style or card-style layout to mirror Bibry's main browsing views.

## Data Layout

Bibry stores a shared catalogue on disk and writes ordinary `.bib` projections:

```text
project/
├── app/
├── bib/
│   ├── main.bib
│   ├── another-library.bib
│   ├── library.sqlite3
│   ├── .active_bib
│   └── history/
│       ├── main.bib/
│       └── another-library.bib/
├── pdf/
│   ├── Turing1936.pdf
│   └── Planck1901.pdf
├── Dockerfile
└── docker-compose.yml
```

* `bib/library.sqlite3` is the canonical catalogue of entries and bibliography membership
* `.bib` files remain standalone, flat BibLaTeX projections suitable for LaTeX, version control, and backup
* Identical entry data may be shared by several bibliographies
* Every database entry has one catalogue-wide citation key; shared copies use it in every bibliography, while near-identical records receive distinct keys
* Imported citation keys are treated as source metadata: Bibry derives canonical `AuthorYear` keys from author/editor and year, adding suffixes for variants
* Editing an entry in one bibliography creates a local variant; use the share operation when the edited record should be reused elsewhere
* `bib/` contains the available bibliography files
* `bib/.active_bib` stores the currently selected bibliography filename
* `bib/history/<filename>/` stores recent revision history for each `.bib` file
* `pdf/` contains optional PDFs named after BibTeX keys

Only the three `.gitkeep` placeholders in the local data directories are intended for Git. Run `python3 scripts/check_repo_safety.py` before committing; it fails if personal BibTeX, PDF, SQLite, metadata, history, cache, or scan-job data is tracked or unignored.

### Catalogue migration and backup

On first startup after upgrading, Bibry imports every existing `bib/*.bib` file into `bib/library.sqlite3`. Exact entry data is reused automatically; records with different fields are retained as separate variants. Existing `.bib` files are not removed. The migration is idempotent.

Back up both the SQLite catalogue and the `.bib` projections. The projections are useful for recovery and external LaTeX use; restoring an older projection alone does not change the catalogue after migration.

If `pdf/<key>.pdf` exists, Bibry shows a PDF link for that entry automatically.

## Running with Docker

Docker Compose is the default way to run Bibry.

Start the application:

```bash
docker compose up --build
```

Or use the convenience wrapper:

```bash
./run.sh
```

Then open:

```text
http://localhost:5000
```

The Compose setup:

* builds the image from the local `Dockerfile`
* runs Gunicorn inside the container
* exposes Bibry on port `5000`
* bind-mounts `bib/` and `pdf/` so your data stays on the host

To run in the background:

```bash
docker compose up --build -d
```

To stop it:

```bash
docker compose down
```

## Updating a deployment

The recommended update procedure is:

```bash
cd /path/to/bibry
tar -czf bibry-data-backup.tgz bib pdf
docker compose down
git pull --ff-only origin main
docker compose up --build -d
```

Use `--ff-only` so the deployment host never creates an accidental merge commit. If the pull refuses because of local tracked changes, inspect them rather than forcing the update. Bibry data under `bib/` and `pdf/` is intentionally ignored by Git.

To rebuild after code changes:

```bash
docker compose up --build
```

## Moving data to a fresh server

Clone the repository on the server, stop any existing Bibry container, and copy the deployment data before starting Compose. Copy `bib/*.bib`, `bib/library.sqlite3`, `bib/.active_bib`, `bib/metadata/`, `bib/history/` if history is wanted, and the complete `pdf/` directory. Do not copy `.venv`, `.DS_Store`, HTTP caches, or scan-job files.

For example:

```bash
rsync -a --delete --exclude='.DS_Store' --exclude='scan_jobs/' --exclude='cache/' \
  ./bib/ user@server:/srv/bibry/bib/
rsync -a --delete --exclude='.DS_Store' ./pdf/ user@server:/srv/bibry/pdf/
ssh user@server 'cd /srv/bibry && docker compose up --build -d'
```

Keep the original data until the server has been checked for entry counts, active bibliography, shared/variant entries, PDF links, history, and orphan scan results. The SQLite catalogue is canonical after migration; transfer it together with the projections rather than relying on a fresh migration.

## Manual Python Run

Docker is the default and recommended path, but you can still run Bibry directly with Python if needed.

Install dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Run with Gunicorn:

```bash
gunicorn wsgi:app \
  --workers 4 \
  --threads 4 \
  --timeout 120 \
  --bind 0.0.0.0:5000
```

Or run the Flask entrypoint directly:

```bash
python wsgi.py
```

## Deployment Notes

For a home server, the simplest setup is usually:

* run Bibry with Docker Compose
* expose it only on your LAN or VPN
* optionally place a reverse proxy in front if you want a nicer hostname or TLS

Bibry is intended for personal use, so exposing it directly to the public internet is not recommended.

## Philosophy

Bibry intentionally avoids complex infrastructure. The goal is to provide a fast and practical interface for working with BibTeX libraries while keeping the whole system transparent and easy to modify.

Your bibliography remains a normal set of `.bib` files that can be edited, version-controlled, backed up, or used with LaTeX as usual.
