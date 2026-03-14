#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sort & dedupe a BibTeX file by (ascending) publication date.
- No external dependencies.
- Deduplicates by DOI (preferred), arXiv ID, then (normalized title + year).
- Keeps the "best" record when merging duplicates (more fields and presence of DOI/URL/abstract).
- Writes a consistently formatted .bib output.
- NEW: If an entry has a DOI, any 'url' field is removed. If it lacks a DOI but its 'url' is a doi.org/dx.doi.org link, the DOI is promoted from that URL and 'url' is removed.

Usage:
    python sort_dedupe_bibtex.py --input mylib.bib --output mylib.sorted.bib
Options:
    --keep-keys             Keep original order of fields within an entry (best-effort). Default: False (fields are re-ordered alphabetically).
    --dry-run               Do not write output; just show stats.
    --prefer                Comma-separated list determining dedupe priority (default: doi,arxiv,titleyear).
                            You can include any subset and order, e.g.: --prefer titleyear,doi
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Dict, List, Tuple, Optional

MONTH_MAP = {
    'jan': 1, 'january': 1,
    'feb': 2, 'february': 2,
    'mar': 3, 'march': 3,
    'apr': 4, 'april': 4,
    'may': 5,
    'jun': 6, 'june': 6,
    'jul': 7, 'july': 7,
    'aug': 8, 'august': 8,
    'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10,
    'nov': 11, 'november': 11,
    'dec': 12, 'december': 12,
}

def _strip_outer_braces_or_quotes(s: str) -> str:
    s = s.strip()
    if not s:
        return s
    if (s[0] == '{' and s.endswith('}')) or (s[0] == '"' and s.endswith('"')):
        return s[1:-1].strip()
    return s

def _normalize_whitespace(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip()

def _normalize_title(s: str) -> str:
    s = s.lower()
    s = re.sub(r'[{}"]', '', s)
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'[^a-z0-9\s]', '', s)  # drop punctuation
    return s.strip()

def _clean_doi(s: str) -> str:
    s = _strip_outer_braces_or_quotes(s)
    s = s.strip()
    s = re.sub(r'^(https?://)?(dx\.)?doi\.org/', '', s, flags=re.I)
    return s.lower()

def _clean_arxiv(e: Dict[str, str]) -> Optional[str]:
    eprint = e.get('eprint') or e.get('arxivid')
    if not eprint:
        return None
    ap = (e.get('archiveprefix') or e.get('eprinttype') or '').lower()
    if ap and 'arxiv' not in ap:
        return None
    s = _strip_outer_braces_or_quotes(eprint).lower().strip()
    s = re.sub(r'^(arxiv:)', '', s)
    s = s.replace('v', 'v')  # keep version if present
    return s or None

def _parse_int(s: str) -> Optional[int]:
    try:
        return int(s)
    except Exception:
        return None

def _month_to_int(m: str) -> Optional[int]:
    if not m:
        return None
    m = _strip_outer_braces_or_quotes(m).strip()
    # Allow numeric month or a macro like "jan"
    if re.fullmatch(r'\d{1,2}', m):
        mi = int(m)
        if 1 <= mi <= 12:
            return mi
        return None
    key = m.lower()
    return MONTH_MAP.get(key)

def _extract_date_fields(fields: Dict[str, str]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    # Prefer full ISO date if available
    date = fields.get('date') or fields.get('year-month-day')
    if date:
        d = _strip_outer_braces_or_quotes(date)
        m = re.match(r'^\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})', d)
        if m:
            y, mo, da = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return y, mo, da
        m = re.match(r'^\s*(\d{4})[-/](\d{1,2})\s*$', d)
        if m:
            y, mo = int(m.group(1)), int(m.group(2))
            return y, mo, None
        m = re.match(r'^\s*(\d{4})\s*$', d)
        if m:
            return int(m.group(1)), None, None

    y = fields.get('year')
    year = None
    if y:
        y = _strip_outer_braces_or_quotes(y)
        y_digits = re.search(r'(\d{4})', y)
        if y_digits:
            year = int(y_digits.group(1))

    month = fields.get('month')
    mo = _month_to_int(month) if month else None

    day = fields.get('day')
    da = None
    if day:
        day = _strip_outer_braces_or_quotes(day)
        m = re.match(r'^\d{1,2}$', day.strip())
        if m:
            da = int(day)

    return year, mo, da

class BibEntry:
    def __init__(self, raw: str):
        self.raw = raw
        self.type: str = ""
        self.key: str = ""
        self.fields: Dict[str, str] = {}
        self._parse()

    def _parse(self):
        txt = self.raw.strip()
        m = re.match(r'@(\w+)\s*{', txt, flags=re.S)
        if not m:
            return
        self.type = m.group(1).strip()
        start = txt.find('{', m.end()-1)
        block = txt[start+1:].strip()
        if block.endswith('}'):
            block = block[:-1].rstrip()
        key, rest = self._split_first(block, ',')
        self.key = key.strip()
        if rest is None:
            self.fields = {}
            return
        self.fields = self._parse_fields(rest)

    @staticmethod
    def _split_first(s: str, delim: str):
        depth = 0
        in_quotes = False
        for i, ch in enumerate(s):
            if ch == '"' and (i == 0 or s[i-1] != '\\'):
                in_quotes = not in_quotes
            elif not in_quotes:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth = max(0, depth-1)
                elif ch == delim and depth == 0:
                    return s[:i], s[i+1:]
        return s, None

    @staticmethod
    def _split_top_level(s: str, delim: str) -> List[str]:
        parts = []
        depth = 0
        in_quotes = False
        start = 0
        for i, ch in enumerate(s):
            if ch == '"' and (i == 0 or s[i-1] != '\\'):
                in_quotes = not in_quotes
            elif not in_quotes:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth = max(0, depth-1)
                elif ch == delim and depth == 0:
                    parts.append(s[start:i])
                    start = i+1
        parts.append(s[start:])
        return parts

    def _parse_fields(self, s: str) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        for chunk in self._split_top_level(s, ','):
            if not chunk.strip():
                continue
            if '=' not in chunk:
                continue
            k, v = chunk.split('=', 1)
            key = k.strip().lower()
            val = v.strip()
            fields[key] = val
        return fields

    def score(self) -> int:
        score = 0
        if 'doi' in self.fields and self.fields['doi'].strip():
            score += 10
        if 'url' in self.fields and self.fields['url'].strip():
            score += 4
        if 'abstract' in self.fields and self.fields['abstract'].strip():
            score += 3
        score += sum(1 for v in self.fields.values() if v.strip())
        return score

    def date_tuple(self) -> Tuple[int, int, int]:
        y, m, d = _extract_date_fields(self.fields)
        return (y if y is not None else 9999,
                m if m is not None else 99,
                d if d is not None else 99)

    def dedupe_keys(self, prefer: List[str]) -> List[str]:
        keys: List[str] = []
        doi = self.fields.get('doi')
        if doi:
            keys.append('doi:' + _clean_doi(doi))
        ax = _clean_arxiv(self.fields)
        if ax:
            keys.append('arxiv:' + ax)
        title = self.fields.get('title')
        y, _, _ = _extract_date_fields(self.fields)
        if title and y:
            keys.append('titleyear:' + _normalize_title(_strip_outer_braces_or_quotes(title)) + f'::{y}')
        pref_order = {p: i for i, p in enumerate(prefer)}
        def weight(k: str) -> int:
            kind = k.split(':', 1)[0]
            return pref_order.get(kind, 999)
        keys.sort(key=weight)
        return keys

    def sanitize(self):
        """Apply post-processing rules:
        - If DOI present, drop 'url'.
        - If DOI missing but url is doi.org/dx.doi.org, promote to DOI and drop 'url'.
        """
        doi_val = self.fields.get('doi')
        url_val = self.fields.get('url')
        if doi_val and url_val:
            self.fields.pop('url', None)
            return

        if (not doi_val) and url_val:
            raw = _strip_outer_braces_or_quotes(url_val).strip()
            if re.search(r'^(https?://)?(dx\.)?doi\.org/', raw, flags=re.I):
                cleaned = _clean_doi(raw)
                if cleaned:
                    self.fields['doi'] = '{' + cleaned + '}'
                self.fields.pop('url', None)

    def to_string(self, keep_field_order: bool = False) -> str:
        if keep_field_order:
            items = list(self.fields.items())
        else:
            items = sorted(self.fields.items(), key=lambda kv: kv[0])
        def fmt_val(v: str) -> str:
            vv = v.strip()
            if vv.startswith('{') or vv.startswith('"'):
                return vv
            if re.fullmatch(r'[A-Za-z]+', vv) or re.fullmatch(r'\d+', vv):
                return vv
            return '{' + vv + '}'
        lines = [f'@{self.type}{{{self.key},']
        for k, v in items:
            lines.append(f'  {k} = {fmt_val(v)},')
        if lines[-1].endswith(','):
            lines[-1] = lines[-1][:-1]
        lines.append('}')
        return '\n'.join(lines)

def split_entries(text: str) -> List[str]:
    entries = []
    i = 0
    n = len(text)
    while i < n:
        at = text.find('@', i)
        if at == -1:
            break
        m = re.match(r'@(\w+)\s*[{(]', text[at:], flags=re.S)
        if not m:
            i = at + 1
            continue
        j = at + m.end() - 1
        opening = text[j]
        closing = '}' if opening == '{' else ')'
        depth = 0
        k = j
        in_quotes = False
        while k < n:
            ch = text[k]
            if ch == '"' and (k == 0 or text[k-1] != '\\'):
                in_quotes = not in_quotes
            elif not in_quotes:
                if ch == opening:
                    depth += 1
                elif ch == closing:
                    depth -= 1
                    if depth == 0:
                        entries.append(text[at:k+1])
                        i = k + 1
                        break
            k += 1
        else:
            entries.append(text[at:])
            i = n
    return entries

def pick_better(a: BibEntry, b: BibEntry) -> BibEntry:
    sa, sb = a.score(), b.score()
    if sa != sb:
        return a if sa > sb else b
    if a.date_tuple() != b.date_tuple():
        return a if a.date_tuple() > b.date_tuple() else b
    if len(a.raw) != len(b.raw):
        return a if len(a.raw) > len(b.raw) else b
    return a if a.key < b.key else b

def main():
    p = argparse.ArgumentParser(description="Sort & dedupe a BibTeX file by date (ascending).")
    p.add_argument('--input', '-i', required=True, help='Path to input .bib file')
    p.add_argument('--output', '-o', required=False, help='Path to output .bib file (default: <input>.sorted.bib)')
    p.add_argument('--keep-keys', action='store_true', help='Keep original field order (best-effort)')
    p.add_argument('--dry-run', action='store_true', help='Only print stats, do not write output')
    p.add_argument('--prefer', default='doi,arxiv,titleyear', help='Dedupe key priority, e.g. "titleyear,doi"')
    args = p.parse_args()

    in_path = args.input
    out_path = args.output or (os.path.splitext(in_path)[0] + '.sorted.bib')
    prefer_list = [s.strip().lower() for s in args.prefer.split(',') if s.strip()]

    if not os.path.exists(in_path):
        print(f'ERROR: Input file not found: {in_path}', file=sys.stderr)
        sys.exit(1)

    with open(in_path, 'r', encoding='utf-8') as f:
        text = f.read()

    raw_entries = split_entries(text)
    entries = [BibEntry(raw) for raw in raw_entries if raw.strip().startswith('@')]

    total = len(entries)
    if total == 0:
        print('No entries found.')
        sys.exit(0)

    # Deduplication
    seen: Dict[str, BibEntry] = {}
    kept: Dict[int, BibEntry] = {}
    dup_count = 0

    for e in entries:
        keys = e.dedupe_keys(prefer_list)
        if not keys:
            kept[id(e)] = e
            continue
        chosen_key = None
        winner: Optional[BibEntry] = None
        for k in keys:
            if k in seen:
                winner = pick_better(seen[k], e)
                chosen_key = k
                break
        if winner is None:
            for k in keys:
                seen[k] = e
            kept[id(e)] = e
        else:
            if winner is e:
                for k in list(seen.keys()):
                    if seen.get(k) is seen[chosen_key]:
                        seen[k] = e
                dup_count += 1
                for hid, be in list(kept.items()):
                    if be is not e and be.key == seen[chosen_key].key:
                        kept.pop(hid, None)
                kept[id(e)] = e
            else:
                dup_count += 1

    deduped_entries = list(kept.values())

    # Sort ascending by (year, month, day); then by normalized title to stabilize
    def sort_key(e: BibEntry):
        y, m, d = e.date_tuple()
        title = _normalize_title(_strip_outer_braces_or_quotes(e.fields.get('title', ''))) if e.fields.get('title') else ''
        return (y, m, d, title, e.key.lower())

    deduped_entries.sort(key=sort_key)

    # Sanitize URLs/DOIs according to requested policy
    for e in deduped_entries:
        e.sanitize()

    if args.dry_run:
        dated = sum(1 for e in deduped_entries if e.date_tuple()[0] != 9999)
        print(f'Entries total: {total}')
        print(f'After dedupe: {len(deduped_entries)} (removed {total - len(deduped_entries)} duplicates; detected {dup_count} dup hits)')
        print(f'With date: {dated}; Without date: {len(deduped_entries) - dated}')
        print('Dry run complete; no file written.')
        return

    with open(out_path, 'w', encoding='utf-8') as f:
        for i, e in enumerate(deduped_entries):
            if i:
                f.write('\n\n')
            f.write(e.to_string(keep_field_order=args.keep_keys))

    print(f'Wrote {len(deduped_entries)} entries to: {out_path} (from {total}; removed {total - len(deduped_entries)} duplicates)')

if __name__ == '__main__':
    main()
