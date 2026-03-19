import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import bibstore
from .enrichment import build_entries_by_key, get_scan_service


SCAN_JOB_DIR = bibstore.BIB_DIR / "scan_jobs"
_RUNNERS = {}
_LOCK = threading.Lock()


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _job_path(job_id):
    SCAN_JOB_DIR.mkdir(parents=True, exist_ok=True)
    return SCAN_JOB_DIR / f"{job_id}.json"


def _write_job(job_id, data):
    path = _job_path(job_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _read_job(job_id):
    path = _job_path(job_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _active_job():
    for path in sorted(SCAN_JOB_DIR.glob("*.json")):
        data = _read_job(path.stem)
        if data and data.get("status") == "running":
            return data
    return None


def start_scan_job(service_name):
    service = get_scan_service(service_name)
    if service is None:
        raise LookupError("Unknown scan service")

    existing = _active_job()
    if existing is not None:
        raise RuntimeError("Another scan is already running")

    entries_by_key = build_entries_by_key()
    job_id = uuid.uuid4().hex
    payload = {
        "id": job_id,
        "service": service_name,
        "label": service.display_name,
        "phase": service.phase_name,
        "status": "running",
        "message": f"{service.display_name} scan underway...",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "total": len(entries_by_key),
        "scanned": 0,
        "actionable_count": 0,
        "items": [],
        "cancel_requested": False,
    }
    _write_job(job_id, payload)

    thread = threading.Thread(target=_run_scan_job, args=(job_id, service_name, list(entries_by_key.values())), daemon=True)
    with _LOCK:
        _RUNNERS[job_id] = thread
    thread.start()
    return payload


def _run_scan_job(job_id, service_name, entries):
    service = get_scan_service(service_name)
    if service is None:
        return

    try:
        total = len(entries)
        for index, entry in enumerate(entries, start=1):
            state = _read_job(job_id)
            if state is None:
                return
            if state.get("cancel_requested"):
                state["status"] = "cancelled"
                state["message"] = f"{service.display_name} scan stopped."
                state["updated_at"] = _utc_now()
                _write_job(job_id, state)
                return

            item = service.scan_entry(entry)
            state = _read_job(job_id) or {}
            items = state.get("items", [])
            if item is not None:
                items.append(item)
            state.update({
                "status": "running",
                "message": f"Scanned {index} of {total} entries.",
                "updated_at": _utc_now(),
                "scanned": index,
                "total": total,
                "items": items,
                "actionable_count": len(items),
            })
            _write_job(job_id, state)

        state = _read_job(job_id) or {}
        state["status"] = "completed"
        state["message"] = f"{service.display_name} scan finished."
        state["updated_at"] = _utc_now()
        _write_job(job_id, state)
    except Exception as exc:
        state = _read_job(job_id) or {}
        state["status"] = "failed"
        state["message"] = str(exc)
        state["updated_at"] = _utc_now()
        _write_job(job_id, state)
    finally:
        with _LOCK:
            _RUNNERS.pop(job_id, None)


def get_scan_job(job_id, cursor=0):
    state = _read_job(job_id)
    if state is None:
        return None

    items = state.get("items", [])
    cursor = max(0, int(cursor or 0))
    return {
        **state,
        "items": items[cursor:],
        "cursor": len(items),
    }


def cancel_scan_job(job_id):
    state = _read_job(job_id)
    if state is None:
        return None
    state["cancel_requested"] = True
    state["updated_at"] = _utc_now()
    _write_job(job_id, state)
    return state
