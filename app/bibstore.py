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

def load_bib():
    """Load or reload the BibTeX database."""
    if not BIBFILE.exists():
        # If no file, start with empty database
        db = bibtexparser.bibdatabase.BibDatabase()
        db.entries = []
        return db
    with open(BIBFILE, encoding="utf-8") as f:
        parser = bibtexparser.bparser.BibTexParser(common_strings=True)
        db = bibtexparser.load(f, parser)
    return db

def save_bib(db):
    """Write the BibTeX database back to file, with undo tracking."""
    global LAST_BIB_STATE, BIB_VERSION
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
    BIB_VERSION += 1
