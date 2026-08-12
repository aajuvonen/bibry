import json
from datetime import datetime, timezone

from . import bibstore


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _path():
    return bibstore.BIB_DIR / "orphan_pdf_state.json"


def fingerprint(path):
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _load():
    path = _path()
    if not path.exists():
        return {"ignored": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ignored": {}}
    return data if isinstance(data, dict) else {"ignored": {}}


def _save(data):
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def is_ignored(filename, file_fingerprint):
    return _load().get("ignored", {}).get(filename, {}).get("fingerprint") == file_fingerprint


def ignore(filename, file_fingerprint):
    data = _load()
    data.setdefault("ignored", {})[filename] = {
        "fingerprint": file_fingerprint,
        "ignored_at": _now(),
    }
    _save(data)
