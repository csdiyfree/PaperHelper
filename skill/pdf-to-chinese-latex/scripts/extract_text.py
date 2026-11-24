# -*- coding: utf-8 -*-
"""Extract page-by-page text from a PDF.

Usage:
    python extract_text.py input.pdf --out output_dir/raw.txt

Writes two files, both with `========== PAGE N ==========` delimiters so a
translator (human or LLM) can find specific pages by line search:

  * raw.txt    -- pdfplumber text (consumed by extract_bibliography.py).
  * source.txt -- PyMuPDF text blocks, one blank line between blocks.
                  pdfplumber drops inter-word spaces on tightly kerned
                  PDFs ("Recurrentneuralnetworks"); PyMuPDF keeps them.
                  source.txt is the source-language side of the bilingual
                  edition (\\bipar{<source>}{<中文>}), so it must be clean.

Requires: pdfplumber and PyMuPDF (`pip install pdfplumber PyMuPDF`).
"""

import argparse
import io
import os
import sys

import pdfplumber


def extract(pdf_path: str, out_path: str) -> int:
    """Extract text. Returns total chars written. Raises SystemExit if the
    PDF appears to have no text layer (scanned image PDF — needs OCR first).
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    total_chars = 0
    with pdfplumber.open(pdf_path) as pdf, open(out_path, "w", encoding="utf-8") as f:
        n = len(pdf.pages)
        for i, page in enumerate(pdf.pages, 1):
            f.write(f"\n========== PAGE {i} ==========\n")
            text = page.extract_text() or ""
            total_chars += len(text)
            f.write(text)
        print(f"Wrote {n} pages -> {out_path}  ({total_chars} chars)")

    # Loud-failure guard: if a 5+ page PDF has < 50 chars in total, it's
    # almost certainly a scanned image PDF with no text layer. The rest
    # of the skill (caption search, bibliography extraction, equation
    # alignment) all depend on real text and will silently produce
    # garbage. Fail here with an actionable message instead.
    if n >= 5 and total_chars < 50:
        sys.stderr.write(
            "\n[ERROR] This PDF appears to have no text layer "
            f"({total_chars} chars across {n} pages).\n"
            "This skill needs a born-digital PDF or one that has been OCR'd.\n"
            "Quick check:    pdftotext input.pdf -\n"
            "If empty, run OCR first (e.g. `ocrmypdf input.pdf input-ocr.pdf`)\n"
            "and re-run this skill with the OCR'd file.\n"
        )
        sys.exit(2)
    return total_chars


def _join_block_lines(text: str) -> str:
    """Undo the PDF's hard line wraps inside one text block.

    Lines are joined with a space. A line ending in '-' is joined with no
    space and the hyphen kept ("transduc-tion", "encoder-decoder"): only
    a reader can tell a soft hyphen from a real compound, so the model
    decides when it transcribes the source half.
    """
    out = ""
    for line in (ln.strip() for ln in text.splitlines()):
        if not line:
            continue
        if not out:
            out = line
        elif out.endswith("-"):
            out += line
        else:
            out += " " + line
    return out


def extract_source(pdf_path: str, out_path: str) -> int:
    """Write PyMuPDF text blocks (reading order, blank line between blocks).

    Each block is roughly one paragraph / heading / caption, which is the
    unit the bilingual edition pairs with its Chinese translation.
    Returns total chars written.
    """
    import fitz  # PyMuPDF

    total_chars = 0
    with fitz.open(pdf_path) as doc, open(out_path, "w", encoding="utf-8") as f:
        for i, page in enumerate(doc, 1):
            f.write(f"\n========== PAGE {i} ==========\n")
            for block in page.get_text("blocks", sort=True):
                if block[6] != 0:  # 0 = text block, 1 = image block
                    continue
                text = _join_block_lines(block[4])
                if not text:
                    continue
                total_chars += len(text)
                f.write(text + "\n\n")
        print(f"Wrote {len(doc)} pages -> {out_path}  ({total_chars} chars)")
    return total_chars


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", help="path to the input PDF")
    parser.add_argument(
        "--out", required=True,
        help="path to the output .txt file (parent dir will be created)",
    )
    parser.add_argument(
        "--source-out", default=None,
        help="path for the PyMuPDF source-text dump "
             "(default: source.txt next to --out)",
    )
    args = parser.parse_args()

    # Force UTF-8 stdout on Windows so prints don't crash on non-ASCII paths.
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    extract(args.pdf, args.out)
    source_out = args.source_out or os.path.join(
        os.path.dirname(os.path.abspath(args.out)), "source.txt"
    )
    extract_source(args.pdf, source_out)


if __name__ == "__main__":
    main()
