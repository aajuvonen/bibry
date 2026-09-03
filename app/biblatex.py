"""BibLaTeX-friendly parsing helpers.

bibtexparser 1.x knows how to parse BibLaTeX syntax, but its default parser
silently ignores entry types outside traditional BibTeX.  Always disable that
filter so entries such as ``@online`` survive every Bibry read path.
"""
import bibtexparser


def parser():
    value = bibtexparser.bparser.BibTexParser(common_strings=True)
    value.ignore_nonstandard_types = False
    return value


def loads(text):
    return bibtexparser.loads(text, parser=parser())


def load(handle):
    return bibtexparser.load(handle, parser=parser())
