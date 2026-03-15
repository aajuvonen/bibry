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
BIB_DIR = ROOT / "bib"
ACTIVE_BIB = BIB_DIR / ".active_bib"
DEFAULT_BIB = "main.bib"
HISTORY_ROOT = BIB_DIR / "history"
HISTORY_LIMIT = 100

# Globals for tracking last state and version (for live-refresh)
BIB_VERSION = 0
_BIB_SIGNATURE = None
_DB_CACHE = None
_LAST_BIB_STATE = {}


def ensure_bib_dirs():
    BIB_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_ROOT.mkdir(parents=True, exist_ok=True)


def _safe_bib_name(filename):
    name = Path(filename).name
    if not name.endswith(".bib"):
        raise ValueError("Bib filename must end with .bib")
    return name


def get_available_bib_paths():
    ensure_bib_dirs()
    return sorted(BIB_DIR.glob("*.bib"))


def get_current_bib_filename():
    ensure_bib_dirs()
    if ACTIVE_BIB.exists():
        candidate = ACTIVE_BIB.read_text(encoding="utf-8").strip()
        if candidate and (BIB_DIR / candidate).exists():
            return candidate

    default_path = BIB_DIR / DEFAULT_BIB
    if default_path.exists():
        return DEFAULT_BIB

    bib_paths = get_available_bib_paths()
    if bib_paths:
        return bib_paths[0].name
    return DEFAULT_BIB


def set_current_bib_filename(filename):
    global _BIB_SIGNATURE, _DB_CACHE
    ensure_bib_dirs()
    name = _safe_bib_name(filename)
    if not (BIB_DIR / name).exists():
        raise FileNotFoundError(name)
    ACTIVE_BIB.write_text(name, encoding="utf-8")
    _BIB_SIGNATURE = None
    _DB_CACHE = None


def get_current_bib_path():
    return BIB_DIR / get_current_bib_filename()


def get_current_history_dir():
    history_dir = HISTORY_ROOT / get_current_bib_filename()
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir


def get_last_bib_state():
    return _LAST_BIB_STATE.get(get_current_bib_filename())


def set_last_bib_state(value):
    filename = get_current_bib_filename()
    if value is None:
        _LAST_BIB_STATE.pop(filename, None)
    else:
        _LAST_BIB_STATE[filename] = value


def list_bib_files():
    items = []
    current = get_current_bib_filename()
    for path in get_available_bib_paths():
        stat = path.stat()
        text = path.read_text(encoding="utf-8")
        items.append({
            "filename": path.name,
            "selected": path.name == current,
            "entry_count": _entry_count(text),
            "created_at": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        })
    return items


def get_bib_signature():
    bibfile = get_current_bib_path()
    if not bibfile.exists():
        return None
    stat = bibfile.stat()
    return (bibfile.name, stat.st_mtime_ns, stat.st_size)


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

    history_dir = get_current_history_dir()

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
    history_path = history_dir / f"{revision_id}__{action}.json"
    history_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    history_files = sorted(history_dir.glob("*.json"), reverse=True)
    for old_path in history_files[HISTORY_LIMIT:]:
        old_path.unlink(missing_ok=True)


def list_history():
    history_dir = get_current_history_dir()
    if not history_dir.exists():
        return []

    items = []
    for path in sorted(history_dir.glob("*.json"), reverse=True):
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
    history_dir = get_current_history_dir()
    if not history_dir.exists():
        return None

    for path in history_dir.glob(f"{revision_id}__*.json"):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def load_bib():
    """Load or reload the BibTeX database."""
    global _BIB_SIGNATURE, _DB_CACHE
    ensure_bib_dirs()
    signature = get_bib_signature()
    if _DB_CACHE is not None and signature == _BIB_SIGNATURE:
        return _DB_CACHE
    bibfile = get_current_bib_path()
    if not bibfile.exists():
        # If no file, start with empty database
        db = bibtexparser.bibdatabase.BibDatabase()
        db.entries = []
        _BIB_SIGNATURE = None
        _DB_CACHE = db
        return db
    with open(bibfile, encoding="utf-8") as f:
        parser = bibtexparser.bparser.BibTexParser(common_strings=True)
        db = bibtexparser.load(f, parser)
    _BIB_SIGNATURE = signature
    _DB_CACHE = db
    return db


def save_bib(db, action="save"):
    """Write the BibTeX database back to file, with undo tracking."""
    global BIB_VERSION, _BIB_SIGNATURE, _DB_CACHE
    ensure_bib_dirs()
    bibfile = get_current_bib_path()
    previous_text = ""
    # Backup current state
    if bibfile.exists():
        previous_text = bibfile.read_text(encoding="utf-8")
        set_last_bib_state(previous_text)
    else:
        set_last_bib_state(None)
    # Write new state
    writer = bibtexparser.bwriter.BibTexWriter()
    new_text = writer.write(db)
    bibfile.write_text(new_text, encoding="utf-8")
    _record_history(previous_text, new_text, action)
    _BIB_SIGNATURE = get_bib_signature()
    _DB_CACHE = db
    BIB_VERSION += 1


def save_bib_text(text, action="save-text"):
    """Write raw BibTeX text back to file, with undo tracking."""
    global BIB_VERSION, _BIB_SIGNATURE, _DB_CACHE
    ensure_bib_dirs()
    bibfile = get_current_bib_path()
    previous_text = ""
    if bibfile.exists():
        previous_text = bibfile.read_text(encoding="utf-8")
        set_last_bib_state(previous_text)
    else:
        set_last_bib_state(None)

    bibfile.write_text(text, encoding="utf-8")
    _record_history(previous_text, text, action)
    _BIB_SIGNATURE = None
    _DB_CACHE = None
    BIB_VERSION += 1
