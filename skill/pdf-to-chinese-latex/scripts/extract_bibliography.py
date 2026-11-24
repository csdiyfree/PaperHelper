# -*- coding: utf-8 -*-
"""Build a `\\bibitem{...}` skeleton from source.txt (or raw.txt).

Locates the References / Bibliography section header in the text dump
written by `extract_text.py`, groups everything below into entries,
and emits a `thebibliography` block where each `\\bibitem{refN}` holds
the original-language entry text. The translator only has to copy the
block into `main.tex` and (optionally) tidy line wraps / punctuation —
no more hand-transcribing 40+ author/year/journal strings.

Input: pass `source.txt` (PyMuPDF, word spacing intact). Given `raw.txt`
(pdfplumber, loses spaces on tightly kerned PDFs: "JimmyLeiBa,") the
script reads the `source.txt` next to it instead when one exists.
Page numbers at page breaks are dropped, and the list stops where the
references end (an Appendix heading, or a page break followed by a page
with no further entries), so appendix text never lands in the last entry.

Two entry styles are handled:

* **Numbered** — `[1] J. A. Sanguesa et al., ...` or `1. Smith, J., ...`
  Common for IEEE / ACM / Wiley journals and most engineering papers.
  Chinese **GB/T 7714** style (`[1] 张三, 李四. 论文标题[J]. 期刊名,
  2020, 30(5): 100-110.`) lands on this same path — the `[N]` prefix and
  the `参考文献` section header are both recognized — so Chinese-source
  papers work out of the box with no extra flags.

* **Author–year** — `Agatz, N., Bouman, P., Schmidt, M., 2018. ...`
  Common for Elsevier / Transportation Research / OR journals. When no
  numeric prefix exists, entries are detected by a leading
  ``Surname, X.`` pattern and renumbered 1..N automatically.

Usage:
    python extract_bibliography.py source.txt              # to stdout
    python extract_bibliography.py source.txt --out bib.tex  # to file
    python extract_bibliography.py source.txt --key-prefix smith  # \\bibitem{smith1}
"""

import argparse
import io
import os
import re
import sys
from typing import List, Optional, Tuple

# Section-header patterns. Matched against a stripped line via `search`,
# so the header word can be followed by body-text overflow on the same
# physical line — common with Elsevier journals where pdfplumber stitches
# the running header onto the next visual line's body fragment, producing
# lines like  `References Research, 135, Article 105443. https://...`.
# Disambiguation (vs prose like "References to prior work...") happens in
# find_references_start by requiring an entry-shaped line within the next
# few lines after the candidate header.
SECTION_HEADER_PATTERNS = [
    re.compile(r"^\s*REFERENCES\b"),
    re.compile(r"^\s*References\b"),
    re.compile(r"^\s*Bibliography\b", re.IGNORECASE),
    re.compile(r"^\s*参考文献"),
    re.compile(r"^\s*文献参考"),
]

# Numbered entry start (must be at line start after optional indent):
#   [1] ... | (1) ... | 1. ... | 1) ...
NUMBERED_ENTRY = re.compile(r"^\s*[\[\(]?(\d+)[\]\)\.]\s+(.+)$")

# Author–year entry start. Matches a capitalized surname followed by
# ", X." (initial+period), with or without a space after the comma —
# pdfplumber on tightly typeset journals (Wiley / Elsevier) often loses
# inter-word whitespace, smushing "Amorosi, L.," into "Amorosi,L.,".
# Used in TWO contexts:
#   - At line start (when one entry per logical line)
#   - Mid-text after an entry-end boundary (next regex below)
AUTHOR_YEAR_ENTRY = re.compile(
    r"^\s*(?:[a-z]{2,4}\s+(?:der|den|de|van|von)\s+)?"
    r"[A-ZÀ-Ü][\w\-']+"
    r"(?:\s+[A-Z][a-z]+)?"
    r",\s?[A-Z]\."
)

# Entry boundary for author–year style when entries are run-on within
# the same line. Pattern: previous entry ended with `.` + space, then the
# next entry begins with a capitalized `Surname, X.,` author pattern.
#
#   "...965–981. Amorosi,L.,Puerto,J.,Valverde,C.,2024..."
#                ^^^^^^^^^^ split here
#   "...Boston, MA. Kuo, R., Lu, S.H., ..."
#                  ^^^^^^^ split here (Knuth-style book entry → next)
#
# The split is zero-width on the surname side; the period+whitespace gets
# consumed. The lookahead requires the trailing comma after the initial
# (e.g., "Kuo, R.,") so we don't over-split on prose like
# "...published by Springer. Smith, J. found..." (no comma after the J.).
AUTHOR_YEAR_SPLIT = re.compile(
    r"\.\s+"
    r"(?=(?:[a-z]{2,4}\s+(?:der|den|de|van|von)\s+)?"
    r"[A-ZÀ-Ü][\w\-']+(?:\s+[A-Z][a-z]+)?,\s?[A-Z]\.(?:[A-Z]\.)?,)"
)

# Lines to skip entirely (page delimiters from extract_text.py + common
# publisher watermark / page-header debris that breaks entry boundary
# detection).
SKIP_PATTERNS = [
    re.compile(r"^=+\s*PAGE\s+\d+\s*=+$"),
    re.compile(r"^\s*Downloaded from\b"),
    re.compile(r"^\s*onlinelibrary\.wiley", re.IGNORECASE),
    re.compile(r"^\s*Wiley Online Library", re.IGNORECASE),
    re.compile(r"^\s*See the Terms and Conditions"),
    re.compile(r"^\s*\d{8,}\s*,?\s*\d{4}\s*,?\s*\d+,\s*$"),  # Wiley journal meta strip
    # The "applicable / Creative / Commons / License" Wiley page-footer
    # ladder that gets word-wrapped onto separate lines:
    re.compile(r"^\s*(applicable|Creative|Commons|License|©\s*\d{4}.*)\s*$"),
    # Journal page header like "C.Liuetal./Intl.Trans.inOp.Res.32(2025)4008–4036 4035":
    re.compile(r"^\s*[A-Z]\.[A-Za-z]+et\s?al\.\s*/"),
]


# Page delimiter written by extract_text.py, and the sentinel it becomes
# inside the references section (so the end-of-section logic can see
# page breaks after the delimiter lines themselves are gone).
PAGE_DELIM = re.compile(r"^=+\s*PAGE\s+(\d+)\s*=+$")
PAGE_BREAK = "\f"

# A bare page number (header / footer) on its own line.
BARE_NUMBER = re.compile(r"^\s*\d{1,4}\s*$")

# A heading that ends the references: an appendix / supplement.
APPENDIX_HEADING = re.compile(
    r"^\s*(?:[A-Z]\.?\s+)?(?:Appendix|APPENDIX|Appendices|APPENDICES|"
    r"Supplementary|SUPPLEMENTARY|Supplemental|附录)\b"
)

# Text that can end a bibliography entry.
ENTRY_END = re.compile(r"[.!?)\]]\s*$")


def find_references_start(lines: List[str]) -> int:
    """Return the index of the line AFTER the References header. -1 if not found.

    Two strategies, applied in order:

    1. **Header match**: find a line containing `References` / `Bibliography`
       / `参考文献` AND verified that an entry-shaped line appears within
       the next 5 lines. The verification disambiguates against in-prose
       mentions like "References to prior work" and against Elsevier's
       compressed running-header-stitched-onto-body-overflow case
       (`References Research, 135, Article 105443. https://...`) where
       the header IS the real section start even though body text follows
       on the same line. Picks the LAST qualifying header (handles
       documents that mention "References" earlier in prose).

    2. **Cluster fallback**: when no header was found (e.g., 2-column
       extraction mangled the layout), look for ≥3 entry-shaped lines
       clustered in the lower half of the document and start from the
       first of those. Best-effort recovery — see troubleshooting.md
       for the 2-column extraction limitation.
    """
    last_header = -1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        matched = any(pat.search(s) for pat in SECTION_HEADER_PATTERNS)
        if not matched:
            continue
        # Verify: an entry-shaped line within the next 5 lines
        look = lines[i + 1:i + 6]
        if any(NUMBERED_ENTRY.match(L) or AUTHOR_YEAR_ENTRY.match(L.strip())
               for L in look):
            last_header = i + 1
    if last_header >= 0:
        return last_header

    # Cluster fallback: ≥3 author-year entries in a row, in lower half
    half = len(lines) // 2
    for i in range(half, len(lines)):
        if not AUTHOR_YEAR_ENTRY.match(lines[i].strip()):
            continue
        n_entries = sum(
            1 for j in range(i, min(i + 10, len(lines)))
            if AUTHOR_YEAR_ENTRY.match(lines[j].strip())
        )
        if n_entries >= 3:
            return i
    return -1


def should_skip(line: str) -> bool:
    for pat in SKIP_PATTERNS:
        if pat.match(line):
            return True
    return False


def prepare_section(lines: List[str]) -> Tuple[List[str], set]:
    """Clean the lines after the References header.

    Page delimiters become PAGE_BREAK sentinels, bare page numbers next to
    a page break (or at the very end) are dropped, publisher debris is
    skipped, and everything from an Appendix / Supplementary heading on is
    cut. Returns (lines, page numbers seen) — the latter lets
    `_strip_page_number` catch a number still glued to an entry's end.
    """
    pages: set = set()
    out: List[str] = []
    for ln in lines:
        s = ln.strip()
        m = PAGE_DELIM.match(s)
        if m:
            pages.add(int(m.group(1)))
            out.append(PAGE_BREAK)
            continue
        if APPENDIX_HEADING.match(s) and len(s) < 80:
            break
        if not s or should_skip(ln):
            continue
        out.append(ln)
    keep: List[str] = []
    for i, ln in enumerate(out):
        if ln != PAGE_BREAK and BARE_NUMBER.match(ln):
            prev = out[i - 1] if i > 0 else None
            nxt = out[i + 1] if i + 1 < len(out) else None
            if prev == PAGE_BREAK or nxt in (PAGE_BREAK, None):
                pages.add(int(ln.strip()))
                continue
        keep.append(ln)
    return keep, pages


def _cut_after_references(chunk: List[str]) -> List[str]:
    """Trim the LAST entry where the references end: at a page break after
    which the entry is already complete (the next page holds no further
    entries, e.g. an appendix). An entry that breaks mid-sentence keeps at
    most 3 continuation lines from the next page, up to its end."""
    out: List[str] = []
    for i, ln in enumerate(chunk):
        if ln != PAGE_BREAK:
            out.append(ln)
            continue
        if out and ENTRY_END.search(out[-1]):
            return out
        for ln2 in [c for c in chunk[i + 1:] if c != PAGE_BREAK][:3]:
            out.append(ln2)
            if ENTRY_END.search(ln2):
                break
        return out
    return out


def _join(chunk: List[str]) -> str:
    """Join an entry's lines; a line-end hyphen is kept and joined without
    a space (`encoder-` + `decoder` -> `encoder-decoder`)."""
    out = ""
    for ln in chunk:
        if ln == PAGE_BREAK:
            continue
        ln = ln.strip()
        if not out:
            out = ln
        elif out.endswith("-"):
            out += ln
        else:
            out += " " + ln
    return re.sub(r"\s+", " ", out).strip()


def _strip_page_number(text: str, pages: set) -> str:
    """Drop a page number glued to the end of an entry ('... 2019. 8')."""
    m = re.search(r"(?<=[.)\]])\s+(\d{1,4})$", text)
    if m and int(m.group(1)) in pages:
        return text[:m.start()].rstrip()
    return text


def parse_entries(lines: List[str]) -> List[Tuple[int, str]]:
    """Group lines into entries. Returns list of (n, text)."""
    lines, pages = prepare_section(lines)

    # Decide style: count which entry pattern is more common in the first
    # ~80 non-empty lines.
    n_numbered = 0
    n_authoryr = 0
    for ln in lines[:80]:
        s = ln.strip()
        if not s:
            continue
        if NUMBERED_ENTRY.match(ln):
            n_numbered += 1
        elif AUTHOR_YEAR_ENTRY.match(ln):
            n_authoryr += 1

    use_numbered = n_numbered >= n_authoryr and n_numbered > 0

    chunks = _parse_numbered(lines) if use_numbered else _parse_author_year(lines)
    if chunks:
        chunks[-1] = (chunks[-1][0], _cut_after_references(chunks[-1][1]))
    entries = []
    for n, chunk in chunks:
        text = _strip_page_number(_join(chunk), pages)
        if not use_numbered:
            text = re.sub(r"\s*©\s*\d{4}.*$", "", text).strip()
        if text:
            entries.append((n, text))
    return entries


def _parse_numbered(lines: List[str]) -> List[Tuple[int, List[str]]]:
    """Group numbered entries ([N] / (N) / N. / N) at line start).

    Numbers only go up, so a continuation line that happens to start with
    "2. " cannot restart an entry. For the [N] style the next marker is also
    found mid-line: PyMuPDF blocks can hold several entries
    ("... 2018. [20] Baptiste Rozière, ...").
    """
    entries: List[Tuple[int, List[str]]] = []
    cur_n: int = 0
    cur: List[str] = []
    bracket: Optional[bool] = None

    for ln in lines:
        if ln == PAGE_BREAK:
            if cur_n:
                cur.append(PAGE_BREAK)
            continue
        s = ln.strip()
        m = NUMBERED_ENTRY.match(s)
        if m and int(m.group(1)) > cur_n:
            if cur_n:
                entries.append((cur_n, cur))
            cur_n, cur = int(m.group(1)), []
            if bracket is None:
                bracket = s.startswith("[")
            s = m.group(2).strip()
        elif not cur_n:
            continue  # prologue before the first entry
        while bracket:
            mm = re.search(r"(?<!\S)\[%d\]\s+" % (cur_n + 1), s)
            if not mm:
                break
            head = s[:mm.start()].strip()
            if head:
                cur.append(head)
            entries.append((cur_n, cur))
            cur_n, cur = cur_n + 1, []
            s = s[mm.end():]
        if s.strip():
            cur.append(s.strip())
    if cur_n and cur:
        entries.append((cur_n, cur))
    return entries


def _parse_author_year(lines: List[str]) -> List[Tuple[int, List[str]]]:
    """Group entries from author-year style references.

    Two complementary strategies are applied:

    1. **Line-anchored detection**: when a line STARTS with the
       `Surname, X.,` pattern, treat it as a new entry boundary. This
       catches entries cleanly separated by line breaks even when the
       previous entry's text doesn't end with a parseable token
       (e.g., Wiley footer "applicable / Creative / Commons / License"
       junk lines in between).

    2. **Run-on splitting** within an already-accumulated chunk: when
       pdfplumber smushed two entries onto one line (no space after
       comma, no line break), `AUTHOR_YEAR_SPLIT` cuts on the
       `<period><space><Surname>,<Initial>.,` boundary.
    """
    # Stage 1: line-anchored grouping (page breaks kept as PAGE_BREAK so
    # the caller can cut the last entry where the references end)
    groups: List[List[str]] = []
    cur: List[str] = []
    for ln in lines:
        if ln == PAGE_BREAK:
            if cur:
                cur.append(PAGE_BREAK)
            continue
        s = ln.strip()
        if AUTHOR_YEAR_ENTRY.match(s):
            if cur:
                groups.append(cur)
            cur = [s]
        elif cur:
            cur.append(s)
        # else: leading prologue junk, drop
    if cur:
        groups.append(cur)

    # Stage 2: apply run-on splitting in case multiple entries were on a
    # single line. A group that holds one entry keeps its lines (and page
    # breaks); split sub-entries become single-line chunks. The journal-
    # footer copyright tail is dropped by the caller.
    entries: List[Tuple[int, List[str]]] = []
    counter = 0
    for group in groups:
        subs = AUTHOR_YEAR_SPLIT.split(_join(group))
        if len(subs) == 1:
            counter += 1
            entries.append((counter, group))
            continue
        for sub in subs:
            sub = sub.strip()
            if sub and AUTHOR_YEAR_ENTRY.match(sub):
                counter += 1
                entries.append((counter, [sub]))
    return entries


def _escape_latex_specials(s: str) -> str:
    """Escape LaTeX special characters that commonly appear inside extracted
    bibliography text — they break xelatex even when the bibliography is
    pasted verbatim. We escape only the characters that show up in real
    bibliography content from publishers and would otherwise be interpreted:

        &   in journal / conference names (`Robotics & Automation`)
        _   in URLs (`/mpa-factsheet_driving-maritime/`) and IDs
        #   rare but possible
        %   rare but possible

    We deliberately do NOT escape `$`, `^`, `~`, `{`, `}`, `\\` because:
      - `$` in a price field is rare; if present, hand-fix is fine
      - `^` / `~` rare in bib prose
      - `{` / `}` / `\\` would corrupt the existing \\bibitem braces
    """
    # Order matters: escape & first since the others don't start with &
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        # Skip already-escaped sequences: \X
        if c == "\\" and i + 1 < len(s):
            out.append(c); out.append(s[i + 1]); i += 2; continue
        if c in "&_#%":
            out.append("\\"); out.append(c)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def emit_bib_block(entries: List[Tuple[int, str]], key_prefix: str = "ref") -> str:
    out = io.StringIO()
    out.write("% ============================================================\n")
    out.write("% Auto-generated by extract_bibliography.py\n")
    out.write("% Each \\bibitem holds the ORIGINAL-language entry text from the\n")
    out.write("% source PDF. Review for line-wrap artifacts before submitting.\n")
    out.write(f"% ({len(entries)} entries detected)\n")
    out.write("% ============================================================\n\n")
    out.write("\\begin{thebibliography}{99}\n\n")
    for n, txt in entries:
        safe = _escape_latex_specials(txt)
        out.write(f"\\bibitem{{{key_prefix}{n}}} {safe}\n\n")
    out.write("\\end{thebibliography}\n")
    return out.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "raw",
        help="path to source.txt (preferred) or raw.txt from extract_text.py; "
             "given raw.txt, the source.txt next to it is read when present",
    )
    parser.add_argument(
        "--out", default=None,
        help="output .tex fragment path; default writes to stdout",
    )
    parser.add_argument(
        "--key-prefix", default="ref",
        help="prefix for \\bibitem keys. Default 'ref' → \\bibitem{ref1}, \\bibitem{ref2}, ...",
    )
    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    if not os.path.isfile(args.raw):
        print(f"ERROR: {args.raw}: file not found", file=sys.stderr)
        sys.exit(2)

    src = args.raw
    sibling = os.path.join(os.path.dirname(os.path.abspath(src)), "source.txt")
    if os.path.basename(src).lower() == "raw.txt" and os.path.isfile(sibling):
        # pdfplumber (raw.txt) drops word spaces on tightly kerned PDFs;
        # PyMuPDF (source.txt) keeps them.
        print(f"Reading {sibling} (word spacing intact) instead of raw.txt",
              file=sys.stderr)
        src = sibling
    with open(src, encoding="utf-8") as f:
        text = f.read()
    lines = []
    for ln in text.splitlines():
        # A header merged with the first entry ("References [1] A. Author")
        # is split so the header search and the entry parser both see it.
        m = re.match(r"^(\s*(?:REFERENCES|References|Bibliography|参考文献))\s+(\[1\]\s.*)$", ln)
        lines.extend([m.group(1), m.group(2)] if m else [ln])

    start = find_references_start(lines)
    if start < 0:
        print("ERROR: no References/Bibliography section header found", file=sys.stderr)
        print("  Looked for: REFERENCES / References / Bibliography / 参考文献", file=sys.stderr)
        sys.exit(2)

    entries = parse_entries(lines[start:])
    if not entries:
        print(
            "ERROR: located References header but couldn't parse any entries.",
            file=sys.stderr,
        )
        print("  Tip: open raw.txt and check the entry numbering style.", file=sys.stderr)
        sys.exit(2)

    block = emit_bib_block(entries, key_prefix=args.key_prefix)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(block)
        print(f"Wrote {len(entries)} entries → {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(block)


if __name__ == "__main__":
    main()
