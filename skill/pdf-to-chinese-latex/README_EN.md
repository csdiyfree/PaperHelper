# pdf-to-chinese-latex

Translate any-language academic PDF papers → an editable Chinese LaTeX
project that compiles in Overleaf with XeLaTeX, with equation numbering,
tables, figures, and bibliography preserved 1:1 with the source.

This is a [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
**skill**: once installed, telling Claude "translate this PDF to Chinese"
triggers the full 7-step workflow automatically. The seven scripts under
`scripts/` are also usable standalone (no skill / no Claude required).

For Chinese / 中文版 readme, see [`README.md`](README.md).

---

## Why this exists

Naive "AI translate this PDF" tools break in three ways:

1. **Vector figures disappear** — network diagrams, routing graphs, schematic
   illustrations are drawn as vector paths, not raster images. Standard
   PDF parsers extract nothing.
2. **Equation numbering drifts** — translators treat equations as flowing
   text. Original `(3.4)` becomes a screenshot, a typo, or vanishes.
3. **Output is a dead PDF** — you can't paste the result into your own
   thesis literature review and keep editing it.

This skill targets these three problems specifically:

- **Caption-anchored vector rendering** via PyMuPDF, with per-figure bbox
  detection + Voronoi-style assignment for multi-figure pages + auto-
  rotation for landscape figures embedded in portrait pages.
- **Equation labels are mirrored** — `\label{eq:cN}` matches source
  equation `(N)`, and `\eqref{eq:cN}` in prose keeps cross-references
  intact.
- **Output is a `.tex` project** — the deliverable is editable LaTeX,
  not a frozen PDF.

---

## Target users

- Graduate students translating English / Japanese / German / Indonesian
  / French academic papers into Chinese for thesis literature review.
- Lecturers preparing classroom Chinese versions of foreign-language
  research papers.
- Anyone producing **editable Chinese LaTeX**, not a read-only PDF.

Not a fit for: text OCR / one-line summary use cases (use lighter tools);
PDFs that are already in Chinese; conversion to Word/PowerPoint (use the
corresponding `docx` / `pptx` skills).

---

## Install

### As a Claude Code skill (recommended)

```bash
# 1. Clone into your skill directory
git clone https://github.com/<your-name>/pdf-to-chinese-latex.git \
  ~/.claude/skills/pdf-to-chinese-latex     # macOS / Linux

# Windows PowerShell
git clone https://github.com/<your-name>/pdf-to-chinese-latex.git `
  "$env:USERPROFILE\.claude\skills\pdf-to-chinese-latex"

# 2. Install Python dependencies
pip install pdfplumber pypdf PyMuPDF Pillow

# 3. Verify install
python ~/.claude/skills/pdf-to-chinese-latex/scripts/self_check.py
# expects: 6 passed, 0 failed
```

Claude Code auto-discovers the skill on next startup. Then ask Claude:

> Translate these papers into Chinese LaTeX: `~/path/to/paper1.pdf`, `~/path/to/paper2.pdf`

The 7-step workflow runs automatically.

### Standalone scripts (no Claude required)

```bash
pip install pdfplumber pypdf PyMuPDF Pillow
git clone https://github.com/<your-name>/pdf-to-chinese-latex.git
cd pdf-to-chinese-latex

# Per-paper pipeline:
python scripts/extract_text.py input.pdf --out work/raw.txt
python scripts/render_figures.py input.pdf --out work/images --auto \
    --side-margin 30 --top-margin 100        # Wiley/Elsevier flags
python scripts/extract_bibliography.py work/raw.txt --out work/_bib.tex
python scripts/extract_tables.py input.pdf --out work/_tables.tex
# ↑ paste from _figure_includes.tex + _tables.tex into main.tex
python scripts/check_tex.py work/main.tex
python scripts/compile_pdf.py work          # local xelatex if installed
```

---

## What the 7-step workflow does

1. **Ask two scoping questions** — figure handling (embed / placeholder / skip)
   and translation granularity (full / core-sections / full + glossary).
2. **Extract text per page** — `pdfplumber` writes `raw.txt` with
   `========== PAGE N ==========` delimiters.
3. **Render figures** — `PyMuPDF` does bbox + Voronoi caption assignment,
   auto-rotates sideways figures, excludes publisher watermarks, emits
   `_figure_includes.tex` (paste-ready blocks) and `_inspection.html`
   (visual checkpoint).
   - **3.5 — Visually verify every extracted figure.** Open
     `_inspection.html` in any browser; each row shows the extracted
     `figN.png` left, the source PDF page with a red overlay of the
     exact crop region right.
4. **Build project skeleton** — `{Author}{Year}_{Topic}_中文版/`
   from the `ctex` template under `references/latex_template.tex`.
5. **Translate section by section** — paragraph-by-paragraph, with
   equations / tables / algorithms / refs preserved structurally.
   `extract_bibliography.py` builds the `\bibitem` skeleton.
   `extract_tables.py` builds the `\begin{table}` skeleton.
6. **Static validation** — `check_tex.py` checks `\begin/\end` balance,
   `\cite ↔ \bibitem`, `\ref ↔ \label`, inline `$...$` parity, and that
   every `\includegraphics{X}` points at a real image file. Follows
   `\input{...}` directives.
7. **Package + handoff** — zip the `{main.tex, images/, README.md}`
   folder; user uploads to Overleaf and compiles with XeLaTeX.
8. **(Optional) Local compile** — if XeLaTeX is installed,
   `compile_pdf.py` runs two passes and outputs `main.pdf`.

---

## Tested coverage

| Publisher / format       | Tested | Notes |
|---|---|---|
| Hindawi 1-col            | ✓ canonical Tan2025 |
| Wiley 1-col              | ✓ canonical Liu2025 (rotated figures + watermarks) |
| Elsevier 1-col + 2-col   | ✓ 6 real papers; full end-to-end translation on Wang2026 + Ai2021 |
| arXiv preprint           | ✓ no publisher header; vertical arXiv stamp |
| MDPI open-access         | ✓ Creative Commons disclaimer handled |
| Springer Nature (Sci. Rep.) | ✓ 61-entry bibliography |
| Chinese-language journal | ✓ 图 N caption keywords; mixed-language bibliography |

| Structural stress fixture | What it covers |
|---|---|
| 2-column with figure in one column | Voronoi assignment + |bbox= override |
| Subfigures sharing one caption | default: one image; manual split via |bbox= |
| GB/T 7714 (Chinese) bibliography | numbered Chinese + [J]/[M]/[C]/[D] markers |
| Theorem / Lemma / Proof environments | template preloads \newtheorem in Chinese |
| Multi-page longtable | row alignment preserved across page break |
| Long caption with (a)/(b)/(c)/(d) sub-labels | one figN.png covering all panels |

Full evaluation harness in [`evals/evals.json`](evals/evals.json).

---

## Dependencies

| Package | Use | Install | License |
|---|---|---|---|
| `pdfplumber` | Text + table extraction | `pip install pdfplumber` | MIT |
| `pypdf` | Backup PDF processing | `pip install pypdf` | BSD |
| `PyMuPDF` | Vector figure rendering | `pip install PyMuPDF` | **AGPL-3.0** |
| `Pillow` | Image rotation | `pip install Pillow` | MIT |
| XeLaTeX *(optional)* | Local compile | MiKTeX / MacTeX / TeX Live | varies |

⚠ **License note**: PyMuPDF is AGPL-3.0. Using it in a commercial/closed
product requires a commercial license from Artifex. For personal/academic
use (the stated target audience) AGPL is not a concern.

Python 3.8+. Overleaf-side no setup needed — the free tier compiles XeLaTeX.

---

## Operating principles

Every change to this skill must pass three tests
(see [`SKILL.md`](SKILL.md#operating-principles-read-before-extending-this-skill)
for the full statement):

1. **Universality** — generalize the rule, not the symptom.
2. **Internalization** — a fresh user benefits without reading the
   commit history.
3. **Low friction, high quality** — sane defaults, loud failure,
   in-tool verification loops, token-conscious docs.

---

## License

MIT
