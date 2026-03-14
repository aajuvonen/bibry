# app/api.py
from flask import Blueprint, jsonify, request, abort, send_from_directory
from pathlib import Path
import requests
import subprocess
import os
import bibtexparser

from .bibstore import load_bib, save_bib, LAST_BIB_STATE, BIB_VERSION, BIBFILE, ROOT
from .latex import latex_to_text

api_bp = Blueprint('api', __name__)

# Directory for PDFs
PDF_DIR = ROOT / "pdf"

# Endpoint: current version for live refresh
@api_bp.route("/version")
def api_version():
    return jsonify({"version": BIB_VERSION})

# Endpoint: list all entries (with metadata)
@api_bp.route("/entries")
def api_entries():
    db = load_bib()
    pdf_files = {p.stem for p in PDF_DIR.glob("*.pdf")}
    out = []
    writer = bibtexparser.bwriter.BibTexWriter()
    for e in db.entries:
        db2 = bibtexparser.bibdatabase.BibDatabase()
        db2.entries = [e]
        raw = writer.write(db2)
        fields = {k: latex_to_text(v) if isinstance(v,str) else v
                  for k,v in e.items()}

        out.append({
            "key": e.get("ID"),
            "type": e.get("ENTRYTYPE"),
            "fields": fields,
            "raw": raw,
            "has_pdf": e.get("ID") in pdf_files
        })
    return jsonify(out)

# Endpoint: get raw BibTeX for one entry
@api_bp.route("/entry/<key>")
def api_entry(key):
    db = load_bib()
    entry = next((e for e in db.entries if e.get("ID") == key), None)
    if not entry:
        return jsonify({"error": "Not found"}), 404
    # Return the raw BibTeX string
    db2 = bibtexparser.bibdatabase.BibDatabase()
    db2.entries = [entry]
    writer = bibtexparser.bwriter.BibTexWriter()
    return jsonify({"raw": writer.write(db2)})

# Endpoint: sanitize / check data consistency
@api_bp.route("/sanitize")
def api_sanitize():
    db = load_bib()
    missing_year = []
    duplicates = []
    seen_doi = {}
    for e in db.entries:
        key = e.get("ID")
        if not e.get("year"):
            missing_year.append(key)
        doi = e.get("doi")
        if doi:
            if doi in seen_doi:
                duplicates.append(key)
            seen_doi[doi] = key
    pdf_names = {p.stem for p in PDF_DIR.glob("*.pdf")}
    keys = {e.get("ID") for e in db.entries}
    orphan = [f for f in pdf_names if f not in keys]
    return jsonify({
        "missing_year": missing_year,
        "duplicates": duplicates,
        "orphan_pdfs": [name + ".pdf" for name in orphan]
    })

# Endpoint: Undo last change
@api_bp.route("/undo", methods=["POST"])
def api_undo():
    global LAST_BIB_STATE, BIB_VERSION
    if LAST_BIB_STATE is None:
        abort(400, "Nothing to undo")
    # Swap current and last states
    current = BIBFILE.read_text(encoding="utf-8")
    BIBFILE.write_text(LAST_BIB_STATE, encoding="utf-8")
    LAST_BIB_STATE = current
    BIB_VERSION += 1
    return jsonify({"ok": True})

# Serve PDF files
def serve_pdf(filename):
    return send_from_directory(PDF_DIR, filename)

# Edit endpoint
@api_bp.route("/entry/<key>", methods=["POST"])
def api_edit(key):

    raw = request.json.get("raw","")

    db = load_bib()

    if raw.strip()=="":
        db.entries = [e for e in db.entries if e.get("ID")!=key]
        save_bib(db)
        return jsonify({"deleted":True})

    parser = bibtexparser.bparser.BibTexParser(common_strings=True)
    newdb = bibtexparser.loads(raw, parser=parser)

    if len(newdb.entries)!=1:
        abort(400,"Invalid entry")

    new_entry = newdb.entries[0]

    for i,e in enumerate(db.entries):
        if e.get("ID")==key:
            db.entries[i]=new_entry
            save_bib(db)
            return jsonify({"ok":True})

    abort(404)