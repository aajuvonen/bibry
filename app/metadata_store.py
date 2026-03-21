import json
from datetime import datetime, timezone
from pathlib import Path

from . import bibstore


METADATA_DIR = bibstore.BIB_DIR / "metadata"


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _metadata_path():
    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    return METADATA_DIR / f"{bibstore.get_current_bib_filename()}.json"


def load_metadata():
    path = _metadata_path()
    if not path.exists():
        return {"entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"entries": {}}
    if not isinstance(data, dict):
        return {"entries": {}}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        data["entries"] = {}
    return data


def save_metadata(data):
    path = _metadata_path()
    payload = {
        "updated_at": _utc_now(),
        "entries": data.get("entries", {}),
    }
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def get_entry_metadata(key):
    entries = load_metadata().get("entries", {})
    value = entries.get(key, {})
    return value if isinstance(value, dict) else {}


def update_entry_metadata(key, updater):
    data = load_metadata()
    entries = data.setdefault("entries", {})
    current = entries.get(key, {})
    if not isinstance(current, dict):
        current = {}
    updated = updater(dict(current))
    entries[key] = updated
    save_metadata(data)
    return updated


def merge_entry_metadata(key, patch):
    def updater(current):
        current.update(patch)
        current["updated_at"] = _utc_now()
        return current

    return update_entry_metadata(key, updater)


def clear_entry_metadata(key):
    data = load_metadata()
    entries = data.setdefault("entries", {})
    if key in entries:
        entries.pop(key, None)
        save_metadata(data)


def rename_entry_metadata(old_key, new_key):
    if not old_key or not new_key or old_key == new_key:
        return

    data = load_metadata()
    entries = data.setdefault("entries", {})
    current = entries.get(old_key)
    if not isinstance(current, dict):
        return

    target = entries.get(new_key, {})
    if not isinstance(target, dict):
        target = {}

    merged = dict(target)
    merged.update(current)
    merged["updated_at"] = _utc_now()
    entries[new_key] = merged
    entries.pop(old_key, None)
    save_metadata(data)


def set_entry_flag(key, flag_name, enabled, detail=None):
    detail = detail or {}

    def updater(current):
        flags = current.setdefault("flags", {})
        if enabled:
            payload = dict(detail)
            payload["active"] = True
            payload["updated_at"] = _utc_now()
            flags[flag_name] = payload
        else:
            flags.pop(flag_name, None)
        current["updated_at"] = _utc_now()
        return current

    return update_entry_metadata(key, updater)


def set_entry_provenance(key, phase_name, detail):
    def updater(current):
        provenance = current.setdefault("provenance", {})
        payload = dict(detail)
        payload["updated_at"] = _utc_now()
        provenance[phase_name] = payload
        current["updated_at"] = _utc_now()
        return current

    return update_entry_metadata(key, updater)


def set_suppression(key, suggestion_id, payload):
    def updater(current):
        suppressed = current.setdefault("suppressed", {})
        data = dict(payload)
        data["updated_at"] = _utc_now()
        suppressed[suggestion_id] = data
        current["updated_at"] = _utc_now()
        return current

    return update_entry_metadata(key, updater)


def get_suppression(key, suggestion_id):
    entry = get_entry_metadata(key)
    suppressed = entry.get("suppressed", {})
    if not isinstance(suppressed, dict):
        return None
    value = suppressed.get(suggestion_id)
    return value if isinstance(value, dict) else None


def clear_suppressions(phase_prefix=None):
    data = load_metadata()
    entries = data.setdefault("entries", {})
    cleared = 0
    for key, current in list(entries.items()):
        if not isinstance(current, dict):
            continue
        suppressed = current.get("suppressed")
        if not isinstance(suppressed, dict) or not suppressed:
            continue
        if not phase_prefix:
            cleared += len(suppressed)
            current["suppressed"] = {}
            current["updated_at"] = _utc_now()
            continue

        kept = {}
        for suggestion_id, payload in suppressed.items():
            if str(suggestion_id).startswith(phase_prefix):
                cleared += 1
            else:
                kept[suggestion_id] = payload
        current["suppressed"] = kept
        current["updated_at"] = _utc_now()

    if cleared:
        save_metadata(data)
    return cleared
