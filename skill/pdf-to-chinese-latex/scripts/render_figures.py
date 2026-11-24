# -*- coding: utf-8 -*-
"""Render figures from a PDF by caption-anchored cropping.

For each (fig_num, page_index_1based, caption_keyword) tuple, locate the
caption text on the page, find the figure content (images, vector
drawings and the figure's own text labels) next to it, and render that
region as a PNG at 3x scale (~216 DPI). The printed caption itself is
NOT part of the crop — the translation typesets it with \\caption{}.

Two robustness features versus the naive caption search:

1. **Caption-shaped hit wins**: when the keyword has multiple hits (very
   common: in-text reference "as shown in Figure 5." besides the actual
   caption "Figure 5: <description>"), a hit that opens its text line
   beats a mid-sentence one; ties go to the hit with the least text in
   the strip above it (a caption sits under its figure, a mention sits
   in prose). Anchors that only matched a weak / in-text form are
   reported as WARN in the summary.

2. **Watermark exclusion**: publisher download stamps (Wiley / Elsevier /
   IEEE) are detected by string search and the crop rect is shrunk to
   avoid intersecting them. The script also honours `--side-margin` and
   `--top-margin` CLI flags for manual override on stubborn PDFs.

Per-figure manual bbox / rotation overrides are available as suffixes
on a `--figs` entry (entries are separated by the comma that starts the
next `N:page:` entry, so a bbox's own commas are fine), e.g.:

    --figs "5:13:Figure 5.|bbox=50,200,540,600,9:24:Fig. 9.|rotate=90"

Usage:
    python render_figures.py input.pdf --out images_dir \
        --figs "1:3:Fig. 1.,2:5:Fig. 2." \
        [--side-margin 30] [--top-margin 80] [--scale 3.0]

Requires: PyMuPDF (`pip install PyMuPDF`).
"""

import argparse
import io
import os
import re
import sys
from html import escape as html_escape
from typing import Dict, List, Optional, Tuple

import fitz  # PyMuPDF


# Publisher watermark / page-header strings worth auto-excluding from the
# crop rect. Keep this list short and high-precision so we don't shrink
# the crop based on text that legitimately belongs inside a figure.
WATERMARK_PATTERNS = (
    "Downloaded from",
    "onlinelibrary.wiley",
    "Wiley Online Library",
    "ScienceDirect.com",
    "ScienceDirect",
    "IEEE Xplore",
    "Creative Commons License",
    "Springer Nature",
)


def parse_figs(
    spec: str,
) -> List[Tuple[int, int, str, Optional[fitz.Rect], Optional[int]]]:
    """Parse `--figs` into (fig_num, page_1based, keyword, optional bbox, optional rotate).

    Allowed suffix syntax on any entry (in any order, pipe-separated):
        |bbox=x0,y0,x1,y1   manual crop rect override (PDF points)
        |rotate=N           force CW rotation N degrees (0/90/180/270);
                            otherwise auto-detected from text direction
    """
    out = []
    # Split only at a comma that starts a new `N:page:` entry, so the commas
    # inside `|bbox=x0,y0,x1,y1` stay with their entry.
    for chunk in re.split(r",(?=\s*\d+\s*:\s*\d+\s*:)", spec):
        chunk = chunk.strip()
        if not chunk:
            continue
        bbox: Optional[fitz.Rect] = None
        rotate: Optional[int] = None
        # Strip suffixes
        while "|" in chunk:
            head, suffix = chunk.rsplit("|", 1)
            if suffix.startswith("bbox="):
                try:
                    x0, y0, x1, y1 = (float(s) for s in suffix[5:].split(","))
                    bbox = fitz.Rect(x0, y0, x1, y1)
                except ValueError as e:
                    raise ValueError(f"bad bbox in {chunk!r}: {e}") from e
                chunk = head
            elif suffix.startswith("rotate="):
                try:
                    rotate = int(suffix[7:]) % 360
                    if rotate not in (0, 90, 180, 270):
                        raise ValueError(f"rotate must be 0/90/180/270 (got {rotate})")
                except ValueError as e:
                    raise ValueError(f"bad rotate in {chunk!r}: {e}") from e
                chunk = head
            else:
                break  # unknown suffix, leave as part of keyword
        parts = chunk.split(":", 2)
        if len(parts) != 3:
            raise ValueError(f"bad --figs entry: {chunk!r} (want fig_num:page:keyword)")
        fig_num = int(parts[0])
        page = int(parts[1])
        keyword = parts[2]
        out.append((fig_num, page, keyword, bbox, rotate))
    return out


# ---------------------------------------------------------------------------
# Caption-keyword variants used everywhere captions are located. Strong forms
# carry a definite caption terminator (period / colon / full-width colon) —
# the standard caption patterns across publishers + Chinese-language papers.
# Weak forms are fallbacks for papers that drop the terminator.
#
# Languages covered explicitly:
#   - English / German:  Fig. N. / Figure N. / Abb. N. / FIGURE N.
#                        Fig. N: / Figure N: (NeurIPS / ICML / arXiv style)
#   - Chinese:           图 N. / 图N. / 图 N: / 图N：(full-width colon)
#
# Chinese support added after real-world tests on Chinese-source journals
# (中国管理科学 / 系统工程理论与实践 / 控制与决策 etc) revealed that English
# keyword search returns 0 hits on those papers.
# ---------------------------------------------------------------------------
_STRONG_KW_FORMS: Tuple[str, ...] = (
    "Fig. {n}.", "Figure {n}.", "Fig {n}.", "FIGURE {n}.", "Abb. {n}.",
    "Fig. {n}:", "Figure {n}:", "Fig {n}:", "FIGURE {n}:", "Abb. {n}:",
    "图 {n}.", "图{n}.", "图 {n}:", "图{n}:", "图 {n}：", "图{n}：",
)
_WEAK_KW_FORMS: Tuple[str, ...] = (
    "Fig. {n} ", "Figure {n} ", "FIGURE {n} ",
    "图 {n} ", "图{n} ",
)


def _strong_keywords(fig_num: int, user_extra: Optional[str] = None) -> List[str]:
    """Materialize strong keyword candidates for `fig_num`, deduped, in order."""
    out = []
    seen = set()
    for kw in ([user_extra] if user_extra else []) + [
        f.format(n=fig_num) for f in _STRONG_KW_FORMS
    ]:
        if kw and kw not in seen:
            seen.add(kw)
            out.append(kw)
    return out


def _weak_keywords(fig_num: int) -> List[str]:
    return [f.format(n=fig_num) for f in _WEAK_KW_FORMS]


def _is_strong(kw: str) -> bool:
    """A keyword is strong when it ends in a caption terminator (. : ：)."""
    return kw.rstrip()[-1:] in (".", ":", "：")


# ---------------------------------------------------------------------------
# Per-page memoization. PyMuPDF's search_for / get_text / get_drawings /
# get_images are non-trivial and the multi-figure pipeline hits each page
# many times. A dict keyed by id(page) (cleared between PDFs) collapses
# repeated lookups to a single call.
# ---------------------------------------------------------------------------
_PAGE_CACHE: Dict[Tuple[int, int], Dict[str, object]] = {}


def _clear_page_cache() -> None:
    _PAGE_CACHE.clear()
    _BODY_SIZE.clear()


def _page_slot(page: "fitz.Page") -> Dict[str, object]:
    # Key by (doc-id, page-number) — id(page) is unstable across calls
    # because PyMuPDF may return new wrapper objects whose ids collide
    # with garbage-collected prior wrappers, causing cache pollution.
    key = (id(page.parent), page.number)
    slot = _PAGE_CACHE.get(key)
    if slot is None:
        slot = _PAGE_CACHE[key] = {}
    return slot


def _search_for(page: "fitz.Page", kw: str) -> List[fitz.Rect]:
    """Memoized `page.search_for(kw, quads=False)`."""
    slot = _page_slot(page)
    cache: Dict[str, List[fitz.Rect]] = slot.setdefault("search", {})  # type: ignore[assignment]
    if kw not in cache:
        cache[kw] = page.search_for(kw, quads=False)
    return cache[kw]


def _page_lines(page: "fitz.Page") -> List[tuple]:
    """Cached text lines of `page` as
    (bbox, rounded dir, text, block index, max font size, all-bold)."""
    slot = _page_slot(page)
    cached = slot.get("lines")
    if cached is None:
        cached = []
        for bi, b in enumerate(page.get_text("dict").get("blocks", [])):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                d = ln.get("dir", (1, 0))
                spans = [sp for sp in ln.get("spans", []) if sp.get("text", "").strip()]
                text = "".join(sp.get("text", "") for sp in ln.get("spans", []))
                size = max((sp.get("size", 0.0) for sp in spans), default=0.0)
                bold = bool(spans) and all(sp.get("flags", 0) & 16 for sp in spans)
                cached.append((fitz.Rect(ln["bbox"]), (round(d[0]), round(d[1])),
                               text, bi, size, bold))
        slot["lines"] = cached
    return cached


_BODY_SIZE: Dict[int, float] = {}


def _body_font_size(doc: "fitz.Document") -> float:
    """Most common font size in the document, weighted by characters."""
    key = id(doc)
    if key not in _BODY_SIZE:
        counts: Dict[float, int] = {}
        for page in doc:
            for line in _page_lines(page):
                sz = round(line[4], 1)
                counts[sz] = counts.get(sz, 0) + len(line[2].strip())
        _BODY_SIZE[key] = max(counts, key=counts.get) if counts else 10.0
    return _BODY_SIZE[key]


def _line_of(page: "fitz.Page", r: fitz.Rect):
    """The text line holding hit rect `r` (by its centre), or None."""
    cx, cy = _rect_center(r)
    for line in _page_lines(page):
        bbox = line[0]
        if bbox.x0 - 1 <= cx <= bbox.x1 + 1 and bbox.y0 - 1 <= cy <= bbox.y1 + 1:
            return line
    return None


def _starts_line(page: "fitz.Page", r: fitz.Rect, kw: str) -> bool:
    """True when the hit opens its text line — the shape of a caption label
    ("Figure 3: ...") rather than an in-text mention ("... in Figure 3.")."""
    line = _line_of(page, r)
    if line is None:
        return False
    bbox, d, text = line[:3]
    if not text.lstrip().lower().startswith(kw.strip().lower()):
        return False
    return d != (1, 0) or r.x0 - bbox.x0 < 3


_DIR_TO_ROTATION = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}


# ---------------------------------------------------------------------------
# Rect math helpers — used in several places for Voronoi assignment.
# ---------------------------------------------------------------------------
def _rect_center(r: fitz.Rect) -> Tuple[float, float]:
    return ((r.x0 + r.x1) / 2.0, (r.y0 + r.y1) / 2.0)


def _rect_dist(r1: fitz.Rect, r2: fitz.Rect) -> float:
    x1, y1 = _rect_center(r1)
    x2, y2 = _rect_center(r2)
    return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5


def _rect_gap(r1: fitz.Rect, r2: fitz.Rect) -> float:
    """Shortest distance between the edges of two rects (0 if they touch)."""
    dx = max(0.0, r1.x0 - r2.x1, r2.x0 - r1.x1)
    dy = max(0.0, r1.y0 - r2.y1, r2.y0 - r1.y1)
    return (dx * dx + dy * dy) ** 0.5


# ---------------------------------------------------------------------------
# Caption-candidate scoring shared by find_caption_rect and auto_discover.
# ---------------------------------------------------------------------------
def _hit_rank(page: "fitz.Page", r: fitz.Rect, kw: str) -> Tuple[bool, bool, int, float]:
    """Sort key for a caption candidate; lower = more caption-like.

    A hit that opens its text line beats an in-text mention; then a strong
    form (terminator) beats a weak one; then the sparsest strip above it
    (caption under a figure vs. mention inside prose); then the topmost.
    """
    return (not _starts_line(page, r, kw), not _is_strong(kw),
            _text_density_above(page, r), r.y0)


def _pick_best_on_page(page: "fitz.Page", keywords: List[str]) -> Optional[Tuple[fitz.Rect, str]]:
    """Among all hits of `keywords` on `page`, return the most caption-like.

    Returns (rect, keyword) or None if no keyword matched.
    """
    hits = [(r, kw) for kw in keywords for r in _search_for(page, kw)]
    if not hits:
        return None
    return min(hits, key=lambda h: _hit_rank(page, h[0], h[1]))


def find_all_caption_rects_on_page(
    page: "fitz.Page",
    max_fig: int = 50,
) -> List[fitz.Rect]:
    """Return one caption rect per figure number found on this page.

    Used to give `find_figure_region_near` enough context to do
    Voronoi-style assignment when several figures share a page. Result is
    memoized per page.
    """
    slot = _page_slot(page)
    cached = slot.get(("all_caps", max_fig))
    if cached is not None:
        return cached  # type: ignore[return-value]

    caps: List[fitz.Rect] = []
    consec_miss = 0
    for n in range(1, max_fig + 1):
        hit = _pick_best_on_page(
            page, _strong_keywords(n) + _weak_keywords(n)
        )
        if hit is None:
            consec_miss += 1
            # Only give up after a long run of consecutive misses AND
            # we've found at least one caption (don't break early on a
            # page whose first figure has a high number).
            if consec_miss >= 10 and caps:
                break
        else:
            consec_miss = 0
            caps.append(hit[0])
    slot[("all_caps", max_fig)] = caps
    return caps


def _caption_direction(page: "fitz.Page", cap: fitz.Rect) -> Tuple[int, int]:
    """Return rounded text-direction vector of the caption's first text line."""
    pad = 4
    probe = fitz.Rect(cap.x0 - pad, cap.y0 - pad, cap.x1 + pad, cap.y1 + pad)
    info = page.get_text("dict", clip=probe)
    for b in info.get("blocks", []):
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            d = line.get("dir", (1, 0))
            return (round(d[0]), round(d[1]))
    return (1, 0)


def _figure_candidates(page: "fitz.Page", min_dim: float = 30.0) -> List[fitz.Rect]:
    """Cached list of raster + vector-drawing bboxes on `page` that are big
    enough to be figure content (not decorative strokes / underlines).
    """
    slot = _page_slot(page)
    key = ("candidates", min_dim)
    cached = slot.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    out: List[fitz.Rect] = []
    # Raster image XObjects.
    for img_info in page.get_images(full=True):
        try:
            xref = img_info[0]
            for r in page.get_image_rects(xref):
                if r.width >= min_dim and r.height >= min_dim:
                    out.append(fitz.Rect(r))
        except Exception:
            pass
    # Vector drawings — many academic figures are pure vector.
    try:
        for d in page.get_drawings():
            r = d.get("rect")
            if r is None:
                continue
            r = fitz.Rect(r)
            if r.width >= min_dim and r.height >= min_dim:
                out.append(r)
    except Exception:
        pass
    slot[key] = out
    return out


def find_figure_region_near(
    page: "fitz.Page",
    anchor: fitz.Rect,
    other_captions: Optional[List[fitz.Rect]] = None,
    max_center_distance: float = 350.0,
    min_dim: float = 30.0,
) -> Optional[fitz.Rect]:
    """Find the bbox of the actual figure content (raster or vector) for
    the caption at `anchor`.

    Universal alternative to caption-anchored cropping: returns the bbox
    of the real image/drawing union assigned to this caption. Robust
    against:

    * Rotated figures (caption sits to the side of the figure rather than
      below — caption-anchored cropping misses the bottom half).
    * Multi-figure pages (each caption gets its own nearby image cluster
      via nearest-caption Voronoi assignment).
    * Wide-margin layouts (figure may not span the full text column).

    Strategy: collect raster image bboxes + large vector-drawing bboxes.
    For each candidate, find its NEAREST caption among
    `[anchor] + other_captions`; keep only candidates whose nearest
    caption is `anchor`. Union those and return.
    """
    candidates = _figure_candidates(page, min_dim)
    if not candidates:
        return None

    captions = [anchor] + list(other_captions or [])

    # Assignment policy: for HORIZONTAL captions (normal layout, figure
    # above caption), use "closest caption strictly below the candidate"
    # — this respects the natural reading order even when sub-figures
    # of one caption are geographically closer to a different caption's
    # text. For ROTATED captions, fall back to Euclidean Voronoi.
    horizontal = (_caption_direction(page, anchor) == (1, 0))

    near: List[fitz.Rect] = []
    for r in candidates:
        # Far-away candidates (logos, other columns) are skipped, but a big
        # figure whose edge sits right at its caption counts even when its
        # centre is far away (a full-page image).
        if _rect_dist(r, anchor) > max_center_distance and _rect_gap(r, anchor) > 36:
            continue
        if horizontal:
            cand_y_bot = r.y1
            # Captions at-or-below the candidate's bottom (with small
            # tolerance so a caption sitting on the figure block counts).
            below = [c for c in captions if c.y0 >= cand_y_bot - 8]
            if below:
                nearest = min(below, key=lambda c: c.y0 - cand_y_bot)
            else:
                nearest = min(captions, key=lambda c: _rect_dist(r, c))
        else:
            nearest = min(captions, key=lambda c: _rect_dist(r, c))
        if nearest is anchor:
            near.append(r)

    if not near:
        return None

    union = fitz.Rect(near[0])
    for r in near[1:]:
        union |= r
    return union


# A text block that opens with a figure / table label is a caption, never
# figure content (it must not be pulled into a neighbouring crop).
_CAPTION_START = re.compile(r"^\s*(fig\.?|figure|abb\.?|table|tab\.|图|表)\s*\d", re.IGNORECASE)


def _attachable_text(page: "fitz.Page") -> List[fitz.Rect]:
    """Text lines that may be figure labels: everything except prose
    paragraphs (a block with a line wider than 45% of the page carrying
    40+ chars), caption blocks and headings (bold and larger than the
    body text). Cached per page."""
    slot = _page_slot(page)
    cached = slot.get("label_lines")
    if cached is not None:
        return cached  # type: ignore[return-value]
    lines = _page_lines(page)
    pw = page.rect.width
    body = _body_font_size(page.parent)
    skip = set()
    first_text: Dict[int, str] = {}
    for bbox, d, text, bi, size, bold in lines:
        first_text.setdefault(bi, text)
        if d == (1, 0) and bbox.width > 0.45 * pw and len(text.strip()) >= 40:
            skip.add(bi)
        if bold and size > body + 0.5:
            skip.add(bi)
    skip |= {bi for bi, t in first_text.items() if _CAPTION_START.match(t)}
    cached = [line[0] for line in lines if line[3] not in skip]
    slot["label_lines"] = cached
    return cached


def _caption_free_area(
    page: "fitz.Page", region: fitz.Rect, cap: fitz.Rect,
) -> Tuple[fitz.Rect, str]:
    """Return (area, side): the page area on the figure's side of its
    caption, so neither the crop nor its growth reaches into the printed
    caption, and the side ('below' / 'above' / 'right' / 'left') the
    caption sits on relative to `region`."""
    line = _line_of(page, cap)
    cap_line = line[0] if line else fitz.Rect(cap)
    blk = fitz.Rect(cap_line)
    if line:
        for other in _page_lines(page):
            bbox, bi = other[0], other[3]
            if bi == line[3]:
                blk = fitz.Rect(min(blk.x0, bbox.x0), min(blk.y0, bbox.y0),
                                max(blk.x1, bbox.x1), max(blk.y1, bbox.y1))
    gaps = {  # side the caption sits on, relative to the figure
        "below": blk.y0 - region.y1, "above": region.y0 - blk.y1,
        "right": blk.x0 - region.x1, "left": region.x0 - blk.x1,
    }
    side = max(gaps, key=gaps.get)
    area = fitz.Rect(page.rect)
    # The caption's first line faces the figure when the caption follows it
    # (below, or beside a figure rotated together with its caption).
    if side == "below":
        area.y1 = cap_line.y0 - 1
    elif side == "above":
        area.y0 = blk.y1 + 1
    elif side == "right":
        area.x1 = cap_line.x0 - 1
    else:
        area.x0 = cap_line.x1 + 1
    return area, side


def _grow_region(
    page: "fitz.Page", seed: fitz.Rect, area: fitz.Rect, side: str = "below",
    gap: float = 6.0, max_passes: int = 80,
) -> fitz.Rect:
    """Grow `seed` by every image, vector drawing (any size) and label-like
    text line within `gap` pt of it, repeatedly. Picks up axis / node
    labels and thin strokes that lie outside the big candidates, e.g.
    vertical word labels of attention maps. Toward the caption (`side`)
    the reach is unlimited: whatever sits between a figure and its own
    caption belongs to the figure (x-axis labels, legends). Only items
    fully inside `area` (the caption-free side) count; prose paragraphs,
    headings and captions never do.
    """
    far = 1e9
    reach = {"below": (gap, gap, gap, far), "above": (gap, far, gap, gap),
             "right": (gap, gap, far, gap), "left": (far, gap, gap, gap)}[side]
    pw, ph = page.rect.width, page.rect.height
    items = [r for r in _figure_candidates(page, 0.0)
             if not (r.width > 0.9 * pw and r.height > 0.9 * ph)]
    items += _attachable_text(page)
    tol = 0.5
    items = [r for r in items
             if r.x0 >= area.x0 - tol and r.y0 >= area.y0 - tol
             and r.x1 <= area.x1 + tol and r.y1 <= area.y1 + tol]
    x0, y0, x1, y1 = seed.x0, seed.y0, seed.x1, seed.y1
    for _ in range(max_passes):
        rest = []
        for r in items:
            if (r.x0 <= x1 + reach[2] and r.x1 >= x0 - reach[0]
                    and r.y0 <= y1 + reach[3] and r.y1 >= y0 - reach[1]):
                x0, y0 = min(x0, r.x0), min(y0, r.y0)
                x1, y1 = max(x1, r.x1), max(y1, r.y1)
            else:
                rest.append(r)
        if len(rest) == len(items):
            break
        items = rest
    return fitz.Rect(x0, y0, x1, y1)


def _keep_pad_off_text(page: "fitz.Page", region: fitz.Rect, clip: fitz.Rect) -> fitz.Rect:
    """Pull the padded `clip` back to `region` wherever the padding would
    reach into a text line that did not join the figure (heading, prose,
    caption), so no sliver of it shows at the crop's edge."""
    joined = set(map(tuple, _attachable_text(page)))
    x0, y0, x1, y1 = clip.x0, clip.y0, clip.x1, clip.y1
    for line in _page_lines(page):
        r = line[0]
        if tuple(r) in joined or not r.intersects(clip):
            continue
        cx, cy = _rect_center(r)
        if r.x1 > region.x0 and r.x0 < region.x1:      # above / below
            if cy < region.y0:
                y0 = max(y0, min(r.y1, region.y0))
            elif cy > region.y1:
                y1 = min(y1, max(r.y0, region.y1))
        if r.y1 > region.y0 and r.y0 < region.y1:      # left / right
            if cx < region.x0:
                x0 = max(x0, min(r.x1, region.x0))
            elif cx > region.x1:
                x1 = min(x1, max(r.x0, region.x1))
    return fitz.Rect(x0, y0, x1, y1)


def detect_clip_rotation(page: "fitz.Page", clip: fitz.Rect) -> int:
    """Return the CW rotation in degrees (0/90/180/270) needed so that the
    rendered crop reads upright.

    Inspects line.dir vectors of all text spans inside the clip:
        (1, 0)  → text reads left-to-right normally     → 0
        (0, -1) → text reads bottom-to-top (90° CCW)    → 90 CW to flip back
        (-1, 0) → text is upside-down                   → 180
        (0, 1)  → text reads top-to-bottom (90° CW)     → 270 CW to flip back
    """
    text = page.get_text("dict", clip=clip)
    weights = {0: 0, 90: 0, 180: 0, 270: 0}
    for b in text.get("blocks", []):
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            dx, dy = line.get("dir", (1, 0))
            length = sum(len(s.get("text", "")) for s in line.get("spans", []))
            if dx > 0.5:
                weights[0] += length
            elif dy < -0.5:
                weights[90] += length
            elif dx < -0.5:
                weights[180] += length
            elif dy > 0.5:
                weights[270] += length
    total = sum(weights.values())
    if total == 0:
        return 0
    dominant = max(weights, key=weights.get)
    # Require >60% of chars to agree, else assume mixed/decorative and don't rotate.
    if weights[dominant] / total < 0.6:
        return 0
    return dominant


def suggest_latex_width(aspect: float) -> Tuple[float, str]:
    """Map a figure's width/height aspect to a sane LaTeX \\includegraphics width.

    Returns (width_fraction, classification). The rule keeps every figure
    at roughly 60-70% of the typeset page area, leaving room for caption +
    surrounding paragraphs on the same page so LaTeX's float algorithm
    doesn't defer the figure to the end of the document.

        aspect ≥ 2.5  → 0.85\\textwidth  (very wide banner)
        1.5 ≤ < 2.5   → 0.75\\textwidth  (wide / standard landscape)
        0.9 ≤ < 1.5   → 0.65\\textwidth  (square-ish)
        < 0.9         → 0.55\\textwidth  (portrait — fits next to text)
    """
    if aspect >= 2.5:
        return 0.85, "very wide"
    if aspect >= 1.5:
        return 0.75, "wide landscape"
    if aspect >= 0.9:
        return 0.65, "square-ish"
    return 0.55, "portrait"


def write_figure_includes_tex(
    out_dir: str,
    rendered: List[Tuple[int, str, int, fitz.Rect]],
) -> None:
    """Write `_figure_includes.tex` next to images/ — a paste-ready set of
    `\\begin{figure}[!htbp] ... \\end{figure}` blocks, one per rendered
    figure, with widths chosen from each image's aspect ratio.
    """
    try:
        from PIL import Image
    except ImportError:
        return  # Pillow missing → silent skip; the .png files are still good
    # Place the .tex sidecar at the parent of images/ so the translator can
    # find it next to main.tex. If out_dir IS the images/ folder, drop it
    # one level up; otherwise drop it inside out_dir.
    parent = out_dir
    if os.path.basename(os.path.normpath(out_dir)).lower() == "images":
        parent = os.path.dirname(os.path.normpath(out_dir)) or "."
    sidecar = os.path.join(parent, "_figure_includes.tex")

    lines = [
        "% ============================================================",
        "% Auto-generated by render_figures.py.",
        "% Paste each block into main.tex where the figure should appear,",
        "% then fill in the TODO caption (source caption without its",
        "% 'Figure N:' label + Chinese, ending with 图中文字：... when the",
        "% figure contains text) and \\label. Width is chosen from",
        "% each PNG's aspect ratio so the figure fits on a shared page and",
        "% doesn't get deferred to the end of the document by LaTeX float.",
        "% ============================================================",
        "",
    ]
    for fig_num, png_path, _page, _clip in rendered:
        try:
            with Image.open(png_path) as img:
                aspect = img.width / img.height if img.height else 1.0
        except Exception:
            aspect = 1.0
        width, classification = suggest_latex_width(aspect)
        # Path used inside \includegraphics: just basename (LaTeX adds dir
        # from \graphicspath{{images/}} in the template).
        rel = os.path.basename(png_path)
        lines.append(
            f"% fig{fig_num}: aspect={aspect:.2f} ({classification}) → "
            f"width={width}\\textwidth"
        )
        lines.append("\\begin{figure}[!htbp]")
        lines.append("\\centering")
        lines.append(f"\\includegraphics[width={width}\\textwidth]{{{rel}}}")
        lines.append("\\caption{\\bihead{TODO source caption}{TODO 中文图题}}")
        lines.append(f"\\label{{fig:fig{fig_num}}}")
        lines.append("\\end{figure}")
        lines.append("")
    with open(sidecar, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote LaTeX include sidecar → {sidecar}")


# Scale used for the inspection page thumbnails: 1 PDF pt = 1.5 CSS px.
# Chosen so the overlay rect math is the simple `* INSPECT_SCALE`.
_INSPECT_SCALE = 1.5


def write_inspection_html(
    out_dir: str,
    rendered: List[Tuple[int, str, int, fitz.Rect]],
    doc: "fitz.Document",
    flags: Optional[Dict[int, List[Tuple[str, str]]]] = None,
) -> None:
    """Emit `_inspection.html` next to images/: a side-by-side gallery
    showing each extracted `figN.png` against a thumbnail of the source
    PDF page with a red rectangle overlaying the exact crop region.

    Purpose: let the translator (or LLM agent) **visually verify in 30
    seconds** that every cropped figure actually matches its caption
    before declaring the extraction done. If a figure looks wrong, the
    troubleshooting entry on `|bbox=x0,y0,x1,y1` overrides is the next
    step.

    Heuristics in the bbox / Voronoi / rotation pipeline are tuned for
    common layouts but cannot enumerate every paper's edge cases — this
    sidecar closes the loop.
    """
    if not rendered:
        return
    images_dir = out_dir
    parent = out_dir
    if os.path.basename(os.path.normpath(out_dir)).lower() == "images":
        parent = os.path.dirname(os.path.normpath(out_dir)) or "."
    sidecar = os.path.join(parent, "_inspection.html")
    flags = flags or {}

    # Render each unique source page once as `_inspect_pNN.png` NEXT TO the
    # HTML (not inside images/, which is what gets zipped for Overleaf).
    # Thumbnails an older version left in images/ are removed.
    if os.path.abspath(parent) != os.path.abspath(images_dir):
        for name in os.listdir(images_dir):
            if name.startswith("_inspect_p") and name.endswith(".png"):
                os.remove(os.path.join(images_dir, name))
    unique_pages = sorted({page_1based for _, _, page_1based, _ in rendered})
    page_thumbs: Dict[int, str] = {}
    for p in unique_pages:
        try:
            page = doc[p - 1]
            pix = page.get_pixmap(matrix=fitz.Matrix(_INSPECT_SCALE, _INSPECT_SCALE))
            thumb_name = f"_inspect_p{p:02d}.png"
            pix.save(os.path.join(parent, thumb_name))
            page_thumbs[p] = thumb_name
        except Exception as e:
            print(f"  [WARN] could not render page {p} thumb: {e}")

    rows = []
    for fig_num, png_path, page_1based, clip in rendered:
        png_rel = os.path.relpath(png_path, parent).replace(os.sep, "/")
        thumb_basename = page_thumbs.get(page_1based, "")
        if thumb_basename:
            # Page dimensions at INSPECT_SCALE = clip CSS coords
            page = doc[page_1based - 1]
            page_w_css = page.rect.width * _INSPECT_SCALE
            page_h_css = page.rect.height * _INSPECT_SCALE
            cx = clip.x0 * _INSPECT_SCALE
            cy = clip.y0 * _INSPECT_SCALE
            cw = (clip.x1 - clip.x0) * _INSPECT_SCALE
            ch = (clip.y1 - clip.y0) * _INSPECT_SCALE
            overlay = (
                f'<div class="page-wrap" style="width:{page_w_css:.0f}px;'
                f'height:{page_h_css:.0f}px;">'
                f'<img src="{thumb_basename}" '
                f'style="width:{page_w_css:.0f}px;height:{page_h_css:.0f}px;">'
                f'<div class="clip-box" style="left:{cx:.0f}px;top:{cy:.0f}px;'
                f'width:{cw:.0f}px;height:{ch:.0f}px;"></div></div>'
            )
        else:
            overlay = '<div class="page-wrap">(page thumbnail unavailable)</div>'
        flag_html = "".join(
            f'<div class="flag {level.lower()}">{level}: {html_escape(msg)}</div>'
            for level, msg in flags.get(fig_num, []))
        rows.append(
            f'<section class="fig-row">'
            f'<h2>fig{fig_num} — page {page_1based}</h2>'
            f'{flag_html}'
            f'<div class="cols">'
            f'<div class="panel"><h3>Extracted</h3>'
            f'<img class="extracted" src="{png_rel}">'
            f'<div class="meta">clip = ({clip.x0:.0f}, {clip.y0:.0f}) → '
            f'({clip.x1:.0f}, {clip.y1:.0f})</div></div>'
            f'<div class="panel"><h3>Source page (red = crop region)</h3>'
            f'{overlay}</div>'
            f'</div></section>'
        )

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Figure inspection — render_figures.py</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; background: #f0f0f0;
         margin: 16px; color: #222; }}
  h1 {{ margin-top: 0; }}
  .help {{ background: #fff8d6; border-left: 4px solid #f0c43e; padding: 10px 14px;
           margin-bottom: 18px; font-size: 14px; }}
  .help code {{ background: rgba(0,0,0,0.05); padding: 1px 5px; border-radius: 3px; }}
  .fig-row {{ background: white; border-radius: 8px; padding: 14px 18px;
              margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .fig-row h2 {{ margin: 0 0 8px; font-size: 16px; color: #555; }}
  .cols {{ display: flex; gap: 24px; flex-wrap: wrap; }}
  .panel {{ flex: 1; min-width: 320px; }}
  .panel h3 {{ margin: 0 0 6px; font-size: 13px; color: #888;
               text-transform: uppercase; letter-spacing: 0.5px; }}
  .panel img.extracted {{ max-width: 100%; border: 1px solid #ddd;
                           background: #fafafa; }}
  .page-wrap {{ position: relative; display: inline-block; border: 1px solid #ddd; }}
  .clip-box {{ position: absolute; border: 2px solid #e63946;
               background: rgba(230,57,70,0.08); pointer-events: none; }}
  .meta {{ font-size: 11px; color: #999; margin-top: 4px; font-family: monospace; }}
  .flag {{ padding: 6px 10px; margin-bottom: 8px; border-radius: 4px; font-size: 14px; }}
  .flag.warn {{ background: #fde2e4; border-left: 4px solid #e63946; font-weight: bold; }}
  .flag.note {{ background: #fff3cd; border-left: 4px solid #f0ad4e; }}
</style></head><body>
<h1>Figure inspection — {len(rendered)} figure(s)</h1>
<div class="help">
  Scan each row. The left panel is what got dropped into <code>images/fig*.png</code>.
  The right panel shows the source PDF page; the <strong>red box</strong> marks the
  exact crop region — the figure only, the printed caption stays outside
  (LaTeX typesets it). If anything looks wrong (figure clipped, wrong region,
  rotation off), override that figure with
  <code>|bbox=x0,y0,x1,y1</code> or <code>|rotate=N</code> in <code>--figs</code> —
  see <code>references/troubleshooting.md</code>. Rows with a red WARN were
  anchored on a weak / in-text mention and are probably wrong.
</div>
{"".join(rows)}
</body></html>
"""
    with open(sidecar, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote visual inspection page → {sidecar}")


def rotate_pixmap_cw(pix: "fitz.Pixmap", degrees: int) -> "fitz.Pixmap":
    """Rotate a fitz.Pixmap clockwise by 0/90/180/270 degrees, returning a
    new Pixmap. Uses Pillow (a transitive dep of pdfplumber) so no extra
    install is required.
    """
    if degrees % 360 == 0:
        return pix
    try:
        from PIL import Image
        import io as _io
        img = Image.open(_io.BytesIO(pix.tobytes("png")))
        # PIL rotates COUNTER-clockwise by default → negate for CW.
        img = img.rotate(-degrees, expand=True)
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return fitz.Pixmap(buf.getvalue())
    except ImportError:
        print("  [WARN] Pillow not installed; cannot rotate. "
              "Install with: pip install Pillow")
        return pix


def _text_density_above(page: "fitz.Page", cap_rect: fitz.Rect, look_above: float = 100.0) -> int:
    """Return char count in the strip directly above the candidate.

    A real caption typically sits below an image (sparse / empty region
    above), while an in-text reference is embedded in body prose (dense
    region above). This score is the primary disambiguator when a page
    has multiple `Fig. N.` matches. Memoized per (page, rounded y0).
    """
    slot = _page_slot(page)
    cache: Dict[Tuple[int, float], int] = slot.setdefault("density", {})  # type: ignore[assignment]
    key = (round(cap_rect.y0), look_above)
    if key in cache:
        return cache[key]

    pw = page.rect.width
    strip = fitz.Rect(0, max(0, cap_rect.y0 - look_above), pw, max(0, cap_rect.y0 - 2))
    if strip.is_empty:
        cache[key] = 0
        return 0
    blocks = page.get_text("blocks", clip=strip) or []
    n = sum(len(b[4] or "") for b in blocks)
    cache[key] = n
    return n


def find_caption_rect(
    page: "fitz.Page",
    fig_num: int,
    user_keyword: str,
) -> Tuple[Optional[fitz.Rect], Optional[str]]:
    """Locate the caption bbox for figure `fig_num`.

    Ranks every hit of the strong forms (terminator — standard caption
    pattern), the user keyword and the weak forms (no terminator) with
    `_hit_rank`: line-opening hits first, strong before weak.
    """
    hit = _pick_best_on_page(
        page, _strong_keywords(fig_num, user_keyword) + _weak_keywords(fig_num))
    return hit if hit else (None, None)


def find_watermark_boxes(page: "fitz.Page") -> List[fitz.Rect]:
    boxes: List[fitz.Rect] = []
    for pat in WATERMARK_PATTERNS:
        for r in page.search_for(pat):
            boxes.append(r)
    return boxes


def shrink_for_watermarks(
    clip: fitz.Rect,
    wm_boxes: List[fitz.Rect],
    page_rect: fitz.Rect,
    pad: float = 4.0,
    edge_thresh: float = 80.0,
) -> fitz.Rect:
    """Pull clip edges inward to avoid intersecting watermark boxes.

    Only watermarks within `edge_thresh` points of a page edge are used,
    so a stray match in the middle of a figure can't shrink us.
    """
    x0, y0, x1, y1 = clip.x0, clip.y0, clip.x1, clip.y1
    pw, ph = page_rect.width, page_rect.height
    for wm in wm_boxes:
        # Right-edge watermark (Wiley sidebar)
        if pw - wm.x1 < edge_thresh and wm.x0 < x1:
            x1 = min(x1, wm.x0 - pad)
        # Left-edge watermark
        elif wm.x0 < edge_thresh and wm.x1 > x0:
            x0 = max(x0, wm.x1 + pad)
        # Top-edge watermark (page header)
        if wm.y0 < edge_thresh and wm.y1 > y0 and wm.y1 < (y0 + y1) / 2:
            y0 = max(y0, wm.y1 + pad)
        # Bottom-edge watermark (page footer) — only if clip extends into it
        elif ph - wm.y1 < edge_thresh and wm.y0 < y1 and wm.y0 > (y0 + y1) / 2:
            y1 = min(y1, wm.y0 - pad)
    # Sanity: never invert
    if x1 - x0 < 50 or y1 - y0 < 50:
        return clip
    return fitz.Rect(x0, y0, x1, y1)


def render_one(
    doc: "fitz.Document",
    page_idx0: int,
    fig_num: int,
    user_keyword: str,
    out_path: str,
    bbox_override: Optional[fitz.Rect] = None,
    rotate_override: Optional[int] = None,
    top_margin: float = 72.0,
    side_margin: float = 0.0,
    pad: float = 4.0,
    scale: float = 3.0,
) -> Tuple[fitz.Rect, bool, str, List[Tuple[str, str]]]:
    """Render one figure (figure content only — the printed caption is
    excluded). Returns (clip_used, used_fallback, note, flags) where
    flags is a list of ("WARN" | "NOTE", message) for the summary and the
    inspection page."""
    page = doc[page_idx0]
    pw, ph = page.rect.width, page.rect.height
    fallback = False  # only flipped True for the full-page fallback below
    flags: List[Tuple[str, str]] = []
    cap: Optional[fitz.Rect] = None

    if bbox_override is not None:
        clip = bbox_override
        note = "manual bbox override"
    else:
        cap, kw_hit = find_caption_rect(page, fig_num, user_keyword)
        if cap is None:
            clip = page.rect
            fallback = True
            note = "no caption keyword matched, full page fallback"
        else:
            if not _starts_line(page, cap, kw_hit):
                flags.append(("WARN", f"anchor '{kw_hit.strip()}' sits mid-line "
                              "- looks like an in-text mention, not the caption"))
            elif not _is_strong(kw_hit):
                flags.append(("WARN", f"anchored on weak form '{kw_hit.strip()}' "
                              f"(no 'Figure {fig_num}:' / 'Figure {fig_num}.' caption found; "
                              "fine only if captions carry no terminator, e.g. 'Fig. 1 Title')"))
            # Primary strategy: find the actual image / drawing region near
            # the caption. Works for normal, rotated, side-by-side, and
            # irregular layouts because it doesn't assume "image is
            # directly above caption".
            #
            # On multi-figure pages, pass other figures' captions so the
            # Voronoi-style assignment keeps each figure's clip to its own
            # image cluster (no cross-contamination).
            all_caps = find_all_caption_rects_on_page(page)
            # Skip self by identity if present, else by float-equal fallback.
            others = [r for r in all_caps if r is not cap
                      and not (r.x0 == cap.x0 and r.y0 == cap.y0)]
            fig_region = find_figure_region_near(page, cap, other_captions=others)
            if fig_region is not None:
                # bbox-based clip: trust the actual figure region's natural
                # extent, grown by the labels / thin strokes touching it.
                # `top_margin` / `side_margin` are user-supplied safety
                # floors for the FALLBACK path; they don't apply here.
                # Watermark exclusion (below) will shrink the clip if a
                # header / sidebar still intersects.
                area, side = _caption_free_area(page, fig_region, cap)
                region = _grow_region(page, fig_region, area, side)
                clip = fitz.Rect(
                    max(0, region.x0 - pad), max(0, region.y0 - pad),
                    min(pw, region.x1 + pad), min(ph, region.y1 + pad),
                ) & area
                clip = _keep_pad_off_text(page, region, clip)
                note = (f"caption hit on '{kw_hit}' at y={cap.y0:.0f} (caption {side}{' of' if side in ('left', 'right') else ''} "
                        f"the figure, excluded) + figure region {fig_region}")
            else:
                # Fallback: classic caption-anchored crop (no image XObject
                # or vector drawing found near caption): the band between
                # the previous caption (or `top_margin`) and this caption.
                prev_caps = []
                for kw in ("Fig.", "Figure", "Table"):
                    for r in _search_for(page, kw):
                        if r.y1 < cap.y0 - 10:
                            prev_caps.append(r)
                y_top = max((r.y1 for r in prev_caps), default=top_margin) + 4
                y_bot = cap.y0 - 2
                clip = fitz.Rect(side_margin, y_top, pw - side_margin, y_bot)
                note = (f"caption hit on '{kw_hit}' at y={cap.y0:.0f}"
                        f" (no image region found, caption-anchored fallback)")

        # Watermark auto-exclusion
        wm_boxes = find_watermark_boxes(page)
        if wm_boxes:
            shrunk = shrink_for_watermarks(clip, wm_boxes, page.rect)
            if shrunk != clip:
                note += f" + watermark-shrunk ({len(wm_boxes)} hits)"
                clip = shrunk

    # Decide rotation: manual override wins; then the caption's own text
    # direction (a figure rotated onto a portrait page is rotated together
    # with its caption); without a caption, vote over the clip's text.
    voted = detect_clip_rotation(page, clip)
    if rotate_override is not None:
        rotation = rotate_override
        if rotation:
            note += f" + manual rotate={rotation}°"
    elif cap is not None:
        line = _line_of(page, cap)
        rotation = _DIR_TO_ROTATION.get(line[1] if line else (1, 0), 0)
        if rotation:
            note += f" + auto-rotated {rotation}° (caption direction)"
        if voted != rotation:
            flags.append(("NOTE", f"most text inside the figure reads {voted}° off, but the "
                          f"caption reads {rotation}° off -> kept at {rotation}°; if the PNG "
                          f"is sideways, re-run with |rotate={voted}"))
    else:
        rotation = voted
        if rotation:
            note += f" + auto-rotated {rotation}° (text direction)"

    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip)
    if rotation:
        pix = rotate_pixmap_cw(pix, rotation)
        # NOTE: when a rotated figure ends up portrait, the LaTeX width
        # picked by `write_figure_includes_tex` (via suggest_latex_width)
        # already drops to 0.55\textwidth, so no extra inline hint is
        # emitted here — the sidecar is the single source of truth for
        # figure-include scaffolding.
    pix.save(out_path)
    return clip, fallback, note, flags


def auto_discover_figures(
    doc: "fitz.Document",
    max_fig: int = 50,
    stop_after_misses: int = 2,
) -> List[Tuple[int, int, str]]:
    """Scan all pages for figure captions and emit `(fig_num, page_1based, kw)`.

    For each figure number N, gathers EVERY hit of the caption keywords
    (strong: `Fig. N.` / `Figure N:` / ..., weak: `Figure N `) across all
    pages and ranks them with `_hit_rank`: a hit that opens its text line
    beats an in-text mention, strong beats weak, then the LOWEST
    text-density in the strip immediately above it — the strongest signal
    of "image above, caption below" vs "embedded in body prose".

    This handles two adversarial layouts correctly:

    * Figures placed AFTER their first text mention (Tan-style): the
      real caption is the lowest hit on the page, but in-text refs
      higher up have dense text above them, so density-pick still wins.

    * Figures placed BEFORE their first text mention (Liu-style page 7,
      where Fig. 3/4/5 stack at the top and body-text discussion is
      below): the real caption is the HIGHEST hit, with only the page
      header above it — density still wins.

    Stops scanning once `stop_after_misses` consecutive figure numbers
    aren't found anywhere in the document.
    """
    out: List[Tuple[int, int, str]] = []
    consec_miss = 0

    def gather(n: int, keywords: List[str]):
        hits = []
        for page_idx, page in enumerate(doc):
            for kw in keywords:
                for r in _search_for(page, kw):
                    hits.append((_hit_rank(page, r, kw), page_idx + 1, kw))
        return hits

    for n in range(1, max_fig + 1):
        candidates = gather(n, _strong_keywords(n) + _weak_keywords(n))
        if not candidates:
            consec_miss += 1
            if consec_miss >= stop_after_misses:
                break
            continue
        consec_miss = 0
        best = min(candidates, key=lambda t: (t[0], t[1]))
        out.append((n, best[1], best[2]))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", help="path to the input PDF")
    parser.add_argument("--out", required=True, help="output images directory")
    parser.add_argument(
        "--figs", required=False, default=None,
        help='comma-separated "fig_num:page:keyword" tuples (optional |bbox=x0,y0,x1,y1). '
             'When --auto is set, this is OPTIONAL and overrides specific entries.',
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="Auto-discover figures by scanning all pages for Fig. N. / Figure N. patterns. "
             "Combine with --figs to override specific entries.",
    )
    parser.add_argument(
        "--max-fig", type=int, default=50,
        help="Highest figure number to scan for in --auto mode. Default: 50",
    )
    parser.add_argument(
        "--scale", type=float, default=3.0,
        help="render scale (3.0 ~= 216 DPI). Default: 3.0",
    )
    parser.add_argument(
        "--top-margin", type=float, default=72.0,
        help="y_top floor for the CAPTION-ANCHORED FALLBACK path only "
             "(triggered when no image/drawing is detected near the caption). "
             "The primary bbox-based path uses the figure's real extent and "
             "ignores this. Default: 72.",
    )
    parser.add_argument(
        "--side-margin", type=float, default=0.0,
        help="Symmetric horizontal margin for the CAPTION-ANCHORED FALLBACK "
             "path only. The primary bbox-based path uses the figure's real "
             "extent. Default: 0.",
    )
    args = parser.parse_args()
    _clear_page_cache()  # ensure a clean cache for this run

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    if not args.auto and not args.figs:
        parser.error("either --auto or --figs is required")

    os.makedirs(args.out, exist_ok=True)
    doc = fitz.open(args.pdf)

    # Build the figs list: auto-discover first, then layer manual --figs overrides.
    if args.auto:
        auto_list = auto_discover_figures(doc, max_fig=args.max_fig)
        figs = [(n, p, kw, None, None) for (n, p, kw) in auto_list]
        print(f"Auto-discovered {len(figs)} figure(s):")
        for n, p, kw, _, _ in figs:
            print(f"  fig{n}: page {p}, keyword='{kw}'")
        if args.figs:
            overrides = parse_figs(args.figs)
            d = {n: (n, p, kw, b, r) for (n, p, kw, b, r) in figs}
            for (n, p, kw, b, r) in overrides:
                d[n] = (n, p, kw, b, r)
                print(f"  override fig{n}: page {p}, keyword='{kw}'"
                      + (f", bbox={b}" if b else "")
                      + (f", rotate={r}" if r is not None else ""))
            figs = [d[k] for k in sorted(d)]
    else:
        figs = parse_figs(args.figs)
    n_ok = 0
    n_fallback = 0
    # Collect per-figure metadata to emit _figure_includes.tex sidecar
    rendered: List[Tuple[int, str, int, fitz.Rect]] = []  # (fig_num, png_path, page_1based, clip)
    all_flags: Dict[int, List[Tuple[str, str]]] = {}
    for fig_num, page, kw, bbox, rotate in figs:
        out_path = os.path.join(args.out, f"fig{fig_num}.png")
        try:
            clip, fb, note, flags = render_one(
                doc, page - 1, fig_num, kw, out_path,
                bbox_override=bbox,
                rotate_override=rotate,
                top_margin=args.top_margin,
                side_margin=args.side_margin,
                scale=args.scale,
            )
        except IndexError:
            print(f"  fig{fig_num}: page {page} out of range (PDF has {len(doc)} pages)")
            continue
        if flags:
            all_flags[fig_num] = flags
        if fb:
            print(f"  fig{fig_num} (page {page}): FALLBACK -- {note}")
            n_fallback += 1
        else:
            print(f"  fig{fig_num} (page {page}) -> {out_path}")
            print(f"    clip={clip}  -- {note}")
            n_ok += 1
            rendered.append((fig_num, out_path, page, clip))
        for level, msg in flags:
            print(f"    [{level}] {msg}")

    # Emit _figure_includes.tex sidecar — aspect-aware \includegraphics
    # blocks for every successfully rendered figure. The translator pastes
    # these into main.tex (filling in caption / label) and avoids the
    # "huge figure floats to end of doc" trap by default.
    if rendered:
        write_figure_includes_tex(args.out, rendered)
        write_inspection_html(args.out, rendered, doc, all_flags)
    doc.close()
    n_warn = sum(1 for fl in all_flags.values() if any(lv == "WARN" for lv, _ in fl))
    print(f"Done: {n_ok} cropped, {n_fallback} fallback to full page"
          + (f", {n_warn} WARN." if n_warn else "."))
    if n_fallback:
        print("Tip: inspect the fallback PNGs; pass a |bbox=... override or fix the keyword.")
    for fig_num in sorted(all_flags):
        for level, msg in all_flags[fig_num]:
            if level == "WARN":
                print(f"[WARN] fig{fig_num}: {msg}. Check this crop in _inspection.html; "
                      "if it is not the figure, fix the page/keyword in --figs "
                      "or pass |bbox=x0,y0,x1,y1.")
    for fig_num in sorted(all_flags):
        for level, msg in all_flags[fig_num]:
            if level == "NOTE":
                print(f"[NOTE] fig{fig_num}: {msg}.")


if __name__ == "__main__":
    main()
