"""Everything the application reads out of a PDF: slide images and graphics.

The thresholds of the graphics extraction are tuned against the real lecture
decks in data/pdf/. Changing them, or the render settings, changes the
grafik:<idx> numbering of every future card — and with it the contract to the
fine-tuned model.
"""

import pymupdf

from backend.config import get_settings

# =========================================================
# Constants
# =========================================================

# --- Thresholds for the graphics extraction (get_graphics) ---
# Minimum area relative to the page: sorts out logos/icons/decoration.
MIN_AREA_RATIO = 0.02
# Maximum area relative to the page: sorts out page-filling backgrounds.
MAX_AREA_RATIO = 0.85
# From this overlap on (intersection / smaller area) two hits count as a
# duplicate and only the larger one is kept.
OVERLAP_RATIO = 0.8
# Single vector paths above this area are slide frames/backgrounds and would
# merge everything into one giant cluster during clustering.
MAX_PATH_AREA_RATIO = 0.5
# Vector clusters made of fewer paths are decoration (bars, lines, boxes); real
# diagrams consist of many paths.
MIN_VECTOR_PATHS = 3
# Cluster tolerance in pt: at 3 (the default) diagrams fall apart into fragments
# (arrows only, braces only), at 15 the whole page merges.
CLUSTER_TOLERANCE = 10
# Extremely wide/tall hits are title bars, rules and the like, not diagrams.
MAX_ASPECT_RATIO = 6.0
# A diagram cluster has to contain at least one 2D shape (both edges >= 10 pt)
# or one long structural line (>= 100 pt, e.g. a table rule) — otherwise it is a
# collection of bullet points/underlines.
MIN_BOX_DIM = 10.0
MIN_LINE_SPAN = 100.0
# Hits whose top edges are closer together than this (pt) count as one row and
# are numbered left to right (sorting by y alone would number graphics that sit
# next to each other in a confusing order).
ROW_TOLERANCE = 30.0

# --- Marking extracted graphics on the slide screenshot ---
# Magenta: high contrast and practically absent from lecture slides.
MARK_COLOR = (1.0, 0.0, 1.0)
MARK_WIDTH = 2.0
# Edge length (pt) of the number chip drawn at the corner of the frame.
MARK_LABEL_SIZE = 14.0


# =========================================================
# Finding and marking graphics
# =========================================================


def _graphic_rects(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    """Filtered, deduplicated, position-sorted graphic rectangles of a page.

    Returns (rect, source) pairs, source is "raster" or "vector".
    """
    page_area = page.rect.get_area()

    candidates: list[tuple[pymupdf.Rect, str]] = []
    for img in page.get_images(full=True):
        for rect in page.get_image_rects(img):
            candidates.append((rect, "raster"))

    # Remove frame/background paths before clustering, otherwise
    # cluster_drawings() merges the whole page into one cluster.
    paths = [
        d for d in page.get_drawings()
        if d["rect"].get_area() < MAX_PATH_AREA_RATIO * page_area
    ]
    for rect in page.cluster_drawings(
        drawings=paths, x_tolerance=CLUSTER_TOLERANCE, y_tolerance=CLUSTER_TOLERANCE
    ):
        # contains instead of intersects: lines have degenerate (area-less) rects.
        members = [d["rect"] for d in paths if rect.contains(d["rect"])]
        if len(members) >= MIN_VECTOR_PATHS and any(
            min(m.width, m.height) >= MIN_BOX_DIM or max(m.width, m.height) >= MIN_LINE_SPAN
            for m in members
        ):
            candidates.append((rect, "vector"))

    # Size/shape filter: too small = logo/decoration, too large = page
    # background, extreme aspect ratio = title bar/rule.
    sized = []
    for rect, source in candidates:
        rect = pymupdf.Rect(rect).intersect(page.rect)
        area = rect.get_area()
        if area < MIN_AREA_RATIO * page_area or area > MAX_AREA_RATIO * page_area:
            continue
        if max(rect.width, rect.height) > MAX_ASPECT_RATIO * min(rect.width, rect.height):
            continue
        sized.append((rect, source))

    # Overlap dedup: largest first, discard heavily overlapping smaller ones.
    sized.sort(key=lambda item: item[0].get_area(), reverse=True)
    kept: list[tuple[pymupdf.Rect, str]] = []
    for rect, source in sized:
        smaller_area = rect.get_area()
        if any(
            pymupdf.Rect(rect).intersect(other).get_area() > OVERLAP_RATIO * smaller_area
            for other, _ in kept
        ):
            continue
        kept.append((rect, source))

    kept.sort(key=lambda item: (round(item[0].y0 / ROW_TOLERANCE), round(item[0].x0)))
    return kept


def _draw_graphic_marks(page: pymupdf.Page) -> None:
    """Draws numbered frames around the extracted graphics of the page.

    Number = list index from get_graphics (identical _graphic_rects order).
    Compute the rects first, then draw: draw_rect adds vector paths to the page
    itself and would otherwise distort the clustering.
    """
    size = MARK_LABEL_SIZE
    for idx, (rect, _source) in enumerate(_graphic_rects(page)):
        page.draw_rect(rect, color=MARK_COLOR, width=MARK_WIDTH)
        # Chip outside the box (bottom right) so it does not cover any graphic
        # content; if there is no room, above it, and inside as a last resort.
        chip = pymupdf.Rect(rect.x1 - size, rect.y1, rect.x1, rect.y1 + size)
        if chip.y1 > page.rect.y1:
            chip = pymupdf.Rect(rect.x1 - size, rect.y0 - size, rect.x1, rect.y0)
        if chip.y0 < 0:
            chip = pymupdf.Rect(rect.x1 - size, rect.y1 - size, rect.x1, rect.y1)
        page.draw_rect(chip, color=MARK_COLOR, fill=MARK_COLOR)
        page.insert_text((chip.x0 + 4, chip.y1 - 4), str(idx), fontsize=11, color=(1, 1, 1))


# =========================================================
# Public
# =========================================================


def get_available_pdfs() -> list[str]:
    """The file names of all PDFs in the configured PDF folder."""
    folder = get_settings().pdf_folder
    return [file.name for file in folder.glob("*.pdf")]


def get_page_count(file_name: str) -> int:
    """The number of slides in the PDF."""
    file_path = get_settings().pdf_folder / file_name
    with pymupdf.open(file_path) as doc:
        return len(doc)


def get_slide_image(file_name: str, page_num: int, mark_graphics: bool = False) -> bytes:
    """Renders one slide as a JPG; with mark_graphics including the numbered frames."""
    file_path = get_settings().pdf_folder / file_name
    # Use 'with' so the file is closed cleanly.
    with pymupdf.open(file_path) as doc:
        page = doc[page_num - 1]

        if mark_graphics:
            _draw_graphic_marks(page)

        # 1. Force a standard zoom (1.0 or 1.5).
        mat = pymupdf.Matrix(1.5, 1.5)

        # 2. CRITICAL: set the colorspace to RGB.
        # This often solves 'bandwriter' dimension problems with CMYK PDFs.
        pix = page.get_pixmap(matrix=mat, colorspace=pymupdf.csRGB)

        # 3. Use JPG instead of PNG.
        # That performs better and avoids PNG header errors.
        return pix.tobytes("jpg")


def get_model_input(file_name: str, page_num: int, *, graphics: bool) -> tuple[bytes, list[bytes]]:
    """What the model gets to see for one slide: image plus its graphics.

    This is the frozen input contract of the fine-tuned model and therefore
    lives in exactly one place.

    graphics off: nothing is extracted and the slide stays unmarked.
    graphics on: the graphics are extracted, and the slide image carries the
    numbered frames exactly when the page has any.
    """
    found = get_graphics(file_name, page_num) if graphics else []
    return get_slide_image(file_name, page_num, mark_graphics=bool(found)), found


def get_graphics(file_name: str, page_num: int) -> list[bytes]:
    """Extracts the graphics of a page (raster images + vector diagrams) as JPGs.

    The order is deterministic (sorted by position on the page), so that the
    list index works as a stable reference (image_index).
    """
    file_path = get_settings().pdf_folder / file_name
    with pymupdf.open(file_path) as doc:
        page = doc[page_num - 1]
        mat = pymupdf.Matrix(1.5, 1.5)  # same setup as get_slide_image
        return [
            page.get_pixmap(matrix=mat, clip=rect, colorspace=pymupdf.csRGB).tobytes("jpg")
            for rect, _source in _graphic_rects(page)
        ]
