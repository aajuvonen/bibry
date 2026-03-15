# Bibry

Bibry is a lightweight web interface for browsing and editing a flat BibTeX/BibLaTeX bibliography. It is designed for personal research libraries where the data lives in normal `.bib` files plus an optional directory of PDFs.

The project stays deliberately simple: no database, no accounts, no heavy frontend framework. The default runtime is now Docker, so Bibry can run locally, on a home server, or behind a VPN without needing an interactive shell session.

## Features

* Browse a bibliography as cards or as a list
* Search and sort entries by year, author, or title
* Edit raw BibTeX directly in the browser
* Add, save, delete, copy, and undo entries
* Import `.bib` files from the toolbar or by drag and drop
* Preview import conflicts with entry-level diffs
* Export selected entries to `export.bib`
* Keep bounded per-file history with restore support
* Switch between multiple `.bib` files in `bib/`
* Show DOI, URL, arXiv, and PDF links when available
* Work reasonably well on mobile as well as desktop

Import and export both pass the resulting bibliography through the sort/dedupe routine before writing or downloading it. Small toast notifications confirm actions such as save, add, import, export, undo, and restore.

## Data Layout

Bibry stores bibliography data directly on disk:

```text
project/
├── app/
├── bib/
│   ├── main.bib
│   ├── another-library.bib
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

* `bib/` contains the available bibliography files
* `bib/.active_bib` stores the currently selected bibliography filename
* `bib/history/<filename>/` stores recent revision history for each `.bib` file
* `pdf/` contains optional PDFs named after BibTeX keys

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

To rebuild after code changes:

```bash
docker compose up --build
```

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
