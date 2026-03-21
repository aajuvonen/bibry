# app/api.py
import html
import io
import re
import zipfile
from pathlib import Path

from flask import Blueprint, jsonify, request, abort, send_from_directory, Response
import bibtexparser

from . import bibstore
from .enrichment import build_entries_by_key, get_scan_service, list_scan_services
from .latex import latex_to_text
from .metadata_store import clear_suppressions, get_entry_metadata, rename_entry_metadata, set_entry_flag, set_entry_provenance, set_suppression
from .scan_jobs import cancel_scan_job, get_scan_job, start_scan_job
from .sort_dedupe_bibtex import BibEntry, process_bibtex_text, split_entries

api_bp = Blueprint('api', __name__)

# Directory for PDFs
PDF_DIR = bibstore.ROOT / "pdf"
_ENTRY_CACHE_SIGNATURE = object()
_ENTRY_CACHE_BASE = None
_RAW_DISPLAY_FIELDS = {"url", "doi", "eprint", "file", "pdf"}


def _build_entry_cache():
    global _ENTRY_CACHE_SIGNATURE, _ENTRY_CACHE_BASE
    signature = bibstore.get_bib_signature()
    if _ENTRY_CACHE_BASE is not None and signature == _ENTRY_CACHE_SIGNATURE:
        return _ENTRY_CACHE_BASE

    db = bibstore.load_bib()
    writer = bibtexparser.bwriter.BibTexWriter()
    cached = []

    for e in db.entries:
        db2 = bibtexparser.bibdatabase.BibDatabase()
        db2.entries = [e]
        raw = writer.write(db2)
        fields = {
            k: (v if k.lower() in _RAW_DISPLAY_FIELDS else latex_to_text(v)) if isinstance(v, str) else v
            for k, v in e.items()
        }
        cached.append({
            "key": e.get("ID"),
            "type": e.get("ENTRYTYPE"),
            "fields": fields,
            "raw": raw,
        })

    _ENTRY_CACHE_SIGNATURE = signature
    _ENTRY_CACHE_BASE = cached
    return _ENTRY_CACHE_BASE


def warm_entries_cache():
    _build_entry_cache()


def _entry_statuses(key):
    metadata = get_entry_metadata(key)
    flags = metadata.get("flags", {}) if isinstance(metadata, dict) else {}
    statuses = []
    for name in ("retracted", "withdrawn"):
        payload = flags.get(name)
        if isinstance(payload, dict) and payload.get("active"):
            statuses.append({
                "name": name,
                "label": payload.get("label") or name.title(),
                "source": payload.get("source") or "",
                "updated_at": payload.get("updated_at") or "",
            })
    return statuses, metadata


def _entry_preview(raw):
    entry = BibEntry(raw)
    fields = entry.fields or {}
    title = latex_to_text(fields.get("title", ""))
    author = latex_to_text(fields.get("author", fields.get("editor", "")))
    year = latex_to_text(fields.get("year", ""))
    return {
        "key": entry.key,
        "type": entry.type,
        "title": title,
        "author": author,
        "year": year,
        "raw": raw.strip(),
    }


def _entry_signature(entry):
    return {
        "type": (entry.get("ENTRYTYPE") or "").lower(),
        "fields": {
            key: value.strip()
            for key, value in sorted(entry.items())
            if key not in {"ID", "ENTRYTYPE"} and isinstance(value, str)
        },
    }


def _entry_conflict(existing_entry, incoming_entry):
    changed_fields = []
    field_names = sorted(
        {
            key for key in existing_entry.keys() | incoming_entry.keys()
            if key not in {"ID", "ENTRYTYPE"}
        }
    )
    for field in field_names:
        before = latex_to_text(existing_entry.get(field, "")) if isinstance(existing_entry.get(field, ""), str) else existing_entry.get(field, "")
        after = latex_to_text(incoming_entry.get(field, "")) if isinstance(incoming_entry.get(field, ""), str) else incoming_entry.get(field, "")
        if before != after:
            changed_fields.append({
                "field": field,
                "before": before or "",
                "after": after or "",
            })

    return {
        "changed_fields": changed_fields,
        "existing": {
            "title": latex_to_text(existing_entry.get("title", "")),
            "author": latex_to_text(existing_entry.get("author", existing_entry.get("editor", ""))),
            "year": latex_to_text(existing_entry.get("year", "")),
            "type": existing_entry.get("ENTRYTYPE", ""),
        },
        "incoming": {
            "title": latex_to_text(incoming_entry.get("title", "")),
            "author": latex_to_text(incoming_entry.get("author", incoming_entry.get("editor", ""))),
            "year": latex_to_text(incoming_entry.get("year", "")),
            "type": incoming_entry.get("ENTRYTYPE", ""),
        },
    }


def _render_export_html(entries, view_mode="list"):
    def clean(value):
        return html.escape(value or "")

    def key_label(entry):
        return clean(entry.get("key", "") or "")

    def title_label(fields):
        return clean(fields.get("title", "") or "(No title)")

    def author_label(fields):
        raw = fields.get("author", fields.get("editor", "")) or ""
        parts = [part.strip() for part in re.split(r"\s+and\s+", raw) if part.strip()]
        if len(parts) > 1:
            raw = ", ".join(parts[:-1]) + ", & " + parts[-1]
        return clean(raw)

    def source_label(fields):
        journal = fields.get("journal", "")
        if journal:
            pieces = [f"<i>{clean(journal)}</i>"]
            if fields.get("volume"):
                pieces.append(clean(fields.get("volume", "")))
            if fields.get("number"):
                pieces[-1] = f"{pieces[-1]}({clean(fields.get('number', ''))})"
            if fields.get("pages"):
                pieces.append(f"pp. {clean(fields.get('pages', ''))}")
            return ", ".join(piece for piece in pieces if piece)
        booktitle = fields.get("booktitle", "")
        if booktitle:
            pieces = [f"<i>{clean(booktitle)}</i>"]
            if fields.get("publisher"):
                pieces.append(clean(fields.get("publisher", "")))
            if fields.get("pages"):
                pieces.append(f"pp. {clean(fields.get('pages', ''))}")
            return ", ".join(piece for piece in pieces if piece)
        if fields.get("publisher"):
            return clean(fields.get("publisher", ""))
        return ""

    def action_links(entry, fields):
        links = []
        key = entry.get("key", "") or ""
        if key and (PDF_DIR / f"{key}.pdf").exists():
            links.append({
                "href": f"pdf/{clean(key)}.pdf",
                "label": "PDF",
                "icon": "fa-file-pdf-o",
                "class_name": "pdf",
            })
        if fields.get("url"):
            links.append({
                "href": clean(fields.get("url", "")),
                "label": "URL",
                "icon": "fa-link",
                "class_name": "url",
            })
        if fields.get("archiveprefix", "").lower() == "arxiv" and fields.get("eprint"):
            links.append({
                "href": f"https://arxiv.org/abs/{clean(fields.get('eprint', ''))}",
                "label": "arXiv",
                "icon": "fa-external-link",
                "class_name": "arxiv",
            })
        if fields.get("doi"):
            links.append({
                "href": f"https://doi.org/{clean(fields.get('doi', ''))}",
                "label": "DOI",
                "icon": "fa-bookmark",
                "class_name": "doi",
            })
        return links

    def card_actions(action_items):
        return "".join(
            f'<a href="{item["href"]}" class="btn btn-sm export-card-link {item["class_name"]}">{item["label"]}</a>'
            for item in action_items
        )

    def list_actions(action_items):
        if not action_items:
            return ""
        return '<span class="export-list-actions ms-2 text-muted">' + "".join(
            f'<a href="{item["href"]}" title="{item["label"]}" class="ms-1 text-decoration-none export-list-link {item["class_name"]}"><i class="fa {item["icon"]}" aria-hidden="true"></i></a>'
            for item in action_items
        ) + "</span>"

    items = []
    for entry in entries:
        fields = entry.get("fields", {})
        title = title_label(fields)
        author = author_label(fields)
        year = clean(fields.get("year", "") or "")
        source = source_label(fields)
        key = key_label(entry)
        action_items = action_links(entry, fields)
        if view_mode == "cards":
            items.append(f"""
              <article class="card bib-card">
                <span class="bib-entry-title">{title}</span>
                {f'<span class="bib-entry-meta bib-entry-author">{author}</span>' if author else ''}
                {f'<span class="bib-entry-meta d-block">{source}</span>' if source else ''}
                {f'<span class="bib-entry-muted d-block">{year}</span>' if year else ''}
                {f'<div class="actions">{card_actions(action_items)}</div>' if action_items else ''}
              </article>
            """)
        else:
            head = ". ".join([piece for piece in [f"{author} ({year})" if author and year else author or (f"({year})" if year else ""), f"<span class='bib-entry-title'>{title}</span>", source] if piece])
            items.append(f"""
              <div class="mb-2 export-list-row">
                <span class="text-muted small me-1"><i class="fa fa-file-text" aria-hidden="true"></i></span>
                <span>{head}.</span>
                {list_actions(action_items)}
                <div class="picker-key mt-1">{key}</div>
              </div>
            """)
    container = f"<div class='cards'>{''.join(items)}</div>" if view_mode == "cards" else f"<div class='list'>{''.join(items)}</div>"
    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Bibry Export</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css" rel="stylesheet">
    <style>
      body{{padding:1rem;background:#f5f5f5}}
      .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem}}
      .card{{display:block;width:100%;padding:12px;margin:0;background:#fff;border:1px solid #ddd;border-radius:6px}}
      .actions{{margin-top:6px;display:flex;gap:6px;flex-wrap:wrap}}
      .bib-entry-title,.bib-entry-meta,.bib-entry-muted{{overflow-wrap:break-word;word-break:normal}}
      .bib-entry-title{{font-weight:700;color:inherit}}
      .bib-entry-meta{{color:#212529}}
      .bib-entry-muted{{color:#6c757d}}
      .bib-entry-author{{display:block;margin-top:.5em}}
      .picker-key{{font-family:monospace;font-size:.85rem;color:#6c757d}}
      .list{{padding:6px}}
      .export-list-row{{background:transparent}}
      .export-card-link.pdf{{background:#dc3545;border-color:#dc3545;color:#fff}}
      .export-card-link.url,.export-card-link.arxiv{{background:#0d6efd;border-color:#0d6efd;color:#fff}}
      .export-card-link.doi{{background:#0dcaf0;border-color:#0dcaf0;color:#000}}
      .export-list-link{{color:#6c757d}}
      .export-list-link.pdf{{color:#dc3545}}
    </style>
  </head>
  <body>
    <div class="container-fluid">
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h1 class="h4 mb-0">Bibry Export</h1>
        <div class="text-muted small">{len(entries)} entries</div>
      </div>
    </div>
    {container}
  </body>
</html>"""


def _normalized_key_token(value):
    token = latex_to_text(value or "")
    token = re.sub(r"[^A-Za-z0-9]+", "", token)
    return token


def _lead_author_token(entry):
    authors = entry.get("author", entry.get("editor", "")) or ""
    first = re.split(r"\s+and\s+", authors, maxsplit=1)[0].strip()
    if "," in first:
        surname = first.split(",", 1)[0].strip()
    else:
        surname = first.split()[-1] if first.split() else ""
    return _normalized_key_token(surname)


def _entry_year_token(entry):
    return _normalized_key_token(entry.get("year", ""))


def _derived_key_for_change(existing_key, existing_entry, new_entry):
    old_author = _lead_author_token(existing_entry)
    new_author = _lead_author_token(new_entry)
    old_year = _entry_year_token(existing_entry)
    new_year = _entry_year_token(new_entry)
    if (not old_author and not old_year) or (old_author == new_author and old_year == new_year):
        return existing_key

    pattern = re.compile(rf"^{re.escape(old_author)}{re.escape(old_year)}(?P<suffix>[A-Za-z]*)$", re.IGNORECASE)
    match = pattern.match(existing_key or "")
    if not match or not new_author or not new_year:
        return existing_key
    return f"{new_author}{new_year}{match.group('suffix')}"


def _move_pdf_for_key_change(old_key, new_key):
    if not old_key or not new_key or old_key == new_key:
        return False
    old_path = PDF_DIR / f"{old_key}.pdf"
    new_path = PDF_DIR / f"{new_key}.pdf"
    if not old_path.exists() or new_path.exists():
        return False
    old_path.rename(new_path)
    return True


def _replace_entry_with_key(db, existing_index, key, new_entry, action):
    new_key = new_entry.get("ID") or key
    if new_key != key and any(index != existing_index and entry.get("ID") == new_key for index, entry in enumerate(db.entries)):
        abort(409, f"Entry '{new_key}' already exists")
    db.entries[existing_index] = new_entry
    bibstore.save_bib(db, action=action)
    _move_pdf_for_key_change(key, new_key)
    rename_entry_metadata(key, new_key)
    return new_key

# Endpoint: current version for live refresh
@api_bp.route("/version")
def api_version():
    return jsonify({"version": bibstore.BIB_VERSION})


@api_bp.route("/bibs")
def api_bibs():
    return jsonify({"items": bibstore.list_bib_files()})


@api_bp.route("/bibs/select", methods=["POST"])
def api_select_bib():
    filename = request.json.get("filename", "")
    if not filename:
        abort(400, "Bib filename is required")
    try:
        bibstore.set_current_bib_filename(filename)
    except FileNotFoundError:
        abort(404, "Bib file not found")
    except ValueError as exc:
        abort(400, str(exc))
    return jsonify({"ok": True, "filename": bibstore.get_current_bib_filename()})

# Endpoint: list all entries (with metadata)
@api_bp.route("/entries")
def api_entries():
    pdf_files = {p.stem for p in PDF_DIR.glob("*.pdf")}
    out = []
    for entry in _build_entry_cache():
        statuses, metadata = _entry_statuses(entry["key"])
        out.append({
            "key": entry["key"],
            "type": entry["type"],
            "fields": entry["fields"],
            "raw": entry["raw"],
            "has_pdf": entry["key"] in pdf_files,
            "statuses": statuses,
            "metadata": metadata,
        })
    return jsonify(out)

# Endpoint: get raw BibTeX for one entry
@api_bp.route("/entry/<key>")
def api_entry(key):
    db = bibstore.load_bib()
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
    db = bibstore.load_bib()
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
    last_state = bibstore.get_last_bib_state()
    if last_state is None:
        abort(400, "Nothing to undo")
    # Swap current and last states
    current = bibstore.get_current_bib_path().read_text(encoding="utf-8")
    bibstore.save_bib_text(last_state, action="undo")
    bibstore.set_last_bib_state(current)
    return jsonify({"ok": True})

# Serve PDF files
def serve_pdf(filename):
    return send_from_directory(PDF_DIR, filename)

# Edit endpoint
@api_bp.route("/entry/<key>", methods=["POST"])
def api_edit(key):

    raw = request.json.get("raw","")

    db = bibstore.load_bib()

    if raw.strip()=="":
        db.entries = [e for e in db.entries if e.get("ID")!=key]
        bibstore.save_bib(db, action="delete-entry")
        return jsonify({"deleted":True, "key": key})

    parser = bibtexparser.bparser.BibTexParser(common_strings=True)
    newdb = bibtexparser.loads(raw, parser=parser)

    if len(newdb.entries)!=1:
        abort(400,"Invalid entry")

    new_entry = newdb.entries[0]

    for i,e in enumerate(db.entries):
        if e.get("ID")==key:
            new_key = _replace_entry_with_key(db, i, key, new_entry, action="update-entry")
            return jsonify({"ok":True, "key": new_key})

    abort(404)


@api_bp.route("/entry", methods=["POST"])
def api_add_entry():
    raw = request.json.get("raw", "")
    if raw.strip() == "":
        abort(400, "Entry content is required")

    parser = bibtexparser.bparser.BibTexParser(common_strings=True)
    newdb = bibtexparser.loads(raw, parser=parser)

    if len(newdb.entries) != 1:
        abort(400, "Invalid entry")

    new_entry = newdb.entries[0]
    new_key = new_entry.get("ID")
    if not new_key:
        abort(400, "Entry key is required")

    db = bibstore.load_bib()
    if any(e.get("ID") == new_key for e in db.entries):
        abort(409, f"Entry '{new_key}' already exists")

    db.entries.append(new_entry)
    bibstore.save_bib(db, action="add-entry")
    return jsonify({"ok": True, "key": new_key}), 201


@api_bp.route("/import/preview", methods=["POST"])
def api_import_preview():
    upload = request.files.get("file")
    if upload is None or upload.filename == "":
        abort(400, "No file provided")

    text = upload.read().decode("utf-8", errors="replace")
    raw_entries = split_entries(text)
    if not raw_entries:
        abort(400, "No BibTeX entries found in file")

    existing_entries = {
        entry.get("ID"): entry
        for entry in bibstore.load_bib().entries
        if entry.get("ID")
    }
    previews = []
    for raw in raw_entries:
        raw = raw.strip()
        if not raw.startswith("@"):
            continue
        preview = _entry_preview(raw)
        incoming_entry = bibtexparser.loads(raw).entries[0]
        existing_entry = existing_entries.get(preview["key"])
        if existing_entry is None:
            preview["status"] = "new"
            preview["exists"] = False
            preview["selected"] = True
            preview["conflict"] = None
        elif _entry_signature(existing_entry) == _entry_signature(incoming_entry):
            preview["status"] = "same"
            preview["exists"] = True
            preview["selected"] = False
            preview["conflict"] = None
        else:
            preview["status"] = "conflict"
            preview["exists"] = True
            preview["selected"] = False
            preview["conflict"] = _entry_conflict(existing_entry, incoming_entry)
        previews.append(preview)

    return jsonify({"entries": previews})


@api_bp.route("/import", methods=["POST"])
def api_import_entries():
    selected_entries = request.json.get("entries", [])
    if not isinstance(selected_entries, list) or not selected_entries:
        abort(400, "No entries selected for import")

    existing_entries = {
        entry.get("ID"): entry
        for entry in bibstore.load_bib().entries
        if entry.get("ID")
    }
    imported_count = 0
    updated_count = 0
    unchanged_count = 0
    for raw in selected_entries:
        if not isinstance(raw, str) or not raw.strip():
            continue
        parsed = bibtexparser.loads(raw).entries
        if len(parsed) != 1:
            continue
        incoming_entry = parsed[0]
        key = incoming_entry.get("ID")
        existing_entry = existing_entries.get(key)
        if existing_entry is None:
            imported_count += 1
        elif _entry_signature(existing_entry) == _entry_signature(incoming_entry):
            unchanged_count += 1
        else:
            updated_count += 1

    current_text = ""
    current_bib = bibstore.get_current_bib_path()
    if current_bib.exists():
        current_text = current_bib.read_text(encoding="utf-8").strip()

    parts = [current_text] if current_text else []
    parts.extend(raw.strip() for raw in selected_entries if isinstance(raw, str) and raw.strip())
    merged_text = "\n\n".join(parts)

    normalized_text, stats = process_bibtex_text(merged_text)
    before_count = len(bibstore.load_bib().entries)
    bibstore.save_bib_text(normalized_text, action="import")

    return jsonify({
        "ok": True,
        "selected_count": len(selected_entries),
        "imported_count": imported_count,
        "updated_count": updated_count,
        "unchanged_count": unchanged_count,
        "total_entries": stats["after_dedupe"],
    })


@api_bp.route("/export", methods=["POST"])
def api_export_entries():
    keys = request.json.get("keys", [])
    export_format = request.json.get("format", "bib")
    html_view = request.json.get("html_view", "list")
    if not isinstance(keys, list) or not keys:
        abort(400, "No entries selected for export")

    key_set = set(keys)
    selected_entries = [entry for entry in _build_entry_cache() if entry["key"] in key_set]
    selected_raw = [entry["raw"].strip() for entry in selected_entries]
    if not selected_entries:
        abort(400, "No matching entries found")

    normalized_text, stats = process_bibtex_text("\n\n".join(selected_raw))
    if export_format == "zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("export.bib", normalized_text)
            archive.writestr("index.html", _render_export_html(selected_entries, view_mode=html_view))
            for entry in selected_entries:
                key = entry.get("key")
                if not key:
                    continue
                pdf_path = PDF_DIR / f"{key}.pdf"
                if pdf_path.exists():
                    archive.write(pdf_path, arcname=f"pdf/{key}.pdf")
        response = Response(buffer.getvalue(), mimetype="application/zip")
        response.headers["Content-Disposition"] = 'attachment; filename="export.zip"'
        response.headers["X-Bibry-Exported-Count"] = str(stats["after_dedupe"])
        return response

    response = Response(normalized_text, mimetype="application/x-bibtex")
    response.headers["Content-Disposition"] = 'attachment; filename="export.bib"'
    response.headers["X-Bibry-Exported-Count"] = str(stats["after_dedupe"])
    return response


@api_bp.route("/history")
def api_history():
    return jsonify({"items": bibstore.list_history()})


@api_bp.route("/history/<revision_id>/restore", methods=["POST"])
def api_history_restore(revision_id):
    revision = bibstore.get_history_revision(revision_id)
    if revision is None:
        abort(404, "History entry not found")

    try:
        restored_text = bibstore.reconstruct_bib_text_at_revision(revision_id)
    except FileNotFoundError:
        abort(404, "History entry not found")

    bibstore.save_bib_text(restored_text, action="history-restore")
    return jsonify({
        "ok": True,
        "revision_id": revision_id,
        "timestamp": revision.get("timestamp"),
    })


@api_bp.route("/scan/services")
def api_scan_services():
    return jsonify({"items": list_scan_services()})


@api_bp.route("/scan/run", methods=["POST"])
def api_scan_run():
    service_name = request.json.get("service", "")
    scanner = get_scan_service(service_name)
    if scanner is None:
        abort(404, "Unknown scan service")

    availability = scanner.availability()
    if not availability.get("available"):
        abort(400, availability.get("reason") or "Scan service is unavailable")

    result = scanner.scan_entries(build_entries_by_key())
    if isinstance(result, dict):
        actionable = result.get("items", [])
        extra = {key: value for key, value in result.items() if key != "items"}
    else:
        actionable = result
        extra = {}
    actionable.sort(key=lambda item: (item["key"] or "").lower())
    payload = {
        "service": service_name,
        "label": scanner.display_name,
        "phase": scanner.phase_name,
        "items": actionable,
    }
    payload.update(extra)
    return jsonify(payload)


@api_bp.route("/scan/jobs", methods=["POST"])
def api_scan_jobs_start():
    service_name = request.json.get("service", "")
    scanner = get_scan_service(service_name)
    if scanner is None:
        abort(404, "Unknown scan service")
    if service_name not in {"crossref", "worldcat"}:
        abort(400, "Background jobs are only supported for Crossref and WorldCat scans")
    availability = scanner.availability()
    if not availability.get("available"):
        abort(400, availability.get("reason") or "Scan service is unavailable")
    try:
        job = start_scan_job(service_name)
    except RuntimeError as exc:
        abort(409, str(exc))
    except LookupError as exc:
        abort(404, str(exc))
    return jsonify(job), 202


@api_bp.route("/scan/jobs/<job_id>")
def api_scan_job_status(job_id):
    cursor = request.args.get("cursor", "0")
    job = get_scan_job(job_id, cursor=cursor)
    if job is None:
        abort(404, "Scan job not found")
    return jsonify(job)


@api_bp.route("/scan/jobs/<job_id>/cancel", methods=["POST"])
def api_scan_job_cancel(job_id):
    job = cancel_scan_job(job_id)
    if job is None:
        abort(404, "Scan job not found")
    return jsonify({"ok": True, "id": job_id})


@api_bp.route("/scan/review/apply", methods=["POST"])
def api_scan_apply():
    key = request.json.get("key", "")
    raw = request.json.get("raw", "")
    basis_signature = request.json.get("basis_signature")
    suggestion_id = request.json.get("id", "")
    provenance = request.json.get("provenance") or {}

    if not key or not raw:
        abort(400, "Scan item key and proposed raw entry are required")

    db = bibstore.load_bib()
    existing_index = next((index for index, entry in enumerate(db.entries) if entry.get("ID") == key), None)
    if existing_index is None:
        abort(404, "Entry not found")

    existing_entry = db.entries[existing_index]
    if basis_signature and _entry_signature(existing_entry) != basis_signature:
        abort(409, "Entry changed since the scan ran; rerun the scan before applying this patch")

    parser = bibtexparser.bparser.BibTexParser(common_strings=True)
    newdb = bibtexparser.loads(raw, parser=parser)
    if len(newdb.entries) != 1:
        abort(400, "Invalid proposed entry")

    proposed_entry = newdb.entries[0]
    derived_key = _derived_key_for_change(key, existing_entry, proposed_entry)
    if derived_key and derived_key != (proposed_entry.get("ID") or ""):
        proposed_entry["ID"] = derived_key

    new_key = _replace_entry_with_key(db, existing_index, key, proposed_entry, action="quality-scan-apply")
    if suggestion_id:
        set_entry_provenance(new_key, "phase1_crossref_last_apply", {
            "source": provenance.get("source") or "Crossref",
            "identifier_used": provenance.get("identifier_used") or "",
            "applied_at": provenance.get("scanned_at") or "",
            "suggestion_id": suggestion_id,
        })
    return jsonify({"ok": True, "key": new_key})


@api_bp.route("/scan/review/reject", methods=["POST"])
def api_scan_reject():
    key = request.json.get("key", "")
    suggestion_id = request.json.get("id", "")
    fingerprint = request.json.get("fingerprint", "")
    suppress = bool(request.json.get("suppress"))

    if not key or not suggestion_id:
        abort(400, "Scan item key and id are required")

    if suppress:
        set_suppression(key, suggestion_id, {"fingerprint": fingerprint})
    return jsonify({"ok": True, "key": key, "suppressed": suppress})


@api_bp.route("/scan/rejections/clear", methods=["POST"])
def api_scan_clear_rejections():
    phase = request.json.get("phase") or ""
    cleared = clear_suppressions(phase_prefix=phase or None)
    return jsonify({"ok": True, "cleared": cleared})


@api_bp.route("/entry/<key>/pdf", methods=["POST"])
def api_attach_pdf(key):
    upload = request.files.get("file")
    if upload is None or upload.filename == "":
        abort(400, "No PDF file provided")

    filename = Path(upload.filename).name
    if not filename.lower().endswith(".pdf"):
        abort(400, "Only PDF files are supported")

    db = bibstore.load_bib()
    if not any(entry.get("ID") == key for entry in db.entries):
        abort(404, "Entry not found")

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    target = PDF_DIR / f"{key}.pdf"
    upload.save(target)
    set_entry_flag(key, "no_pdf_expected", False)
    return jsonify({"ok": True, "key": key, "filename": target.name})


@api_bp.route("/entry/<key>/no-pdf-expected", methods=["POST"])
def api_mark_no_pdf_expected(key):
    db = bibstore.load_bib()
    if not any(entry.get("ID") == key for entry in db.entries):
        abort(404, "Entry not found")

    enabled = bool(request.json.get("enabled", True))
    detail = {"label": "No PDF expected", "source": "user"}
    set_entry_flag(key, "no_pdf_expected", enabled, detail if enabled else {})
    return jsonify({"ok": True, "key": key, "enabled": enabled})
