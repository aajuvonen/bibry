import os
import time
import re
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
import bibtexparser

app = Flask(__name__, static_folder="static")

ROOT = Path(__file__).parent
BIBFILE = ROOT / "main.bib"
PDF_DIR = ROOT / "pdf"

PDF_DIR.mkdir(exist_ok=True)

entries_cache = []
entries_map = {}
pdf_files = set()
last_bib_mtime = 0


def load_bib():
    global entries_cache, entries_map, last_bib_mtime

    if not BIBFILE.exists():
        entries_cache = []
        entries_map = {}
        return

    mtime = BIBFILE.stat().st_mtime
    if mtime == last_bib_mtime:
        return

    last_bib_mtime = mtime

    with open(BIBFILE) as f:
        db = bibtexparser.load(f)

    entries_cache = db.entries
    entries_map = {e["ID"]: e for e in entries_cache}


def scan_pdfs():
    global pdf_files
    pdf_files = {p.stem for p in PDF_DIR.glob("*.pdf")}


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/pdf/<path:name>")
def pdf(name):
    return send_from_directory(PDF_DIR, name)


@app.route("/api/entries")
def api_entries():
    load_bib()
    scan_pdfs()

    out = []

    for e in entries_cache:

        key = e.get("ID")

        out.append({
            "key": key,
            "title": e.get("title"),
            "author": e.get("author"),
            "year": e.get("year"),
            "publisher": e.get("publisher"),
            "url": e.get("url"),
            "doi": e.get("doi"),
            "type": e.get("ENTRYTYPE"),
            "has_pdf": key in pdf_files
        })

    return jsonify(out)


@app.route("/api/entry/<key>")
def api_entry(key):

    load_bib()

    e = entries_map.get(key)

    if not e:
        return jsonify({"error": "not found"}), 404

    db = bibtexparser.bibdatabase.BibDatabase()
    db.entries = [e]

    writer = bibtexparser.bwriter.BibTexWriter()

    return jsonify({
        "raw": writer.write(db)
    })


@app.route("/api/sanitize")
def api_sanitize():

    load_bib()

    missing_year = []
    duplicates = []
    seen_doi = {}

    for e in entries_cache:

        key = e.get("ID")

        if not e.get("year"):
            missing_year.append(key)

        doi = e.get("doi")

        if doi:
            if doi in seen_doi:
                duplicates.append(key)
            seen_doi[doi] = key

    scan_pdfs()

    keys = {e["ID"] for e in entries_cache}

    orphan = []

    for p in pdf_files:
        if p not in keys:
            orphan.append(p + ".pdf")

    return jsonify({
        "missing_year": missing_year,
        "duplicates": duplicates,
        "orphan_pdfs": orphan
    })


if __name__ == "__main__":
    app.run()