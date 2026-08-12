"""SQLite-backed catalogue for shared BibLaTeX entries.

The catalogue deliberately keeps bibliography membership separate from entry
identity.  Citation keys are membership-local; an entry may be referenced by
many bibliography files and a work may have several entry variants.
"""
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import bibtexparser

from .sort_dedupe_bibtex import process_bibtex_text


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _doi(value):
    value = _clean(value)
    return re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", value)


def _identity(entry):
    doi = _doi(entry.get("doi"))
    if doi:
        return "doi:" + doi
    eprint = _clean(entry.get("eprint"))
    archive = _clean(entry.get("archiveprefix") or entry.get("eprinttype"))
    if eprint and (not archive or "arxiv" in archive):
        return "arxiv:" + eprint.removeprefix("arxiv:")
    title = _clean(entry.get("title"))
    year = _clean(entry.get("year") or entry.get("date"))[:4]
    author = _clean(entry.get("author") or entry.get("editor")).split(" and ")[0]
    if title and year and author:
        return "titleyear:" + hashlib.sha256(f"{title}|{author}|{year}".encode()).hexdigest()
    return None


def _signature(entry):
    values = {k.lower(): str(v).strip() for k, v in entry.items()
              if k not in {"ID", "ENTRYTYPE"}}
    payload = (str(entry.get("ENTRYTYPE", "")).lower(),
               tuple(sorted(values.items())))
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


class LibraryStore:
    def __init__(self, root, bib_dir):
        self.root = Path(root)
        self.bib_dir = Path(bib_dir)
        self.path = self.bib_dir / "library.sqlite3"
        self.bib_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def session(self):
        db = self.connect()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _initialize(self):
        with self.session() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS bibliographies (
              id INTEGER PRIMARY KEY, filename TEXT UNIQUE NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS works (
              id INTEGER PRIMARY KEY, identity TEXT UNIQUE,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entries (
              id INTEGER PRIMARY KEY, work_id INTEGER REFERENCES works(id),
              entry_type TEXT NOT NULL, fields TEXT NOT NULL,
              signature TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bibliography_entries (
              bibliography_id INTEGER NOT NULL REFERENCES bibliographies(id) ON DELETE CASCADE,
              entry_id INTEGER NOT NULL REFERENCES entries(id),
              citation_key TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (bibliography_id, citation_key),
              UNIQUE (bibliography_id, entry_id, citation_key)
            );
            CREATE INDEX IF NOT EXISTS idx_entries_work ON entries(work_id);
            CREATE INDEX IF NOT EXISTS idx_membership_entry ON bibliography_entries(entry_id);
            """)

    @staticmethod
    def _encode(entry):
        db = bibtexparser.bibdatabase.BibDatabase()
        db.entries = [entry]
        return bibtexparser.bwriter.BibTexWriter().write(db).strip()

    @staticmethod
    def _decode(raw, key, entry_type):
        parsed = bibtexparser.loads(raw)
        entry = parsed.entries[0] if parsed.entries else {}
        entry["ID"] = key
        entry["ENTRYTYPE"] = entry_type
        return entry

    def _bibliography(self, db, filename):
        now = _utc_now()
        row = db.execute("SELECT id FROM bibliographies WHERE filename=?", (filename,)).fetchone()
        if row:
            db.execute("UPDATE bibliographies SET updated_at=? WHERE id=?", (now, row["id"]))
            return row["id"]
        cur = db.execute("INSERT INTO bibliographies(filename,created_at,updated_at) VALUES(?,?,?)",
                         (filename, now, now))
        return cur.lastrowid

    def _entry_id(self, db, entry):
        sig = _signature(entry)
        row = db.execute("SELECT id FROM entries WHERE signature=?", (sig,)).fetchone()
        if row:
            return row["id"]
        identity = _identity(entry)
        work_id = None
        if identity:
            row = db.execute("SELECT id FROM works WHERE identity=?", (identity,)).fetchone()
            if row:
                work_id = row["id"]
            else:
                work_id = db.execute("INSERT INTO works(identity,created_at) VALUES(?,?)",
                                     (identity, _utc_now())).lastrowid
        raw = self._encode(entry)
        cur = db.execute("""INSERT INTO entries(work_id,entry_type,fields,signature,created_at,updated_at)
                           VALUES(?,?,?,?,?,?)""",
                         (work_id, entry.get("ENTRYTYPE", "misc"), raw, sig, _utc_now(), _utc_now()))
        return cur.lastrowid

    def import_file(self, filename, entries):
        with self.session() as db:
            bibliography_id = self._bibliography(db, filename)
            db.execute("DELETE FROM bibliography_entries WHERE bibliography_id=?", (bibliography_id,))
            for position, entry in enumerate(entries):
                key = entry.get("ID")
                if not key:
                    continue
                entry_id = self._entry_id(db, entry)
                db.execute("""INSERT INTO bibliography_entries
                    (bibliography_id,entry_id,citation_key,position) VALUES(?,?,?,?)""",
                           (bibliography_id, entry_id, key, position))

    def has_bibliographies(self):
        with self.session() as db:
            return db.execute("SELECT 1 FROM bibliographies LIMIT 1").fetchone() is not None

    def entries_for(self, filename):
        with self.session() as db:
            rows = db.execute("""SELECT e.fields, e.entry_type, m.citation_key, m.position
                FROM bibliography_entries m JOIN bibliographies b ON b.id=m.bibliography_id
                JOIN entries e ON e.id=m.entry_id WHERE b.filename=? ORDER BY m.position, m.citation_key""",
                              (filename,)).fetchall()
        result = []
        for row in rows:
            result.append(self._decode(row["fields"], row["citation_key"], row["entry_type"]))
        return result

    def memberships_for(self, filename):
        with self.session() as db:
            rows = db.execute("""SELECT m.citation_key, e.id entry_id, e.work_id, e.signature
              FROM bibliography_entries m JOIN bibliographies b ON b.id=m.bibliography_id
              JOIN entries e ON e.id=m.entry_id WHERE b.filename=?""", (filename,)).fetchall()
        return {row["citation_key"]: dict(row) for row in rows}

    def all_entries_with_memberships(self):
        with self.session() as db:
            rows = db.execute("""SELECT e.fields, e.entry_type, e.id entry_id,
              b.filename, m.citation_key
              FROM entries e
              JOIN bibliography_entries m ON m.entry_id=e.id
              JOIN bibliographies b ON b.id=m.bibliography_id
              ORDER BY m.citation_key, b.filename""").fetchall()
        result = []
        for row in rows:
            key = row["citation_key"]
            result.append({"raw": row["fields"], "key": key,
                           "entry_type": row["entry_type"],
                           "entry_id": row["entry_id"], "filename": row["filename"]})
        return result

    def save_entries(self, filename, entries):
        self.import_file(filename, entries)

    def refresh_projection(self, filename, path):
        entries = self.entries_for(filename)
        db = bibtexparser.bibdatabase.BibDatabase()
        db.entries = entries
        text = bibtexparser.bwriter.BibTexWriter().write(db) if entries else ""
        path.write_text(text, encoding="utf-8")

    def migrate_existing(self, paths):
        for path in sorted(paths):
            try:
                with path.open(encoding="utf-8") as handle:
                    parsed = bibtexparser.load(handle)
                self.import_file(path.name, parsed.entries)
            except (OSError, ValueError):
                continue

    def membership_info(self, filename, key):
        with self.session() as db:
            row = db.execute("""SELECT e.id entry_id, e.work_id, e.signature
              FROM bibliography_entries m JOIN bibliographies b ON b.id=m.bibliography_id
              JOIN entries e ON e.id=m.entry_id WHERE b.filename=? AND m.citation_key=?""",
                             (filename, key)).fetchone()
            return dict(row) if row else None

    def variants_for(self, filename, key):
        with self.session() as db:
            row = db.execute("""SELECT e.work_id FROM bibliography_entries m
              JOIN bibliographies b ON b.id=m.bibliography_id
              JOIN entries e ON e.id=m.entry_id
              WHERE b.filename=? AND m.citation_key=?""", (filename, key)).fetchone()
            if not row or row["work_id"] is None:
                return []
            rows = db.execute("""SELECT e.id, e.fields, e.entry_type, e.signature,
              COUNT(m2.bibliography_id) references_count
              FROM entries e LEFT JOIN bibliography_entries m2 ON m2.entry_id=e.id
              WHERE e.work_id=? GROUP BY e.id ORDER BY e.id""", (row["work_id"],)).fetchall()
        return [{"entry_id": r["id"], "raw": r["fields"], "type": r["entry_type"],
                 "signature": r["signature"], "references_count": r["references_count"]}
                for r in rows]

    def share(self, source_filename, key, target_filename, target_key=None):
        with self.session() as db:
            source = db.execute("""SELECT e.id, e.entry_type FROM bibliography_entries m
              JOIN bibliographies b ON b.id=m.bibliography_id JOIN entries e ON e.id=m.entry_id
              WHERE b.filename=? AND m.citation_key=?""", (source_filename, key)).fetchone()
            if not source:
                raise KeyError(key)
            target_key = target_key or key
            target_id = self._bibliography(db, target_filename)
            db.execute("DELETE FROM bibliography_entries WHERE bibliography_id=? AND citation_key=?",
                       (target_id, target_key))
            position = db.execute("SELECT COALESCE(MAX(position), -1)+1 FROM bibliography_entries WHERE bibliography_id=?",
                                 (target_id,)).fetchone()[0]
            db.execute("INSERT INTO bibliography_entries(bibliography_id,entry_id,citation_key,position) VALUES(?,?,?,?)",
                       (target_id, source["id"], target_key, position))
        return target_key
