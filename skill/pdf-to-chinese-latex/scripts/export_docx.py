# -*- coding: utf-8 -*-
"""Export a Chinese LaTeX project (ctex) to a Word .docx via pandoc.

Converts the main.tex inside the given project directory to main.docx,
embedding figures from images/ and rendering equations as native Word
(OMML) math. If pandoc is not on PATH, prints installation guidance.

Usage:
    python export_docx.py path/to/project_folder
    python export_docx.py path/to/project_folder --main main.tex --out main.docx

Requires: pandoc (https://pandoc.org).
  - conda  : conda install -c conda-forge pandoc
  - Windows: winget install JohnMacFarlane.Pandoc
  - macOS  : brew install pandoc
  - Linux  : sudo apt install pandoc

KNOWN LOSSES (docx is the *editable* deliverable; PDF is the faithful one):
  - LaTeX auto-numbering of equations/figures/tables (\\label/\\ref/\\eqref)
    does NOT survive to docx. In-text "see Eq. (8)" cross-references may
    render blank or as '?'. Numbering must be re-applied by hand in Word.
  - thebibliography renders as a plain list (no [N] back-links).
  - multirow tables and algorithm pseudocode may misalign.
For a pixel-faithful copy with all numbering intact, use compile_pdf.py.
"""

import argparse
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def find_pandoc() -> Optional[str]:
    """Return path to pandoc, or None if not found."""
    p = shutil.which("pandoc")
    if p:
        return p
    # Common install paths as a last resort (PATH may be stale right after install).
    if sys.platform == "win32":
        candidates = [
            os.path.expandvars(r"%LOCALAPPDATA%\Pandoc\pandoc.exe"),
            os.path.expandvars(r"%ProgramFiles%\Pandoc\pandoc.exe"),
            os.path.expandvars(r"%USERPROFILE%\miniconda3\Library\bin\pandoc.exe"),
            os.path.expandvars(r"%USERPROFILE%\miniconda3\Scripts\pandoc.exe"),
            os.path.expandvars(r"%USERPROFILE%\anaconda3\Library\bin\pandoc.exe"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
    return None


def print_install_help() -> None:
    print("[ERROR] pandoc not found on PATH.\n")
    print("To export Word, install pandoc:")
    print("  conda   : conda install -c conda-forge pandoc")
    print("  Windows : winget install JohnMacFarlane.Pandoc")
    print("  macOS   : brew install pandoc")
    print("  Linux   : sudo apt install pandoc\n")
    print("Or, as a fallback, compile a faithful PDF instead:")
    print("  python compile_pdf.py <project_folder>\n")


def export_one(project_dir: Path, main_name: str, out_name: str, pandoc: str) -> int:
    main_tex = project_dir / main_name
    if not main_tex.is_file():
        print(f"[ERROR] {main_tex} does not exist")
        return 2

    out_path = project_dir / out_name
    images_dir = project_dir / "images"

    # resource-path lets pandoc resolve \includegraphics{figN.png} that rely on
    # \graphicspath{{images/}} — search both the project root and images/.
    resource_path = os.pathsep.join([str(project_dir), str(images_dir)])

    cmd = [
        pandoc,
        main_name,
        "-f", "latex",
        "-t", "docx",
        "-o", out_name,
        "--resource-path", resource_path,
        "--wrap=preserve",
    ]
    print(f"[1/1] pandoc: {main_tex.name} -> {out_name}")
    r = subprocess.run(
        cmd,
        cwd=str(project_dir),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    # pandoc prints warnings (unknown commands, dropped refs) to stderr but
    # still produces a usable docx — surface them without treating as fatal.
    warn = (r.stderr or "").strip()
    if warn:
        shown = warn.splitlines()
        print(f"  pandoc warnings ({len(shown)} line(s), first 15):")
        for ln in shown[:15]:
            print(f"    {ln}")

    if r.returncode != 0:
        print(f"[ERROR] pandoc failed (exit={r.returncode}).")
        return r.returncode

    if not out_path.is_file():
        print("[ERROR] pandoc exited 0 but no .docx was written.")
        return 3

    size_kb = out_path.stat().st_size / 1024
    print(f"[OK]  {out_path}  ({size_kb:.1f} KB)")
    print("\nNote: docx is the EDITABLE deliverable. Equation/figure cross-")
    print("references and auto-numbering may be lost — re-apply in Word, or")
    print("use compile_pdf.py for a faithful PDF with numbering intact.")
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
        "--out", default="main.docx",
        help="output .docx filename (default: main.docx)",
    )
    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    pandoc = find_pandoc()
    if not pandoc:
        print_install_help()
        sys.exit(127)

    project_dir = Path(args.project_dir).resolve()
    if not project_dir.is_dir():
        print(f"[ERROR] {project_dir} is not a directory")
        sys.exit(2)

    print(f"Using pandoc: {pandoc}")
    sys.exit(export_one(project_dir, args.main, args.out, pandoc))


if __name__ == "__main__":
    main()
