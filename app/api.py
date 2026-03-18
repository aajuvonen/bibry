# app/api.py
from flask import Blueprint, jsonify, request, abort, send_from_directory, Response
import bibtexparser
from pathlib import Path

from . import bibstore
from .enrichment import build_entries_by_key, get_scan_service, list_scan_services
from .latex import latex_to_text
from .metadata_store import clear_suppressions, get_entry_metadata, set_entry_flag, set_entry_provenance, set_suppression
from .sort_dedupe_bibtex import BibEntry, process_bibtex_text, split_entries

api_bp = Blueprint('api', __name__)

# Directory for PDFs
PDF_DIR = bibstore.ROOT / "pdf"
_ENTRY_CACHE_SIGNATURE = object()
_ENTRY_CACHE_BASE = None


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
            k: latex_to_text(v) if isinstance(v, str) else v
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
    return (
        (entry.get("ENTRYTYPE") or "").lower(),
        tuple(sorted(
            (key, value.strip())
            for key, value in entry.items()
            if key not in {"ID", "ENTRYTYPE"} and isinstance(value, str)
        )),
    )


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
            db.entries[i]=new_entry
            bibstore.save_bib(db, action="update-entry")
            return jsonify({"ok":True, "key": new_entry.get("ID", key)})

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
    if not isinstance(keys, list) or not keys:
        abort(400, "No entries selected for export")

    key_set = set(keys)
    selected_raw = [entry["raw"].strip() for entry in _build_entry_cache() if entry["key"] in key_set]
    if not selected_raw:
        abort(400, "No matching entries found")

    normalized_text, stats = process_bibtex_text("\n\n".join(selected_raw))
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

    db.entries[existing_index] = newdb.entries[0]
    bibstore.save_bib(db, action="quality-scan-apply")
    if suggestion_id:
        set_entry_provenance(key, "phase1_crossref_last_apply", {
            "source": provenance.get("source") or "Crossref",
            "identifier_used": provenance.get("identifier_used") or "",
            "applied_at": provenance.get("scanned_at") or "",
            "suggestion_id": suggestion_id,
        })
    return jsonify({"ok": True, "key": key})


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
