# app/bibstore.py
import bibtexparser
import difflib
import json
from datetime import datetime, timezone
from pathlib import Path

from .latex import latex_to_text
from .sort_dedupe_bibtex import BibEntry
from .sort_dedupe_bibtex import split_entries

# Path to the BibTeX file (flat-file store)
ROOT = Path(__file__).parent.parent
BIBFILE = ROOT / "main.bib"
BACKUP = ROOT / "backups" / "main.bib.bak"
HISTORY_DIR = ROOT / "backups" / "history"
HISTORY_LIMIT = 100

# Globals for tracking last state and version (for live-refresh)
LAST_BIB_STATE = None
BIB_VERSION = 0
_BIB_SIGNATURE = None
_DB_CACHE = None


def get_bib_signature():
    if not BIBFILE.exists():
        return None
    stat = BIBFILE.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _entry_count(text):
    return len([raw for raw in split_entries(text or "") if raw.strip().startswith("@")])


def _diff_stats(diff_text):
    added = 0
    removed = 0
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _entry_map(text):
    entries = {}
    for raw in split_entries(text or ""):
        raw = raw.strip()
        if not raw.startswith("@"):
            continue
        entry = BibEntry(raw)
        if entry.key:
            entries[entry.key] = entry
    return entries


def _entry_text(entry, field):
    if not entry:
        return ""
    if field == "key":
        return entry.key
    if field == "type":
        return entry.type
    return latex_to_text(entry.fields.get(field, ""))


def _entry_signature(entry):
    if entry is None:
        return None
    return (
        (entry.type or "").lower(),
        tuple(sorted((key, value.strip()) for key, value in entry.fields.items())),
    )


def _entry_change_summary(previous_entry, new_entry):
    source = new_entry or previous_entry
    changed_fields = []
    if previous_entry and new_entry:
        field_names = sorted(set(previous_entry.fields) | set(new_entry.fields))
        for field in field_names:
            before = latex_to_text(previous_entry.fields.get(field, ""))
            after = latex_to_text(new_entry.fields.get(field, ""))
            if before != after:
                changed_fields.append({
                    "field": field,
                    "before": before,
                    "after": after,
                })

    return {
        "key": source.key if source else "",
        "change_type": (
            "added" if previous_entry is None else
            "removed" if new_entry is None else
            "edited"
        ),
        "entry_type": (source.type or "").lower() if source else "",
        "title_before": _entry_text(previous_entry, "title"),
        "title_after": _entry_text(new_entry, "title"),
        "author_before": _entry_text(previous_entry, "author") or _entry_text(previous_entry, "editor"),
        "author_after": _entry_text(new_entry, "author") or _entry_text(new_entry, "editor"),
        "year_before": _entry_text(previous_entry, "year"),
        "year_after": _entry_text(new_entry, "year"),
        "changed_fields": changed_fields[:8],
    }


def _entry_changes(previous_text, new_text):
    previous_entries = _entry_map(previous_text)
    new_entries = _entry_map(new_text)
    changes = []

    for key in sorted(set(previous_entries) | set(new_entries)):
        before = previous_entries.get(key)
        after = new_entries.get(key)
        if before and after:
            if _entry_signature(before) == _entry_signature(after):
                continue
        changes.append(_entry_change_summary(before, after))

    return changes


def _record_history(previous_text, new_text, action):
    if previous_text == new_text:
        return

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc)
    revision_id = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
    diff_text = "".join(
        difflib.unified_diff(
            (previous_text or "").splitlines(keepends=True),
            (new_text or "").splitlines(keepends=True),
            fromfile="main.bib:before",
            tofile="main.bib:after",
            n=3,
        )
    )
    added, removed = _diff_stats(diff_text)
    payload = {
        "id": revision_id,
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "action": action,
        "entries_before": _entry_count(previous_text),
        "entries_after": _entry_count(new_text),
        "lines_added": added,
        "lines_removed": removed,
        "diff": diff_text,
        "changes": _entry_changes(previous_text, new_text),
        "snapshot": new_text,
    }
    history_path = HISTORY_DIR / f"{revision_id}__{action}.json"
    history_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    history_files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)
    for old_path in history_files[HISTORY_LIMIT:]:
        old_path.unlink(missing_ok=True)


def list_history():
    if not HISTORY_DIR.exists():
        return []

    items = []
    for path in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items.append({
            "id": data.get("id"),
            "timestamp": data.get("timestamp"),
            "action": data.get("action", "save"),
            "entries_before": data.get("entries_before", 0),
            "entries_after": data.get("entries_after", 0),
            "lines_added": data.get("lines_added", 0),
            "lines_removed": data.get("lines_removed", 0),
            "changes": data.get("changes", []),
            "diff_preview": "\n".join((data.get("diff") or "").splitlines()[:40]),
        })
    return items


def get_history_revision(revision_id):
    if not HISTORY_DIR.exists():
        return None

    for path in HISTORY_DIR.glob(f"{revision_id}__*.json"):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None

def load_bib():
    """Load or reload the BibTeX database."""
    global _BIB_SIGNATURE, _DB_CACHE
    signature = get_bib_signature()
    if _DB_CACHE is not None and signature == _BIB_SIGNATURE:
        return _DB_CACHE
    if not BIBFILE.exists():
        # If no file, start with empty database
        db = bibtexparser.bibdatabase.BibDatabase()
        db.entries = []
        _BIB_SIGNATURE = None
        _DB_CACHE = db
        return db
    with open(BIBFILE, encoding="utf-8") as f:
        parser = bibtexparser.bparser.BibTexParser(common_strings=True)
        db = bibtexparser.load(f, parser)
    _BIB_SIGNATURE = signature
    _DB_CACHE = db
    return db

def save_bib(db, action="save"):
    """Write the BibTeX database back to file, with undo tracking."""
    global LAST_BIB_STATE, BIB_VERSION, _BIB_SIGNATURE, _DB_CACHE
    if not BACKUP.parent.exists():
        BACKUP.parent.mkdir(parents=True)
    previous_text = ""
    # Backup current state
    if BIBFILE.exists():
        previous_text = BIBFILE.read_text(encoding="utf-8")
        LAST_BIB_STATE = previous_text
        # Also save a timestamped backup copy
        BACKUP.write_text(LAST_BIB_STATE, encoding="utf-8")
    # Write new state
    writer = bibtexparser.bwriter.BibTexWriter()
    new_text = writer.write(db)
    BIBFILE.write_text(new_text, encoding="utf-8")
    _record_history(previous_text, new_text, action)
    _BIB_SIGNATURE = get_bib_signature()
    _DB_CACHE = db
    BIB_VERSION += 1


def save_bib_text(text, action="save-text"):
    """Write raw BibTeX text back to file, with undo tracking."""
    global LAST_BIB_STATE, BIB_VERSION, _BIB_SIGNATURE, _DB_CACHE
    if not BACKUP.parent.exists():
        BACKUP.parent.mkdir(parents=True)
    previous_text = ""
    if BIBFILE.exists():
        previous_text = BIBFILE.read_text(encoding="utf-8")
        LAST_BIB_STATE = previous_text
        BACKUP.write_text(LAST_BIB_STATE, encoding="utf-8")
    else:
        LAST_BIB_STATE = None

    BIBFILE.write_text(text, encoding="utf-8")
    _record_history(previous_text, text, action)
    _BIB_SIGNATURE = None
    _DB_CACHE = None
    BIB_VERSION += 1
