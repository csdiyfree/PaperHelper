# Figure extraction — how `render_figures.py` decides what to crop

This document goes one level deeper than SKILL.md Step 3. Read it only when
`_inspection.html` shows a figure that was cropped wrong and you need to
understand which knob to turn.

## The pipeline in one paragraph

For each figure number N the script (a) locates a caption rect via keyword
search + text-density disambiguation, (b) collects every raster XObject and
every vector drawing ≥30pt on the same page as bbox candidates, (c) assigns
each candidate to its nearest caption via a Voronoi-style rule, (d) takes
the bbox union of all candidates owned by the current caption as the clip,
(e) auto-detects whether the clip's text reads sideways and rotates the
rendered PNG back to upright, and (f) auto-detects publisher watermarks
near the page edges and shrinks the clip to exclude them. The final
pixmap is rendered at 3× scale (≈216 DPI) and saved as `fig{N}.png`.

## Pitfall 1 — In-text reference vs real caption

`Figure 5` may appear inside body text ("as shown in Figure 5"), inside
another figure's caption text, AND under the actual figure ("Figure 5.
Route of Scenario 2"). Naive `search_for` returns all of them.

The script disambiguates by **text density in the 100pt strip immediately
above each candidate** — a real caption sits below an image (sparse /
empty above), an in-text reference is embedded in body prose (dense
above). The lowest-density candidate wins. This handles both common
layouts correctly:

- Figure placed *after* its first text mention (Tan-style): real caption is
  the lowest hit on the page.
- Figure placed *before* its first text mention (Liu-style page 7, where
  Fig.3 / 4 / 5 stack at the top of the page and the body-text discussion
  is at the bottom): real caption is HIGHER than the in-text refs, but
  still has the least text above it.

If the heuristic still picks wrong (e.g. the figure is wrapped by a table
or a sidebar block), override with `|bbox=x0,y0,x1,y1` after the keyword:

```
--figs "5:11:Figure 5.|bbox=50,200,540,560"
```

## Pitfall 2 — Landscape figures rotated 90° into a portrait page

Large multi-panel comparisons and wide network diagrams are often rotated
90° when embedded in a portrait journal page. Rendering the clip as-is
leaves the figure sideways.

The script reads `line.dir` from text spans inside the clip — if ≥60% of
characters point a non-horizontal direction, the rendered PNG is
auto-rotated back to upright (uses Pillow, a transitive pdfplumber
dependency, so no extra install is required). Force or disable via per-fig
`|rotate=N` (N = 0/90/180/270 CW):

```
--figs "9:24:Fig. 9.|rotate=90"     # force 90° CW
--figs "9:24:Fig. 9.|rotate=0"      # disable auto-rotation
```

## Pitfall 3 — Publisher watermarks (Wiley / Elsevier / IEEE)

These journals stamp a vertical "Downloaded from onlinelibrary.wiley.com"
sidebar on the right edge and a journal-header band at the top of each
page. Both intrude into figure crops if you take the clip as-is.

`render_figures.py` knows a short list of watermark strings and shrinks the
clip rect to avoid any bbox within 80pt of a page edge that matches one of
them. For stubborn cases (watermark uses an unlisted phrase or sits further
inside the page), pass:

- `--side-margin 30` — drops 30pt from each horizontal edge of the clip.
- `--top-margin 100` — raises the floor of the first-figure-on-page clip
  from 72pt to 100pt.

Recommended baseline for Wiley papers (all 9 Liu2025 figures clean
after this):

```bash
python scripts/render_figures.py "<pdf>" --out images \
    --side-margin 30 --top-margin 100 --auto
```

`--top-margin` and `--side-margin` apply ONLY to the caption-anchored
fallback path. The primary bbox-based path uses the figure's real extent
and ignores them. Watermark auto-exclusion runs in both paths.

## Pitfall 4 — No caption keyword matched

If a caption keyword isn't found on its expected page, the script falls
back to rendering the full page. That's a useful signal in the output —
re-check the page number and try alternative keywords (`Figure 1.` vs
`Fig. 1.`, with/without trailing period).

The script automatically tries several common variants
(`Fig. N.`, `Figure N.`, `FIGURE N`, `Abb. N.`), so a single keyword
like `Fig. 1.` will still find a paper that uses `Figure 1.`.

## When all heuristics fail: bbox override

The hard escape hatch for any figure the heuristics get wrong is
`|bbox=x0,y0,x1,y1` after the keyword. Coordinates are in PDF points
(72 pt = 1 inch). Read them off the inspection HTML's clip meta (e.g.
`clip = (113, 100) → (480, 380)`) and adjust by 10~30 pt as needed.

For deeper failure modes (two-column layouts, subfigures with their own
captions, etc.), see `troubleshooting.md`.
