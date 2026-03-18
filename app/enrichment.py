import hashlib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html import unescape
from urllib.parse import quote, urljoin

import bibtexparser
import requests

from . import bibstore
from .latex import latex_to_text
from .metadata_store import get_entry_metadata, set_entry_flag, set_entry_provenance


SCAN_PHASE = "phase1_crossref"
SCAN_SOURCE = "crossref"
USER_AGENT = "Bibry/phase1-quality-scan (mailto:unknown@example.invalid)"
CACHE_ROOT = bibstore.BIB_DIR / "cache" / "phase1"
HTTP_TIMEOUT = 20
CACHE_TTL_SECONDS = 14 * 24 * 60 * 60
NEGATIVE_CACHE_TTL_SECONDS = 24 * 60 * 60
MIN_REQUEST_INTERVAL_SECONDS = 1.0
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 1.0

CORE_FIELDS = (
    "author",
    "title",
    "journal",
    "booktitle",
    "year",
    "volume",
    "number",
    "pages",
    "publisher",
    "doi",
    "url",
    "note",
)
REMOVABLE_FIELDS = {"journal", "booktitle"}
ARXIV_URL_RE = re.compile(r"https?://arxiv\.org/(?:abs|pdf)/([^/?#]+)", re.I)
WITHDRAWN_RE = re.compile(r"\bwithdrawn\b", re.I)
RETRACTED_RE = re.compile(r"\bretract(?:ed|ion)\b", re.I)
ISBN_SPLIT_RE = re.compile(r"[^0-9Xx]+")
XML_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
BOOK_LIKE_TYPES = {"book", "inbook", "incollection", "phdthesis", "mastersthesis", "thesis"}


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_text(value):
    return re.sub(r"\s+", " ", latex_to_text(value or "")).strip()


def _normalize_field_value(value):
    return _clean_text(value)


def _fingerprint_payload(payload):
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _entry_signature(entry):
    return {
        "type": (entry.get("ENTRYTYPE") or "").lower(),
        "fields": {
            key: value.strip()
            for key, value in sorted(entry.items())
            if key not in {"ID", "ENTRYTYPE"} and isinstance(value, str)
        },
    }


def _cache_path(kind, cache_key):
    directory = CACHE_ROOT / kind
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    return directory / f"{digest}.json"


class CachedHttpClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, application/atom+xml, application/xml;q=0.9, */*;q=0.1",
        })
        self._last_request_at = 0.0

    def _read_cache(self, path, ttl_seconds):
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        fetched_at = payload.get("fetched_at")
        if not fetched_at:
            return None
        try:
            fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        age = time.time() - fetched.timestamp()
        if age > ttl_seconds:
            return None
        return payload.get("data")

    def _write_cache(self, path, data):
        payload = {"fetched_at": _utc_now(), "data": data}
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")

    def get_json(self, kind, url, params=None):
        params = params or {}
        cache_key = json.dumps({"url": url, "params": params}, ensure_ascii=True, sort_keys=True)
        path = _cache_path(kind, cache_key)
        cached = self._read_cache(path, CACHE_TTL_SECONDS)
        if cached is not None:
            return cached
        negative_path = _cache_path(f"{kind}-errors", cache_key)
        if self._read_cache(negative_path, NEGATIVE_CACHE_TTL_SECONDS) is not None:
            return None

        last_error = None
        for attempt in range(MAX_RETRIES):
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
                time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
            try:
                response = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
                self._last_request_at = time.monotonic()
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(f"retryable status {response.status_code}", response=response)
                response.raise_for_status()
                data = response.json()
                self._write_cache(path, data)
                return data
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt == MAX_RETRIES - 1:
                    break
                time.sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))

        self._write_cache(negative_path, {"error": str(last_error)})
        return None

    def get_text(self, kind, url):
        cache_key = json.dumps({"url": url}, ensure_ascii=True, sort_keys=True)
        path = _cache_path(kind, cache_key)
        cached = self._read_cache(path, CACHE_TTL_SECONDS)
        if isinstance(cached, dict) and "text" in cached:
            return cached["text"]
        negative_path = _cache_path(f"{kind}-errors", cache_key)
        if self._read_cache(negative_path, NEGATIVE_CACHE_TTL_SECONDS) is not None:
            return None

        last_error = None
        for attempt in range(MAX_RETRIES):
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
                time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
            try:
                response = self.session.get(url, timeout=HTTP_TIMEOUT)
                self._last_request_at = time.monotonic()
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(f"retryable status {response.status_code}", response=response)
                response.raise_for_status()
                payload = {"text": response.text}
                self._write_cache(path, payload)
                return response.text
            except requests.RequestException as exc:
                last_error = exc
                if attempt == MAX_RETRIES - 1:
                    break
                time.sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))

        self._write_cache(negative_path, {"error": str(last_error)})
        return None


class CrossrefScanner:
    service_name = "crossref"
    display_name = "Crossref"
    phase_name = SCAN_PHASE

    def __init__(self):
        self.http = CachedHttpClient()

    def availability(self):
        return {"available": True, "reason": ""}

    def scan_entries(self, entries_by_key):
        actionable = []
        for key, entry in entries_by_key.items():
            item = self.scan_entry(entry)
            if item is not None:
                actionable.append(item)
        return actionable

    def scan_entry(self, entry):
        key = entry.get("ID")
        if not key:
            return None

        record, identifier_used = self._resolve_crossref(entry)
        arxiv_status = self._detect_arxiv_status(entry)
        retraction_status = self._detect_retraction_status(record)

        provenance = {
            "phase": SCAN_PHASE,
            "source": SCAN_SOURCE,
            "scanned_at": _utc_now(),
            "identifier_used": identifier_used,
            "matched": bool(record),
        }
        if record:
            provenance["crossref_doi"] = record.get("DOI") or ""
            provenance["crossref_type"] = record.get("type") or ""
        set_entry_provenance(key, SCAN_PHASE, provenance)

        set_entry_flag(key, "retracted", bool(retraction_status), retraction_status or {})
        set_entry_flag(key, "withdrawn", bool(arxiv_status), arxiv_status or {})

        if not record and not retraction_status and not arxiv_status:
            return None

        candidate_fields = self._build_candidate_fields(entry, record, retraction_status, arxiv_status)
        patch = self._build_patch(entry, candidate_fields)
        if not patch["changed_fields"]:
            return None

        suggestion_id = f"{self.phase_name}:{patch['fingerprint']}"
        metadata = get_entry_metadata(key)
        suppressed = ((metadata.get("suppressed") or {}).get(suggestion_id) or {})
        if suppressed.get("fingerprint") == patch["fingerprint"]:
            return None

        proposed_entry = self._apply_patch(entry, patch)
        proposed_raw = self._serialize_entry(proposed_entry)
        current_raw = self._serialize_entry(entry)

        return {
            "id": suggestion_id,
            "phase": self.phase_name,
            "service": self.service_name,
            "key": key,
            "title": _clean_text(entry.get("title") or candidate_fields.get("title")),
            "type": (entry.get("ENTRYTYPE") or proposed_entry.get("ENTRYTYPE") or "").lower(),
            "summary": self._summary(entry, candidate_fields),
            "source": self.display_name,
            "status_flags": patch["status_flags"],
            "basis_signature": _entry_signature(entry),
            "provenance": provenance,
            "patch": patch,
            "current_raw": current_raw.strip(),
            "proposed_raw": proposed_raw.strip(),
        }

    def _summary(self, entry, candidate_fields):
        parts = [
            _clean_text(entry.get("author") or entry.get("editor")),
            _clean_text(entry.get("year") or candidate_fields.get("year")),
            (entry.get("ENTRYTYPE") or "").lower(),
        ]
        return " • ".join([part for part in parts if part])

    def _resolve_crossref(self, entry):
        doi = _clean_text(entry.get("doi"))
        if doi:
            payload = self.http.get_json("crossref", f"https://api.crossref.org/works/{quote(doi, safe='/')}")
            message = (payload or {}).get("message") if isinstance(payload, dict) else None
            if isinstance(message, dict):
                return message, "doi"

        title = _clean_text(entry.get("title"))
        author = _clean_text(entry.get("author") or entry.get("editor"))
        year = _clean_text(entry.get("year"))
        if not title:
            return None, "none"

        params = {
            "rows": 5,
            "query.title": title,
        }
        if author:
            params["query.author"] = author
        if year.isdigit() and len(year) == 4:
            params["filter"] = f"from-pub-date:{year},until-pub-date:{year}"

        payload = self.http.get_json("crossref", "https://api.crossref.org/works", params=params)
        items = (((payload or {}).get("message") or {}).get("items") or []) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            return None, "title-author-year"

        best = None
        best_score = 0.0
        for item in items:
            score = self._crossref_match_score(entry, item)
            if score > best_score:
                best_score = score
                best = item
        if best is None or best_score < 0.72:
            return None, "title-author-year"
        return best, "title-author-year"

    def _crossref_match_score(self, entry, item):
        entry_title = _clean_text(entry.get("title")).lower()
        candidate_title = _clean_text(((item.get("title") or [""]) or [""])[0]).lower()
        title_ratio = SequenceMatcher(None, entry_title, candidate_title).ratio() if entry_title and candidate_title else 0.0

        year = _clean_text(entry.get("year"))
        candidate_year = self._extract_year(item)
        year_score = 1.0 if year and candidate_year and year == candidate_year else 0.0

        entry_author = _clean_text(entry.get("author") or entry.get("editor")).lower()
        candidate_author = _clean_text(self._authors_from_crossref(item)).lower()
        author_score = 0.0
        if entry_author and candidate_author:
            author_score = 1.0 if entry_author.split(" and ")[0][:40] in candidate_author else 0.6

        api_score = float(item.get("score") or 0.0)
        normalized_api_score = min(api_score / 100.0, 1.0)
        return (title_ratio * 0.6) + (year_score * 0.2) + (author_score * 0.1) + (normalized_api_score * 0.1)

    def _extract_year(self, item):
        for field in ("published-print", "published-online", "issued", "created"):
            date_parts = (((item.get(field) or {}).get("date-parts") or [[None]]) or [[None]])[0]
            if date_parts and date_parts[0]:
                return str(date_parts[0])
        return ""

    def _authors_from_crossref(self, item):
        authors = []
        for author in item.get("author") or []:
            family = _clean_text(author.get("family"))
            given = _clean_text(author.get("given"))
            if family and given:
                authors.append(f"{family}, {given}")
            elif family:
                authors.append(family)
            elif given:
                authors.append(given)
        return " and ".join(authors)

    def _detect_retraction_status(self, record):
        if not isinstance(record, dict):
            return None

        titles = " ".join([_clean_text(text) for text in record.get("title") or []])
        if RETRACTED_RE.search(titles):
            return {"label": "Retracted", "source": "Crossref"}

        for update in record.get("update-to") or []:
            label = _clean_text(update.get("label"))
            if RETRACTED_RE.search(label):
                return {
                    "label": "Retracted",
                    "source": "Crossref",
                    "update_label": label,
                    "doi": _clean_text(update.get("DOI")),
                }

        for assertion in record.get("assertion") or []:
            label = _clean_text(assertion.get("label"))
            value = _clean_text(assertion.get("value"))
            combined = f"{label} {value}".strip()
            if RETRACTED_RE.search(combined):
                return {"label": "Retracted", "source": "Crossref", "detail": combined}

        relation = record.get("relation") or {}
        relation_text = json.dumps(relation, ensure_ascii=True).lower()
        if "retract" in relation_text:
            return {"label": "Retracted", "source": "Crossref"}
        return None

    def _detect_arxiv_status(self, entry):
        arxiv_id = self._extract_arxiv_id(entry)
        if not arxiv_id:
            return None

        feed = self.http.get_text("arxiv", f"https://export.arxiv.org/api/query?id_list={quote(arxiv_id)}")
        if not feed:
            return None

        try:
            root = ET.fromstring(feed)
        except ET.ParseError:
            return None

        entry_node = root.find("atom:entry", XML_NS)
        if entry_node is None:
            return None

        haystacks = []
        for tag in ("atom:title", "atom:summary", "arxiv:comment"):
            node = entry_node.find(tag, XML_NS)
            if node is not None and node.text:
                haystacks.append(node.text.strip())
        combined = " ".join(haystacks)
        if WITHDRAWN_RE.search(combined):
            return {"label": "arXiv: withdrawn", "source": "arXiv", "arxiv_id": arxiv_id}
        return None

    def _extract_arxiv_id(self, entry):
        archive_prefix = _clean_text(entry.get("archiveprefix"))
        eprint = _clean_text(entry.get("eprint"))
        if archive_prefix.lower() == "arxiv" and eprint:
            return eprint

        for field in ("url", "note", "howpublished"):
            match = ARXIV_URL_RE.search(entry.get(field) or "")
            if match:
                return match.group(1).replace(".pdf", "")
        return ""

    def _build_candidate_fields(self, entry, record, retraction_status, arxiv_status):
        candidate = {}
        if isinstance(record, dict):
            candidate["author"] = self._authors_from_crossref(record)
            candidate["title"] = _clean_text(((record.get("title") or [""]) or [""])[0])
            container = _clean_text(((record.get("container-title") or [""]) or [""])[0])
            entry_type = self._infer_biblatex_type(record, entry)
            candidate["ENTRYTYPE"] = entry_type
            if entry_type == "article":
                candidate["journal"] = container
                candidate["booktitle"] = ""
            else:
                candidate["booktitle"] = container
                candidate["journal"] = ""
            candidate["year"] = self._extract_year(record)
            candidate["volume"] = _clean_text(record.get("volume"))
            candidate["number"] = _clean_text(record.get("issue"))
            candidate["pages"] = _clean_text(record.get("page"))
            candidate["publisher"] = _clean_text(record.get("publisher"))
            candidate["doi"] = _clean_text(record.get("DOI"))
            candidate["url"] = _clean_text(record.get("URL"))

        annotations = []
        existing_note = _clean_text(entry.get("note"))
        if retraction_status:
            annotations.append("Retracted")
        if arxiv_status:
            annotations.append("arXiv: withdrawn")
        if annotations:
            notes = []
            if existing_note:
                notes.append(existing_note)
            for value in annotations:
                if value not in notes:
                    notes.append(value)
            candidate["note"] = "; ".join(notes)
        return candidate

    def _infer_biblatex_type(self, record, entry):
        crossref_type = _clean_text(record.get("type")).lower()
        mapping = {
            "journal-article": "article",
            "proceedings-article": "inproceedings",
            "book-chapter": "incollection",
            "book": "book",
            "monograph": "book",
            "posted-content": "online",
            "report": "techreport",
            "dissertation": "phdthesis",
        }
        if crossref_type in mapping:
            return mapping[crossref_type]
        return (entry.get("ENTRYTYPE") or "misc").lower()

    def _build_patch(self, entry, candidate_fields):
        tracked_fields = set(CORE_FIELDS)
        if candidate_fields.get("journal"):
            tracked_fields.add("booktitle")
        if candidate_fields.get("booktitle"):
            tracked_fields.add("journal")

        changes = []
        patch_fields = {}
        removals = []
        for field in sorted(tracked_fields):
            current = _normalize_field_value(entry.get(field))
            target = _normalize_field_value(candidate_fields.get(field))
            if current == target:
                continue
            if target:
                patch_fields[field] = target
                changes.append({
                    "field": field,
                    "action": "add" if not current else "change",
                    "before": current,
                    "after": target,
                })
            elif current and field in REMOVABLE_FIELDS:
                patch_fields[field] = ""
                removals.append(field)
                changes.append({
                    "field": field,
                    "action": "remove",
                    "before": current,
                    "after": "",
                })

        status_flags = []
        note_text = _normalize_field_value(candidate_fields.get("note"))
        if "Retracted" in note_text:
            status_flags.append("retracted")
        if "arXiv: withdrawn" in note_text:
            status_flags.append("withdrawn")

        fingerprint = _fingerprint_payload({
            "key": entry.get("ID"),
            "changes": changes,
            "status_flags": status_flags,
        })
        return {
            "fields": patch_fields,
            "removed_fields": removals,
            "changed_fields": changes,
            "status_flags": status_flags,
            "fingerprint": fingerprint,
        }

    def _apply_patch(self, entry, patch):
        updated = dict(entry)
        for field, value in patch.get("fields", {}).items():
            if value:
                updated[field] = value
            else:
                updated.pop(field, None)
        return updated

    def _serialize_entry(self, entry):
        db = bibtexparser.bibdatabase.BibDatabase()
        db.entries = [entry]
        writer = bibtexparser.bwriter.BibTexWriter()
        return writer.write(db)


def build_entries_by_key():
    db = bibstore.load_bib()
    return {
        entry.get("ID"): entry
        for entry in db.entries
        if entry.get("ID")
    }


def _normalize_isbn(value):
    parts = ISBN_SPLIT_RE.split(_clean_text(value).upper())
    for part in parts:
        if len(part) in {10, 13}:
            return part
    return ""


def _html_title(text):
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    return _clean_text(unescape(match.group(1))) if match else ""


def _meta_content(text, name):
    match = re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\'](.*?)["\']', text, re.I | re.S)
    return _clean_text(unescape(match.group(1))) if match else ""


def _first_match(text, pattern):
    match = re.search(pattern, text, re.I | re.S)
    return _clean_text(unescape(match.group(1))) if match else ""


class WorldCatScanner:
    service_name = "worldcat"
    display_name = "WorldCat"
    phase_name = "phase2_worldcat"
    base_url = "https://search.worldcat.org"

    def __init__(self):
        self.http = CachedHttpClient()

    def availability(self):
        return {
            "available": True,
            "reason": "Uses public WorldCat catalog pages; official OCLC API credentials are not configured in this app.",
        }

    def scan_entries(self, entries_by_key):
        actionable = []
        for key, entry in entries_by_key.items():
            item = self.scan_entry(entry)
            if item is not None:
                actionable.append(item)
        return actionable

    def scan_entry(self, entry):
        entry_type = (entry.get("ENTRYTYPE") or "").lower()
        if entry_type not in BOOK_LIKE_TYPES:
            return None

        record, identifier_used = self._resolve_worldcat(entry)
        if not record:
            set_entry_provenance(entry.get("ID"), self.phase_name, {
                "phase": self.phase_name,
                "source": self.service_name,
                "scanned_at": _utc_now(),
                "identifier_used": identifier_used,
                "matched": False,
            })
            return None

        candidate_fields = self._build_candidate_fields(entry, record)
        patch = self._build_patch(entry, candidate_fields)
        if not patch["changed_fields"]:
            return None

        key = entry.get("ID")
        provenance = {
            "phase": self.phase_name,
            "source": self.service_name,
            "scanned_at": _utc_now(),
            "identifier_used": identifier_used,
            "matched": True,
            "worldcat_id": record.get("oclc_number") or "",
            "record_url": record.get("url") or "",
        }
        set_entry_provenance(key, self.phase_name, provenance)

        suggestion_id = f"{self.phase_name}:{patch['fingerprint']}"
        metadata = get_entry_metadata(key)
        suppressed = ((metadata.get("suppressed") or {}).get(suggestion_id) or {})
        if suppressed.get("fingerprint") == patch["fingerprint"]:
            return None

        proposed_entry = self._apply_patch(entry, patch)
        return {
            "id": suggestion_id,
            "phase": self.phase_name,
            "service": self.service_name,
            "key": key,
            "title": _clean_text(entry.get("title") or candidate_fields.get("title")),
            "type": entry_type,
            "summary": " • ".join(filter(None, [
                _clean_text(entry.get("author") or entry.get("editor")),
                _clean_text(entry.get("year") or candidate_fields.get("year")),
                entry_type,
            ])),
            "source": self.display_name,
            "status_flags": [],
            "basis_signature": _entry_signature(entry),
            "provenance": provenance,
            "patch": patch,
            "current_raw": self._serialize_entry(entry).strip(),
            "proposed_raw": self._serialize_entry(proposed_entry).strip(),
        }

    def _resolve_worldcat(self, entry):
        isbn = _normalize_isbn(entry.get("isbn"))
        if isbn:
            record = self._fetch_worldcat_record(f"https://www.worldcat.org/isbn/{quote(isbn)}")
            if record:
                return record, "isbn"

        title = _clean_text(entry.get("title"))
        author = _clean_text(entry.get("author") or entry.get("editor"))
        if not title:
            return None, "none"
        query = " ".join(filter(None, [title, author]))
        search_url = f"{self.base_url}/search?q={quote(query)}"
        search_html = self.http.get_text("worldcat-search", search_url)
        if not search_html:
            return None, "title-author"

        match = re.search(r'href=["\'](?P<href>/title/[^"\']+)["\']', search_html, re.I)
        if not match:
            return None, "title-author"
        href = match.group("href")
        record = self._fetch_worldcat_record(urljoin(self.base_url, href))
        if not record:
            return None, "title-author"
        return record, "title-author"

    def _fetch_worldcat_record(self, url):
        html = self.http.get_text("worldcat-record", url)
        if not html:
            return None

        title = (
            _meta_content(html, "og:title")
            or _first_match(html, r"<h1[^>]*>(.*?)</h1>")
            or _html_title(html)
        )
        if not title:
            return None

        author = (
            _meta_content(html, "og:description")
            or _first_match(html, r"Author:\s*([^<\n]+)")
        )
        publisher_line = _first_match(html, r"Publisher:\s*([^<\n]+)")
        edition = _first_match(html, r"Edition:\s*([^<\n]+)")
        isbn_line = _first_match(html, r"ISBN:\s*([^<\n]+)") or _first_match(html, r"ISBNs?:\s*([^<\n]+)")
        oclc_number = _first_match(html, r"OCLC Number / Unique Identifier:\s*([0-9]+)") or _first_match(html, r'"oclcNumber"\s*:\s*"([0-9]+)"')

        publisher = ""
        year = ""
        if publisher_line:
            parts = [part.strip() for part in publisher_line.split(",") if part.strip()]
            if parts:
                publisher = parts[0]
                for part in reversed(parts):
                    year_match = re.search(r"(1[5-9]\d{2}|20\d{2}|2100)", part)
                    if year_match:
                        year = year_match.group(1)
                        break

        isbns = []
        for part in ISBN_SPLIT_RE.split(isbn_line.upper()):
            if len(part) in {10, 13}:
                isbns.append(part)

        return {
            "url": url,
            "oclc_number": oclc_number,
            "title": title,
            "author": author,
            "publisher": publisher,
            "year": year,
            "edition": edition,
            "isbns": isbns,
        }

    def _build_candidate_fields(self, entry, record):
        candidate = {}
        isbn = _normalize_isbn(entry.get("isbn"))
        record_isbn = next((value for value in record.get("isbns") or [] if value), "")
        if record_isbn:
            candidate["isbn"] = record_isbn

        title = _clean_text(record.get("title"))
        current_title = _clean_text(entry.get("title"))
        if not current_title or SequenceMatcher(None, current_title.lower(), title.lower()).ratio() >= 0.88:
            candidate["title"] = title

        publisher = _clean_text(record.get("publisher"))
        current_publisher = _clean_text(entry.get("publisher"))
        if publisher and (
            not current_publisher
            or publisher.lower() in current_publisher.lower()
            or current_publisher.lower() in publisher.lower()
            or SequenceMatcher(None, current_publisher.lower(), publisher.lower()).ratio() >= 0.75
        ):
            candidate["publisher"] = publisher

        year = _clean_text(record.get("year"))
        current_year = _clean_text(entry.get("year"))
        if year and (not current_year or not re.fullmatch(r"\d{4}", current_year)):
            candidate["year"] = year

        edition = _clean_text(record.get("edition"))
        current_edition = _clean_text(entry.get("edition"))
        if edition and (
            not current_edition
            or edition.lower() in current_edition.lower()
            or current_edition.lower() in edition.lower()
        ):
            candidate["edition"] = edition

        if not isbn and record_isbn:
            candidate["isbn"] = record_isbn
        return candidate

    def _build_patch(self, entry, candidate_fields):
        tracked_fields = ("isbn", "publisher", "year", "title", "edition")
        changes = []
        patch_fields = {}
        for field in tracked_fields:
            current = _normalize_field_value(entry.get(field))
            target = _normalize_field_value(candidate_fields.get(field))
            if not target or current == target:
                continue
            changes.append({
                "field": field,
                "action": "add" if not current else "change",
                "before": current,
                "after": target,
            })
            patch_fields[field] = target

        fingerprint = _fingerprint_payload({
            "key": entry.get("ID"),
            "service": self.service_name,
            "changes": changes,
        })
        return {
            "fields": patch_fields,
            "removed_fields": [],
            "changed_fields": changes,
            "status_flags": [],
            "fingerprint": fingerprint,
        }

    def _apply_patch(self, entry, patch):
        updated = dict(entry)
        for field, value in patch.get("fields", {}).items():
            updated[field] = value
        return updated

    def _serialize_entry(self, entry):
        db = bibtexparser.bibdatabase.BibDatabase()
        db.entries = [entry]
        writer = bibtexparser.bwriter.BibTexWriter()
        return writer.write(db)


SCAN_SERVICES = {
    CrossrefScanner.service_name: CrossrefScanner(),
    WorldCatScanner.service_name: WorldCatScanner(),
}


def get_scan_service(name):
    return SCAN_SERVICES.get(name)


def list_scan_services():
    items = []
    for name, service in SCAN_SERVICES.items():
        availability = service.availability()
        items.append({
            "name": name,
            "label": service.display_name,
            "phase": service.phase_name,
            "available": bool(availability.get("available")),
            "reason": availability.get("reason") or "",
        })
    return items
