"""SQLite-backed catalogue for shared BibLaTeX entries.

Each entry owns one catalogue-wide citation key.  Bibliography membership is
separate from entry identity, but always projects that canonical key so PDFs
named after citation keys remain unambiguous.
"""
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import bibtexparser

from .biblatex import load as load_biblatex_file, loads as load_biblatex
from .latex import latex_to_text
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
            columns = {row["name"] for row in db.execute("PRAGMA table_info(entries)")}
            if "canonical_key" not in columns:
                db.execute("ALTER TABLE entries ADD COLUMN canonical_key TEXT")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_canonical_key
                          ON entries(canonical_key COLLATE NOCASE)
                          WHERE canonical_key IS NOT NULL""")
            self._adopt_unambiguous_canonical_keys(db)

    @staticmethod
    def _encode(entry):
        db = bibtexparser.bibdatabase.BibDatabase()
        db.entries = [entry]
        return bibtexparser.bwriter.BibTexWriter().write(db).strip()

    @staticmethod
    def _decode(raw, key, entry_type):
        parsed = load_biblatex(raw)
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

    @staticmethod
    def _key_token(value):
        token = re.sub(r"[^A-Za-z0-9]+", "", latex_to_text(value or ""))
        return token[:1].upper() + token[1:] if token else ""

    @classmethod
    def _key_base(cls, entry):
        """Derive Bibry's key from bibliographic content, never source ID."""
        people = latex_to_text(entry.get("author") or entry.get("editor") or "")
        first = re.split(r"\s+and\s+", people, maxsplit=1)[0].strip()
        if "," in first:
            surname = first.split(",", 1)[0].strip()
        else:
            parts = first.split()
            surname = parts[-1] if parts else ""
        year_match = re.search(r"\d{4}", latex_to_text(entry.get("year") or entry.get("date") or ""))
        surname_token = cls._key_token(surname)
        year = year_match.group(0) if year_match else ""
        return f"{surname_token}{year}" or surname_token or year or "Entry"

    def _available_canonical_key(self, db, desired, entry_id=None):
        desired = self._key_token(desired) or "Entry"
        candidate = desired
        suffix_index = 0
        while True:
            row = db.execute("SELECT id FROM entries WHERE canonical_key=? COLLATE NOCASE", (candidate,)).fetchone()
            if not row or row["id"] == entry_id:
                return candidate
            suffix = ""
            value = suffix_index
            while True:
                suffix = chr(ord("a") + value % 26) + suffix
                value = value // 26 - 1
                if value < 0:
                    break
            candidate = f"{desired}{suffix}"
            suffix_index += 1

    def _adopt_unambiguous_canonical_keys(self, db):
        """Backfill old catalogues without guessing collision ownership."""
        rows = db.execute("""SELECT e.id, GROUP_CONCAT(DISTINCT m.citation_key) keys
            FROM entries e LEFT JOIN bibliography_entries m ON m.entry_id=e.id
            WHERE e.canonical_key IS NULL GROUP BY e.id""").fetchall()
        for row in rows:
            keys = [key for key in (row["keys"] or "").split(",") if key]
            if len(keys) != 1:
                continue
            owner = db.execute("SELECT id FROM entries WHERE canonical_key=? COLLATE NOCASE", (keys[0],)).fetchone()
            if owner and owner["id"] != row["id"]:
                continue
            # Do not claim a key still used by a different legacy entry.
            other = db.execute("""SELECT DISTINCT entry_id FROM bibliography_entries
                WHERE citation_key=? COLLATE NOCASE AND entry_id<>?""", (keys[0], row["id"])).fetchone()
            if not other:
                db.execute("UPDATE entries SET canonical_key=? WHERE id=?", (keys[0], row["id"]))

    def _entry_id(self, db, entry):
        sig = _signature(entry)
        row = db.execute("SELECT id, canonical_key FROM entries WHERE signature=?", (sig,)).fetchone()
        if row:
            canonical_key = row["canonical_key"] or self._available_canonical_key(db, self._key_base(entry), row["id"])
            if not row["canonical_key"]:
                db.execute("UPDATE entries SET canonical_key=? WHERE id=?", (canonical_key, row["id"]))
            return row["id"], canonical_key
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
        canonical_key = self._available_canonical_key(db, self._key_base(entry))
        cur = db.execute("""INSERT INTO entries(work_id,entry_type,fields,signature,canonical_key,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?)""",
                         (work_id, entry.get("ENTRYTYPE", "misc"), raw, sig, canonical_key, _utc_now(), _utc_now()))
        return cur.lastrowid, canonical_key

    def import_file(self, filename, entries):
        normalized = []
        with self.session() as db:
            bibliography_id = self._bibliography(db, filename)
            db.execute("DELETE FROM bibliography_entries WHERE bibliography_id=?", (bibliography_id,))
            seen_entry_ids = set()
            for entry in entries:
                key = entry.get("ID")
                if not key:
                    continue
                entry_id, canonical_key = self._entry_id(db, entry)
                if entry_id in seen_entry_ids:
                    continue
                seen_entry_ids.add(entry_id)
                db.execute("""INSERT INTO bibliography_entries
                    (bibliography_id,entry_id,citation_key,position) VALUES(?,?,?,?)""",
                           (bibliography_id, entry_id, canonical_key, len(normalized)))
                normalized.append({**entry, "ID": canonical_key})
        return normalized

    def has_bibliographies(self):
        with self.session() as db:
            return db.execute("SELECT 1 FROM bibliographies LIMIT 1").fetchone() is not None

    def entries_for(self, filename):
        with self.session() as db:
            rows = db.execute("""SELECT e.fields, e.entry_type, e.canonical_key, m.citation_key, m.position
                FROM bibliography_entries m JOIN bibliographies b ON b.id=m.bibliography_id
                JOIN entries e ON e.id=m.entry_id WHERE b.filename=? ORDER BY m.position, m.citation_key""",
                              (filename,)).fetchall()
        result = []
        for row in rows:
            result.append(self._decode(row["fields"], row["canonical_key"] or row["citation_key"], row["entry_type"]))
        return result

    def bibliography_page(self, filename, limit=10, offset=0):
        limit = max(1, min(int(limit or 10), 100))
        offset = max(0, int(offset or 0))
        with self.session() as db:
            total = db.execute("""SELECT COUNT(*) FROM bibliography_entries m
              JOIN bibliographies b ON b.id=m.bibliography_id WHERE b.filename=?""", (filename,)).fetchone()[0]
            rows = db.execute("""SELECT e.fields, e.entry_type, e.canonical_key, e.id entry_id,
              e.work_id, e.created_at, e.updated_at, m.citation_key, m.position
              FROM bibliography_entries m JOIN bibliographies b ON b.id=m.bibliography_id
              JOIN entries e ON e.id=m.entry_id WHERE b.filename=?
              ORDER BY m.position, m.citation_key LIMIT ? OFFSET ?""",
                              (filename, limit, offset)).fetchall()
        return rows, total

    def database_page(self, limit=10, offset=0):
        limit = max(1, min(int(limit or 10), 100))
        offset = max(0, int(offset or 0))
        with self.session() as db:
            total = db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            rows = db.execute("""SELECT e.fields, e.entry_type, e.canonical_key, e.id entry_id,
              e.work_id, e.created_at, e.updated_at, COUNT(m.bibliography_id) reference_count
              FROM entries e LEFT JOIN bibliography_entries m ON m.entry_id=e.id
              GROUP BY e.id ORDER BY e.id LIMIT ? OFFSET ?""", (limit, offset)).fetchall()
        return rows, total

    def search_entries(self, query, filename, limit=25, offset=0):
        """Find catalogue entries without materialising the whole library."""
        query = str(query or "").strip()
        limit = max(1, min(int(limit or 25), 50))
        offset = max(0, int(offset or 0))
        if not query:
            return [], 0

        # Treat user input literally; '%' and '_' must not become LIKE wildcards.
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        where = """(e.fields LIKE ? ESCAPE '\\' COLLATE NOCASE OR EXISTS (
            SELECT 1 FROM bibliography_entries search_m
            WHERE search_m.entry_id=e.id AND search_m.citation_key LIKE ? ESCAPE '\\' COLLATE NOCASE
        ))"""
        with self.session() as db:
            total = db.execute(f"SELECT COUNT(*) FROM entries e WHERE {where}", (pattern, pattern)).fetchone()[0]
            rows = db.execute(f"""SELECT e.id entry_id, e.fields, e.entry_type, e.canonical_key, e.work_id,
                e.created_at, e.updated_at,
                (SELECT COUNT(*) FROM bibliography_entries refs WHERE refs.entry_id=e.id) reference_count,
                (SELECT current_m.citation_key FROM bibliography_entries current_m
                  JOIN bibliographies current_b ON current_b.id=current_m.bibliography_id
                  WHERE current_m.entry_id=e.id AND current_b.filename=? LIMIT 1) current_key
                FROM entries e WHERE {where}
                ORDER BY e.id DESC LIMIT ? OFFSET ?""",
                (filename, pattern, pattern, limit, offset)).fetchall()
        return rows, total

    def entry_for_id(self, entry_id, key):
        with self.session() as db:
            row = db.execute("SELECT fields, entry_type FROM entries WHERE id=?", (int(entry_id),)).fetchone()
        if not row:
            return None
        return self._decode(row["fields"], key, row["entry_type"])

    def canonical_key_for_entry(self, entry_id):
        with self.session() as db:
            row = db.execute("SELECT canonical_key, fields FROM entries WHERE id=?", (int(entry_id),)).fetchone()
            if not row:
                return None
            if row["canonical_key"]:
                return row["canonical_key"]
            membership = db.execute("SELECT citation_key FROM bibliography_entries WHERE entry_id=? LIMIT 1", (int(entry_id),)).fetchone()
            parsed = self._decode(row["fields"], membership["citation_key"] if membership else "Entry", "misc")
            key = self._available_canonical_key(db, self._key_base(parsed), int(entry_id))
            db.execute("UPDATE entries SET canonical_key=? WHERE id=?", (key, int(entry_id)))
            return key

    def bibliography_stats(self):
        with self.session() as db:
            rows = db.execute("""SELECT b.filename, b.created_at, b.updated_at,
              COUNT(m.citation_key) entry_count
              FROM bibliographies b LEFT JOIN bibliography_entries m ON m.bibliography_id=b.id
              GROUP BY b.id ORDER BY b.filename""").fetchall()
        return [dict(row) for row in rows]

    def database_stats(self):
        with self.session() as db:
            row = db.execute("SELECT COUNT(*) entry_count, MIN(created_at) created_at, MAX(updated_at) updated_at FROM entries").fetchone()
        return dict(row)

    def create_bibliography(self, filename):
        with self.session() as db:
            if db.execute("SELECT 1 FROM bibliographies WHERE filename=?", (filename,)).fetchone():
                raise FileExistsError(filename)
            self._bibliography(db, filename)

    def delete_bibliography(self, filename):
        with self.session() as db:
            row = db.execute("SELECT id FROM bibliographies WHERE filename=?", (filename,)).fetchone()
            if not row:
                raise FileNotFoundError(filename)
            count = db.execute("SELECT COUNT(*) FROM bibliographies").fetchone()[0]
            if count <= 1:
                raise ValueError("The final bibliography cannot be deleted")
            db.execute("DELETE FROM bibliographies WHERE id=?", (row["id"],))

    def delete_orphan_entries(self, entry_ids):
        entry_ids = [int(value) for value in entry_ids]
        if not entry_ids:
            return 0
        with self.session() as db:
            placeholders = ",".join("?" for _ in entry_ids)
            rows = db.execute(f"""SELECT e.id FROM entries e
              LEFT JOIN bibliography_entries m ON m.entry_id=e.id
              WHERE e.id IN ({placeholders}) GROUP BY e.id HAVING COUNT(m.bibliography_id)=0""", entry_ids).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                marks = ",".join("?" for _ in ids)
                db.execute(f"DELETE FROM entries WHERE id IN ({marks})", ids)
        return len(ids)

    def delete_orphan_entry(self, entry_id):
        return self.delete_orphan_entries([entry_id])

    def orphan_entries(self):
        with self.session() as db:
            return db.execute("""SELECT e.id entry_id, e.work_id, e.fields, e.entry_type,
              e.created_at, e.updated_at FROM entries e
              LEFT JOIN bibliography_entries m ON m.entry_id=e.id
              GROUP BY e.id HAVING COUNT(m.bibliography_id)=0 ORDER BY e.id""").fetchall()

    def key_integrity_issues(self):
        """Return legacy aliases/collisions requiring an explicit repair."""
        with self.session() as db:
            rows = db.execute("""SELECT e.id entry_id, e.fields, e.entry_type, e.canonical_key,
                GROUP_CONCAT(DISTINCT m.citation_key) keys
                FROM entries e LEFT JOIN bibliography_entries m ON m.entry_id=e.id
                GROUP BY e.id
                ORDER BY e.id""").fetchall()
            issues = []
            for row in rows:
                keys = [key for key in (row["keys"] or "").split(",") if key]
                if not keys:
                    continue
                entry = self._decode(row["fields"], "", row["entry_type"])
                expected_base = self._key_base(entry)
                if row["canonical_key"] and re.fullmatch(rf"{re.escape(expected_base)}[a-z]*", row["canonical_key"]):
                    continue
                proposed = self._available_canonical_key(db, expected_base, row["entry_id"])
                pdf_keys = [key for key in keys if (self.root / "pdf" / f"{key}.pdf").exists()]
                issues.append({"entry_id": row["entry_id"], "fields": row["fields"],
                               "entry_type": row["entry_type"], "keys": keys,
                               "proposed_key": proposed, "pdf_keys": pdf_keys})
        return issues

    def duplicate_variant_groups(self):
        """Return review candidates which resolve to the same bibliographic work.

        A shared work identity is deliberately only a candidate signal: variants can
        be intentional.  Callers must therefore present these groups for an explicit
        survivor choice rather than merging them automatically.
        """
        with self.session() as db:
            groups = db.execute("""SELECT e.work_id, w.identity
                FROM entries e JOIN works w ON w.id=e.work_id
                GROUP BY e.work_id HAVING COUNT(*) > 1 ORDER BY e.work_id""").fetchall()
            result = []
            for group in groups:
                rows = db.execute("""SELECT e.id entry_id, e.fields, e.entry_type,
                    e.canonical_key, e.created_at, e.updated_at,
                    COUNT(m.bibliography_id) reference_count
                    FROM entries e LEFT JOIN bibliography_entries m ON m.entry_id=e.id
                    WHERE e.work_id=? GROUP BY e.id
                    ORDER BY reference_count DESC, e.updated_at DESC, e.id""",
                                  (group["work_id"],)).fetchall()
                result.append({"work_id": group["work_id"], "identity": group["identity"],
                               "variants": [dict(row) for row in rows]})
        return result

    def merge_variant_entries(self, keep_entry_id, merge_entry_ids):
        """Merge reviewed same-work variants into the selected surviving entry."""
        keep_entry_id = int(keep_entry_id)
        merge_entry_ids = list(dict.fromkeys(int(entry_id) for entry_id in merge_entry_ids
                                              if int(entry_id) != keep_entry_id))
        if not merge_entry_ids:
            raise ValueError("Choose at least one different variant to merge")
        placeholders = ",".join("?" for _ in merge_entry_ids)
        with self.session() as db:
            keeper = db.execute("SELECT id, work_id, canonical_key FROM entries WHERE id=?",
                                (keep_entry_id,)).fetchone()
            if not keeper:
                raise KeyError("Surviving database entry not found")
            if keeper["work_id"] is None:
                raise ValueError("This entry has no shared work identity")
            variants = db.execute(f"SELECT id, work_id FROM entries WHERE id IN ({placeholders})",
                                  merge_entry_ids).fetchall()
            if len(variants) != len(merge_entry_ids) or any(row["work_id"] != keeper["work_id"] for row in variants):
                raise ValueError("Only variants from the same reviewed work can be merged")
            if not keeper["canonical_key"]:
                raise ValueError("Repair this entry's citation key before merging variants")

            memberships = db.execute(f"""SELECT m.bibliography_id, m.entry_id, b.filename
                FROM bibliography_entries m JOIN bibliographies b ON b.id=m.bibliography_id
                WHERE m.entry_id IN ({placeholders})""", merge_entry_ids).fetchall()
            filenames = set()
            for membership in memberships:
                bibliography_id = membership["bibliography_id"]
                filename = membership["filename"]
                conflict = db.execute("""SELECT entry_id FROM bibliography_entries
                    WHERE bibliography_id=? AND citation_key=? COLLATE NOCASE AND entry_id NOT IN (?, ?)""",
                                      (bibliography_id, keeper["canonical_key"], keep_entry_id,
                                       membership["entry_id"])).fetchone()
                if conflict:
                    raise ValueError(f"Cannot merge: {filename} already uses {keeper['canonical_key']}")
                existing = db.execute("""SELECT 1 FROM bibliography_entries
                    WHERE bibliography_id=? AND entry_id=?""", (bibliography_id, keep_entry_id)).fetchone()
                if existing:
                    db.execute("DELETE FROM bibliography_entries WHERE bibliography_id=? AND entry_id=?",
                               (bibliography_id, membership["entry_id"]))
                else:
                    db.execute("""UPDATE bibliography_entries SET entry_id=?, citation_key=?
                        WHERE bibliography_id=? AND entry_id=?""",
                               (keep_entry_id, keeper["canonical_key"], bibliography_id,
                                membership["entry_id"]))
                filenames.add(filename)
            db.execute(f"DELETE FROM entries WHERE id IN ({placeholders})", merge_entry_ids)
        return {"entry_id": keep_entry_id, "merged_entry_ids": merge_entry_ids,
                "filenames": sorted(filenames)}

    def repair_canonical_key(self, entry_id):
        return self.repair_canonical_keys([entry_id])[0]

    def repair_canonical_keys(self, entry_ids):
        """Repair a reviewed group atomically, avoiding transient key clashes."""
        entry_ids = list(dict.fromkeys(int(entry_id) for entry_id in entry_ids))
        if not entry_ids:
            return []
        placeholders = ",".join("?" for _ in entry_ids)
        with self.session() as db:
            entries = db.execute(f"SELECT id, canonical_key, fields, entry_type FROM entries WHERE id IN ({placeholders})", entry_ids).fetchall()
            if len(entries) != len(entry_ids):
                raise KeyError("Database entry not found")
            memberships = db.execute(f"""SELECT m.entry_id, m.citation_key, m.bibliography_id, b.filename
                FROM bibliography_entries m JOIN bibliographies b ON b.id=m.bibliography_id
                WHERE m.entry_id IN ({placeholders}) ORDER BY m.entry_id, m.position""", entry_ids).fetchall()
            by_entry = {entry_id: [] for entry_id in entry_ids}
            for membership in memberships:
                by_entry[membership["entry_id"]].append(membership)

            # Keys of entries not being repaired remain reserved.  Selected entries
            # then claim their old key in stable order, with suffixes for the rest.
            reserved_rows = db.execute(f"""SELECT DISTINCT m.citation_key FROM bibliography_entries m
                WHERE m.entry_id NOT IN ({placeholders})""", entry_ids).fetchall()
            reserved = {row["citation_key"].lower() for row in reserved_rows}
            canonical_rows = db.execute(f"""SELECT canonical_key FROM entries
                WHERE id NOT IN ({placeholders}) AND canonical_key IS NOT NULL""", entry_ids).fetchall()
            reserved.update(row["canonical_key"].lower() for row in canonical_rows)

            assigned = {}
            entry_rows = {row["id"]: row for row in entries}
            for entry_id in sorted(entry_ids):
                old_memberships = by_entry[entry_id]
                row = entry_rows[entry_id]
                desired = self._key_base(self._decode(row["fields"], "", row["entry_type"]))
                candidate = desired
                suffix_index = 0
                while candidate.lower() in reserved:
                    suffix = ""
                    value = suffix_index
                    while True:
                        suffix = chr(ord("a") + value % 26) + suffix
                        value = value // 26 - 1
                        if value < 0:
                            break
                    candidate = f"{desired}{suffix}"
                    suffix_index += 1
                reserved.add(candidate.lower())
                assigned[entry_id] = candidate

            # Clear existing ownership first. Move memberships through unique temporary
            # keys so swapping legacy aliases cannot violate a bibliography's
            # (bibliography_id, citation_key) primary key mid-transaction.
            db.execute(f"UPDATE entries SET canonical_key=NULL WHERE id IN ({placeholders})", entry_ids)
            for entry_id, key in assigned.items():
                db.execute("UPDATE entries SET canonical_key=? WHERE id=?", (key, entry_id))
            for entry_id in assigned:
                db.execute("UPDATE bibliography_entries SET citation_key=? WHERE entry_id=?",
                           (f"__bibry_repair_{entry_id}", entry_id))
            for entry_id, key in assigned.items():
                db.execute("UPDATE bibliography_entries SET citation_key=? WHERE entry_id=?", (key, entry_id))

            results = []
            for entry_id in entry_ids:
                old_memberships = by_entry[entry_id]
                results.append({"key": assigned[entry_id],
                                "filenames": sorted({row["filename"] for row in old_memberships}),
                                "old_keys": sorted({row["citation_key"] for row in old_memberships})})
        return results

    def update_entry_globally(self, entry_id, entry):
        raw = self._encode(entry)
        signature = _signature(entry)
        with self.session() as db:
            if not db.execute("SELECT 1 FROM entries WHERE id=?", (entry_id,)).fetchone():
                raise KeyError(entry_id)
            existing = db.execute("SELECT id, canonical_key FROM entries WHERE signature=? AND id<>?", (signature, entry_id)).fetchone()
            if existing:
                rows = db.execute("""SELECT m.bibliography_id, b.filename FROM bibliography_entries m
                    JOIN bibliographies b ON b.id=m.bibliography_id WHERE m.entry_id=?""", (entry_id,)).fetchall()
                for row in rows:
                    duplicate = db.execute("SELECT 1 FROM bibliography_entries WHERE bibliography_id=? AND entry_id=?",
                                           (row["bibliography_id"], existing["id"])).fetchone()
                    if duplicate:
                        db.execute("DELETE FROM bibliography_entries WHERE bibliography_id=? AND entry_id=?",
                                   (row["bibliography_id"], entry_id))
                    else:
                        db.execute("""UPDATE bibliography_entries SET entry_id=?, citation_key=?
                            WHERE bibliography_id=? AND entry_id=?""",
                            (existing["id"], existing["canonical_key"], row["bibliography_id"], entry_id))
                db.execute("DELETE FROM entries WHERE id=?", (entry_id,))
                return {"filenames": sorted({row["filename"] for row in rows}), "merged": True,
                        "entry_id": existing["id"]}
            db.execute("UPDATE entries SET entry_type=?, fields=?, signature=?, updated_at=? WHERE id=?",
                       (entry.get("ENTRYTYPE", "misc"), raw, signature, _utc_now(), entry_id))
            rows = db.execute("""SELECT DISTINCT b.filename FROM bibliography_entries m
              JOIN bibliographies b ON b.id=m.bibliography_id WHERE m.entry_id=?""", (entry_id,)).fetchall()
        return {"filenames": [row["filename"] for row in rows], "merged": False, "entry_id": entry_id}

    def memberships_for(self, filename):
        with self.session() as db:
            rows = db.execute("""SELECT m.citation_key, e.id entry_id, e.work_id, e.signature
              FROM bibliography_entries m JOIN bibliographies b ON b.id=m.bibliography_id
              JOIN entries e ON e.id=m.entry_id WHERE b.filename=?""", (filename,)).fetchall()
        return {row["citation_key"]: dict(row) for row in rows}

    def citation_key_in_use(self, key):
        with self.session() as db:
            return db.execute("SELECT 1 FROM bibliography_entries WHERE citation_key=? COLLATE NOCASE LIMIT 1", (key,)).fetchone() is not None

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
        return self.import_file(filename, entries)

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
                    parsed = load_biblatex_file(handle)
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
            source = db.execute("""SELECT e.id, e.entry_type, e.canonical_key FROM bibliography_entries m
              JOIN bibliographies b ON b.id=m.bibliography_id JOIN entries e ON e.id=m.entry_id
              WHERE b.filename=? AND m.citation_key=?""", (source_filename, key)).fetchone()
            if not source:
                raise KeyError(key)
            target_key = source["canonical_key"] or key
            target_id = self._bibliography(db, target_filename)
            db.execute("DELETE FROM bibliography_entries WHERE bibliography_id=? AND citation_key=?",
                       (target_id, target_key))
            position = db.execute("SELECT COALESCE(MAX(position), -1)+1 FROM bibliography_entries WHERE bibliography_id=?",
                                 (target_id,)).fetchone()[0]
            db.execute("INSERT INTO bibliography_entries(bibliography_id,entry_id,citation_key,position) VALUES(?,?,?,?)",
                       (target_id, source["id"], target_key, position))
        return target_key
