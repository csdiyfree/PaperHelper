# -*- coding: utf-8 -*-
"""Check that the bilingual edition pairs every unit correctly.

Reads every source/Chinese pair written with the bilingual macros
(\\bipar, \\bihead, \\bisection, \\bisubsection, \\bisubsubsection,
\\biitem, \\bifoot, \\bithanks) from main.tex, then searches the text
of both compiled PDFs:

  * the source half must appear in main_bilingual.pdf     (else MISSING-SRC)
  * a paragraph-length source half must NOT appear whole
    in main.pdf                                            (else LEAK)
  * the Chinese half must appear in both PDFs             (else MISSING-ZH)
  * a long source probe found more than once in the
    bilingual PDF is reported as a possible duplicate      (DUP?, warning)

Each half is reduced to its longest run of plain text (math, \\cite,
\\ref and other commands removed), whitespace and soft hyphens stripped,
and probed at its start, middle and end — a page break or running header
can split one probe but rarely all three.

Usage:
    python check_bilingual.py path/to/project_folder

Exits 0 when no pair FAILs (warnings allowed), 1 otherwise.
Run it after compile_pdf.py has produced both PDFs.
"""

import argparse
import io
import os
import re
import sys

PAIR_MACROS = ("bipar", "bihead", "bisection", "bisubsection",
               "bisubsubsection", "biitem", "bifoot", "bithanks")
SRC_PROBE = 24   # chars per probe, source side (whitespace stripped)
ZH_PROBE = 12    # chars per probe, Chinese side
CJK = re.compile(r"[\u3400-\u9fff]")


def strip_comments(tex: str) -> str:
    out = []
    for line in tex.splitlines():
        cut = len(line)
        i = 0
        while i < len(line):
            if line[i] == "\\":
                i += 2
                continue
            if line[i] == "%":
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def brace_arg(s: str, i: int) -> "tuple[str, int]":
    """s[i] must be '{' (after optional whitespace); return (content, end)."""
    while i < len(s) and s[i].isspace():
        i += 1
    if i >= len(s) or s[i] != "{":
        raise ValueError(f"expected '{{' near: {s[i:i + 40]!r}")
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":          # skip the escaped char (\{ \} \\ ...)
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    raise ValueError(f"unbalanced braces near: {s[i:i + 40]!r}")


def find_pairs(tex: str):
    pat = re.compile(r"\\(" + "|".join(PAIR_MACROS) + r")(?![a-zA-Z])")
    pairs = []
    for m in pat.finditer(tex):
        line = tex.count("\n", 0, m.start()) + 1
        rest = tex[m.end():m.end() + 20].lstrip()
        if not rest.startswith("{"):
            continue  # a definition such as \providecommand{\bipar}[2]{...}
        try:
            src, k = brace_arg(tex, m.end())
            zh, _ = brace_arg(tex, k)
        except ValueError as e:
            pairs.append((m.group(1), line, None, None, str(e)))
            continue
        pairs.append((m.group(1), line, src, zh, None))
    return pairs


def plain_run(s: str) -> str:
    """Longest run of typeset-able plain text in a LaTeX fragment."""
    s = re.sub(r"\\(bifoot|bithanks|footnote|thanks)\s*\{.*", "|", s, flags=re.S)
    s = re.sub(r"\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)", "|", s, flags=re.S)
    s = re.sub(r"(?<!\\)\$.*?(?<!\\)\$", "|", s, flags=re.S)
    s = re.sub(r"\\(cite[a-zA-Z]*|ref|eqref|label|url|href|includegraphics)"
               r"\s*(\[[^\]]*\])?\s*\{[^}]*\}", "|", s)
    s = re.sub(r"\\[a-zA-Z]+\*?", "|", s)
    s = re.sub(r"\\.", "|", s)                 # \% \& \_ \\ ...
    s = re.sub(r"[{}~`'\"\[\]]", "|", s)
    runs = [re.sub(r"\s+", "", x) for x in s.split("|")]
    runs = [x for x in runs if x]
    return max(runs, key=len) if runs else ""


def probes(run: str, n: int):
    if len(run) <= n:
        return [run] if run else []
    mid = (len(run) - n) // 2
    return list(dict.fromkeys([run[:n], run[mid:mid + n], run[-n:]]))


def pdf_text(path: str) -> str:
    import fitz  # PyMuPDF
    with fitz.open(path) as d:
        t = "".join(p.get_text() for p in d)
    t = t.replace("\u00ad", "")
    t = re.sub(r"-\n", "", t)          # LaTeX hyphenation at line ends
    return re.sub(r"\s+", "", t)


def found(text: str, ps) -> bool:
    return any(p in text for p in ps)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project_dir", help="folder with main.tex, main.pdf, main_bilingual.pdf")
    args = ap.parse_args()
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    d = args.project_dir
    need = ["main.tex", "main.pdf", "main_bilingual.pdf"]
    missing = [f for f in need if not os.path.isfile(os.path.join(d, f))]
    if missing:
        print(f"[ERROR] missing {missing} in {d} -- run compile_pdf.py first")
        sys.exit(2)

    with open(os.path.join(d, "main.tex"), encoding="utf-8") as f:
        tex = strip_comments(f.read())
    pairs = find_pairs(tex)
    mono = pdf_text(os.path.join(d, "main.pdf"))
    bil = pdf_text(os.path.join(d, "main_bilingual.pdf"))

    fails = warns = 0
    for kind, line, src, zh, err in pairs:
        if err:
            print(f"  FAIL L{line} \\{kind}: cannot parse arguments ({err})")
            fails += 1
            continue
        flags, notes = [], []
        sp = probes(plain_run(src), SRC_PROBE)
        zp = probes(plain_run(zh), ZH_PROBE)
        if sp and not found(bil, sp):
            flags.append("MISSING-SRC")
        # A leak means the whole source run shows up in the Chinese edition.
        # Chinese text legitimately quotes source terms in parentheses
        # (“注意力机制（attention mechanism）”), so short runs and partial
        # matches are not evidence of a leak.
        src_run = plain_run(src)
        if (kind in ("bipar", "biitem", "bifoot", "bithanks")
                and len(src_run) >= 40 and not CJK.search(src_run)
                and all(p in mono for p in sp)):
            flags.append("LEAK")
        if zp and not (found(bil, zp) and found(mono, zp)):
            flags.append("MISSING-ZH")
        if (len(sp) == 3 and len(src_run) >= 40
                and all(bil.count(p) > 1 for p in sp)):
            notes.append("DUP?")
        if not src.strip() or not zh.strip():
            flags.append("EMPTY-HALF")
        if flags:
            fails += 1
        elif notes:
            warns += 1
        if flags or notes:
            tag = "FAIL" if flags else "WARN"
            print(f"  {tag} L{line} \\{kind} {' '.join(flags + notes)}"
                  f"  src~{(sp[0] if sp else '')!r}  zh~{(zp[0] if zp else '')!r}")

    print(f"  pairs={len(pairs)} fail={fails} warn={warns}")
    if not pairs:
        print("FAIL: no bilingual pairs found in main.tex -- Step 5 must write "
              "\\bipar{source}{中文} etc.")
        sys.exit(1)
    if fails:
        print("FAIL: fix the flagged pairs in main.tex (a MISSING-SRC/LEAK that is "
              "only a page-break split can be ignored after looking at the page).")
        sys.exit(1)
    print("OK: every pair appears correctly in both editions.")


if __name__ == "__main__":
    main()
