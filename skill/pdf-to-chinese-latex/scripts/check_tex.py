# -*- coding: utf-8 -*-
"""Static-validate a LaTeX file before sending it to Overleaf.

Checks (LaTeX comments are stripped first, honouring \\%):
    * \\begin{env} / \\end{env} balance per environment
    * \\cite{key} <-> \\bibitem{key} correspondence
    * \\ref / \\eqref <-> \\label correspondence
    * each line's $...$ count is even (catches stray $ in prose)
    * every \\includegraphics{...} target exists AND has valid image
      magic bytes (catches placeholder text files masquerading as PNGs)

Exits 0 if everything looks fine, 1 otherwise. Use this before declaring
a translation "done" -- a clean static check catches the majority of bugs
that would otherwise force a round-trip to Overleaf.

Usage:
    python check_tex.py path/to/main.tex
"""

import argparse
import io
import os
import re
import sys
from collections import Counter


# Image format magic-byte signatures. The check looks for one of these
# at the start of the file; anything else (including 12-byte ASCII
# placeholders saved as `placeholder\n`) fails as "not a real image".
IMG_MAGIC = [
    ("PNG",  b"\x89PNG\r\n\x1a\n"),
    ("JPEG", b"\xff\xd8\xff"),
    ("PDF",  b"%PDF"),
    ("GIF87", b"GIF87a"),
    ("GIF89", b"GIF89a"),
    ("TIFF_LE", b"II*\x00"),
    ("TIFF_BE", b"MM\x00*"),
    ("EPS",  b"%!PS"),
    ("BMP",  b"BM"),
    ("WebP", b"RIFF"),  # generic RIFF header; webp specifically is RIFF....WEBP
]

# Extensions LaTeX will silently auto-append when \includegraphics{X}
# is given without one.
LATEX_GRAPHICS_EXTS = (".png", ".jpg", ".jpeg", ".pdf", ".eps", ".tif", ".tiff", ".bmp")


def check_image_file(path: str) -> "tuple[str | None, str]":
    """Return (kind, error). kind is None when the file isn't a valid image."""
    if not os.path.isfile(path):
        return None, "file not found"
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, f"stat failed: {e}"
    if size == 0:
        return None, "0-byte file"
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError as e:
        return None, f"read failed: {e}"
    for name, sig in IMG_MAGIC:
        if head.startswith(sig):
            return name, f"OK {name} ({size} B)"
    # Provide a hint about what was actually in the file
    try:
        snippet = head.decode("ascii", errors="replace").replace("\r", "").replace("\n", "\\n")
    except Exception:
        snippet = repr(head[:8])
    return None, f"unrecognized header {snippet!r} ({size} B) -- placeholder or corrupted?"


def expand_inputs(path: str, _seen: "set[str] | None" = None, _depth: int = 0) -> str:
    """Read `path` and recursively inline every `\\input{X}` / `\\include{X}`
    referenced from it. Resolves X relative to `path`'s directory; tries the
    target as-is, then with a `.tex` extension. Catches circular includes
    via `_seen`. Comments (lines that start with %) are skipped so that
    commented-out \\input statements don't trigger expansion.

    Universal: lets check_tex.py handle modular main.tex projects that
    split body / bibliography / preamble across files via \\input{}.
    Previously, `\\bibitem` entries in an \\input'd file were invisible to
    cite/bibitem matching, causing false-positive MISSING reports.
    """
    if _seen is None:
        _seen = set()
    abs_path = os.path.abspath(path)
    if abs_path in _seen or _depth > 10:
        return ""  # circular or absurd depth — bail
    _seen.add(abs_path)
    base_dir = os.path.dirname(abs_path)
    try:
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    except OSError:
        return ""

    inc_re = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")

    def replace(m: "re.Match[str]") -> str:
        target = m.group(1).strip()
        # try as-is, then with .tex appended
        for cand in (target, target + ".tex"):
            full = cand if os.path.isabs(cand) else os.path.join(base_dir, cand)
            if os.path.isfile(full):
                return "\n" + expand_inputs(full, _seen, _depth + 1) + "\n"
        # leave the directive in place if we can't find the file — the
        # downstream checks will flag the resulting missing bibitems / etc.
        return m.group(0)

    # Mask out commented lines so commented \input doesn't get expanded.
    def is_commented(start: int) -> bool:
        line_start = txt.rfind("\n", 0, start) + 1
        prefix = txt[line_start:start]
        i = 0
        while i < len(prefix):
            if prefix[i] == "\\" and i + 1 < len(prefix):
                i += 2; continue
            if prefix[i] == "%":
                return True
            i += 1
        return False

    out = []
    pos = 0
    for m in inc_re.finditer(txt):
        out.append(txt[pos:m.start()])
        if is_commented(m.start()):
            out.append(m.group(0))
        else:
            out.append(replace(m))
        pos = m.end()
    out.append(txt[pos:])
    return "".join(out)


def strip_comments(txt: str) -> str:
    """Remove LaTeX comments: from an unescaped `%` to the end of its line.

    `\\%` is a literal percent sign; `\\\\%` is a line break followed by a
    comment (an even run of backslashes does not escape the `%`). Newlines
    are kept so reported line numbers still match the file.
    """
    out = []
    for line in txt.split("\n"):
        i = 0
        while i < len(line):
            c = line[i]
            if c == "\\":
                i += 2  # skip the escaped char (\%, \\, ...)
                continue
            if c == "%":
                line = line[:i]
                break
            i += 1
        out.append(line)
    return "\n".join(out)


def check(path: str) -> int:
    # Inline \input{}/\include{} so bibitems / labels / images defined in
    # split-out files are visible to the static checks below.
    txt = expand_inputs(path)
    if not txt:
        # Fall back to raw read if expansion path failed
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    # A commented-out \includegraphics / \cite / \label must not count.
    txt = strip_comments(txt)

    issues = 0

    # 1. begin/end balance
    begs = re.findall(r"\\begin\{([^}]+)\}", txt)
    ends = re.findall(r"\\end\{([^}]+)\}", txt)
    bc, ec = Counter(begs), Counter(ends)
    for k in set(list(bc) + list(ec)):
        if bc[k] != ec[k]:
            print(f"  MISMATCH \\begin{{{k}}}={bc[k]} \\end{{{k}}}={ec[k]}")
            issues += 1
    print(f"  begin total={sum(bc.values())}, end total={sum(ec.values())}")

    # 2. cite ↔ bibitem
    cites = re.findall(r"\\cite[a-zA-Z]*\{([^}]+)\}", txt)
    cite_keys = set()
    for c in cites:
        for k in c.split(","):
            cite_keys.add(k.strip())
    bibs = set(re.findall(r"\\bibitem\{([^}]+)\}", txt))
    missing = cite_keys - bibs
    unused = bibs - cite_keys
    if missing:
        print(f"  MISSING bibitems for: {sorted(missing)}")
        issues += 1
    if unused:
        print(f"  UNUSED bibitems: {sorted(unused)}")
        # unused is usually a soft warning, not an error
    print(f"  cited={len(cite_keys)} bibitems={len(bibs)}")

    # 3. ref / eqref ↔ label
    labels = set(re.findall(r"\\label\{([^}]+)\}", txt))
    refs = set(re.findall(r"\\ref\{([^}]+)\}", txt)) | set(
        re.findall(r"\\eqref\{([^}]+)\}", txt)
    )
    missing_lbl = refs - labels
    if missing_lbl:
        print(f"  REFS without matching \\label: {sorted(missing_lbl)}")
        issues += 1
    print(f"  labels={len(labels)} refs={len(refs)}")

    # 4. per-line $...$ parity
    odd_lines = []
    for i, ln in enumerate(txt.split("\n"), 1):
        s = ln
        cnt = 0
        j = 0
        while j < len(s):
            if s[j] == "\\" and j + 1 < len(s):
                # skip escaped char (e.g., \$)
                j += 2
                continue
            if s[j] == "$":
                cnt += 1
            j += 1
        if cnt % 2:
            odd_lines.append((i, cnt, ln[:80]))
    if odd_lines:
        print(f"  ODD $ count on {len(odd_lines)} line(s):")
        for i, c, l in odd_lines[:10]:
            print(f"    line {i} (count={c}): {l}")
        issues += 1

    # 5. \includegraphics target must be a real image, not a placeholder.
    # Honors \graphicspath{{dir1/}{dir2/}} from the preamble.
    base_dir = os.path.dirname(os.path.abspath(path))
    # Collect search dirs: main.tex dir first, then each graphicspath entry.
    search_dirs = [base_dir]
    gp_re = re.compile(r"\\graphicspath\s*\{((?:\{[^}]*\}\s*)+)\}")
    for gp_match in gp_re.findall(txt):
        for d in re.findall(r"\{([^}]*)\}", gp_match):
            if d:
                resolved = d if os.path.isabs(d) else os.path.join(base_dir, d)
                search_dirs.append(resolved)
    inc_re = re.compile(r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}")
    img_paths = inc_re.findall(txt)
    bad_imgs = []
    for ref in img_paths:
        ref = ref.strip()
        # If ref has no extension, LaTeX would try extensions in order.
        extensions_to_try = [""] if any(
            ref.lower().endswith(e) for e in LATEX_GRAPHICS_EXTS
        ) else [""] + list(LATEX_GRAPHICS_EXTS)
        kind = None
        last_err = "no candidate matched"
        for ext in extensions_to_try:
            candidate_name = ref + ext if ext else ref
            for d in search_dirs:
                abs_path = candidate_name if os.path.isabs(candidate_name) \
                    else os.path.join(d, candidate_name)
                k, err = check_image_file(abs_path)
                if k:
                    kind = k
                    break
                last_err = err
            if kind:
                break
        if not kind:
            bad_imgs.append((ref, last_err))
    if bad_imgs:
        print(f"  BAD \\includegraphics targets ({len(bad_imgs)}):")
        for r, e in bad_imgs[:10]:
            print(f"    {r}: {e}")
        issues += 1
    print(f"  images={len(img_paths)} valid={len(img_paths) - len(bad_imgs)}"
          + (f"  (search_dirs: {[os.path.basename(d) or '.' for d in search_dirs]})"
             if len(search_dirs) > 1 else ""))

    if issues == 0:
        print("OK: no issues detected.")
    else:
        print(f"FAIL: {issues} issue(s) detected.")
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tex", help="path to main.tex")
    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    sys.exit(1 if check(args.tex) else 0)


if __name__ == "__main__":
    main()
