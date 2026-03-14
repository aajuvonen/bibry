from pylatexenc.latex2text import LatexNodes2Text

converter = LatexNodes2Text()

def latex_to_text(s):
    if not s:
        return ""
    return converter.latex_to_text(s)