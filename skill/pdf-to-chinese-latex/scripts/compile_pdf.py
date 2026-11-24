# -*- coding: utf-8 -*-
"""Compile a Chinese LaTeX project (ctex + XeLaTeX) to PDF locally.

Runs xelatex twice (second pass resolves cross-references) on the
main.tex inside the given project directory. If the folder also holds
main_bilingual.tex (the source-above / Chinese-below edition that
\\input's main.tex), it is compiled the same way into main_bilingual.pdf.
If xelatex is not in PATH, prints installation guidance and the
Overleaf-upload fallback instead.

Usage:
    python compile_pdf.py path/to/project_folder
    python compile_pdf.py path/to/project_folder --main main.tex --keep-aux
    python compile_pdf.py path/to/project_folder --no-bilingual

By default the script removes auxiliary files (.aux/.log/.out/.toc/.synctex.gz)
after a successful compile, keeping only main.pdf next to main.tex. Pass
--keep-aux to preserve them (useful for debugging compile errors).

Requires: a local TeX distribution with XeLaTeX. Recommended:
  - Windows : winget install MiKTeX.MiKTeX
  - macOS   : brew install --cask mactex
  - Linux   : apt install texlive-xetex texlive-lang-chinese
"""

import argparse
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


AUX_SUFFIXES = (".aux", ".log", ".out", ".toc", ".synctex.gz", ".fls", ".fdb_latexmk")


def find_xelatex() -> Optional[str]:
    """Return path to xelatex.exe/xelatex, or None if not in PATH."""
    p = shutil.which("xelatex")
    if p:
        return p
    # On Windows, MikTeX may not be in current shell's PATH right after install.
    # Try common install paths as a last resort.
    if sys.platform == "win32":
        candidates = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\MiKTeX\miktex\bin\x64\xelatex.exe"),
            r"C:\Program Files\MiKTeX\miktex\bin\x64\xelatex.exe",
            r"C:\texlive\2024\bin\windows\xelatex.exe",
            r"C:\texlive\2025\bin\windows\xelatex.exe",
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
    return None


def print_install_help() -> None:
    print("[ERROR] xelatex not found on PATH.\n")
    print("To compile locally, install a TeX distribution with XeLaTeX:")
    print("  Windows : winget install MiKTeX.MiKTeX")
    print("  macOS   : brew install --cask mactex")
    print("  Linux   : sudo apt install texlive-xetex texlive-lang-chinese\n")
    print("Or, as a fallback, upload to Overleaf:")
    print("  1. Zip up {main.tex, images/, README.md}")
    print("  2. New Project -> Upload Project on https://www.overleaf.com")
    print("  3. Menu -> Compiler -> XeLaTeX -> Recompile\n")


def compile_one(project_dir: Path, main_name: str, keep_aux: bool, xelatex: str) -> int:
    main_tex = project_dir / main_name
    if not main_tex.is_file():
        print(f"[ERROR] {main_tex} does not exist")
        return 2

    print(f"[1/2] xelatex pass 1: {main_tex.name}")
    r1 = subprocess.run(
        [xelatex, "-interaction=nonstopmode", "-halt-on-error", main_name],
        cwd=str(project_dir),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r1.returncode != 0:
        print(f"[ERROR] pass 1 failed (exit={r1.returncode}). Last lines of stdout:")
        for ln in (r1.stdout or "").splitlines()[-25:]:
            print(f"    {ln}")
        return r1.returncode

    print("[2/2] xelatex pass 2 (resolve cross-references)")
    r2 = subprocess.run(
        [xelatex, "-interaction=nonstopmode", "-halt-on-error", main_name],
        cwd=str(project_dir),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r2.returncode != 0:
        print(f"[ERROR] pass 2 failed (exit={r2.returncode}). Last lines of stdout:")
        for ln in (r2.stdout or "").splitlines()[-25:]:
            print(f"    {ln}")
        return r2.returncode

    # Inspect the resulting PDF
    pdf_path = main_tex.with_suffix(".pdf")
    if not pdf_path.is_file():
        print("[ERROR] xelatex exited 0 but no PDF was written.")
        return 3

    size_kb = pdf_path.stat().st_size / 1024
    n_pages = "?"
    try:
        import fitz
        with fitz.open(str(pdf_path)) as d:
            n_pages = len(d)
    except Exception:
        pass
    print(f"[OK]  {pdf_path}  ({size_kb:.1f} KB, {n_pages} pages)")

    # Clean up aux files unless --keep-aux
    if not keep_aux:
        stem = main_tex.stem
        for sfx in AUX_SUFFIXES:
            f = project_dir / f"{stem}{sfx}"
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "project_dir",
        help="directory containing main.tex (and images/)",
    )
    parser.add_argument(
        "--main", default="main.tex",
        help="main .tex filename inside project_dir (default: main.tex)",
    )
    parser.add_argument(
        "--keep-aux", action="store_true",
        help="keep .aux/.log/.out/etc. after compile (useful for debugging)",
    )
    parser.add_argument(
        "--no-bilingual", action="store_true",
        help="skip main_bilingual.tex even if it exists",
    )
    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    xelatex = find_xelatex()
    if not xelatex:
        print_install_help()
        sys.exit(127)

    project_dir = Path(args.project_dir).resolve()
    if not project_dir.is_dir():
        print(f"[ERROR] {project_dir} is not a directory")
        sys.exit(2)

    print(f"Using xelatex: {xelatex}")
    rc = compile_one(project_dir, args.main, args.keep_aux, xelatex)
    bilingual = project_dir / "main_bilingual.tex"
    if rc == 0 and not args.no_bilingual and bilingual.is_file() \
            and bilingual.name != args.main:
        print(f"\nBilingual edition: {bilingual.name}")
        rc = compile_one(project_dir, bilingual.name, args.keep_aux, xelatex)
    sys.exit(rc)


if __name__ == "__main__":
    main()
