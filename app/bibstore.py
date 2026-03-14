# app/bibstore.py
import bibtexparser
from pathlib import Path

# Path to the BibTeX file (flat-file store)
ROOT = Path(__file__).parent.parent
BIBFILE = ROOT / "main.bib"
BACKUP = ROOT / "backups" / "main.bib.bak"

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

def save_bib(db):
    """Write the BibTeX database back to file, with undo tracking."""
    global LAST_BIB_STATE, BIB_VERSION, _BIB_SIGNATURE, _DB_CACHE
    if not BACKUP.parent.exists():
        BACKUP.parent.mkdir(parents=True)
    # Backup current state
    if BIBFILE.exists():
        LAST_BIB_STATE = BIBFILE.read_text(encoding="utf-8")
        # Also save a timestamped backup copy
        BACKUP.write_text(LAST_BIB_STATE, encoding="utf-8")
    # Write new state
    writer = bibtexparser.bwriter.BibTexWriter()
    BIBFILE.write_text(writer.write(db), encoding="utf-8")
    _BIB_SIGNATURE = get_bib_signature()
    _DB_CACHE = db
    BIB_VERSION += 1
