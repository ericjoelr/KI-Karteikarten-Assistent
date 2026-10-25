"""Batch run: the cards of many slides in one go, one slide after another.

Exception to the module layout: the job management for the UI sits as its own
block at the end of the file, so that `run` stays usable — and readable, and
testable — without it.
"""

import logging
import threading
from datetime import datetime
from typing import Callable

from openai import RateLimitError

from backend import llm, pdf
from backend.config import get_settings
from backend.schemas import BatchProgress, BatchResult
from backend.storage import CardStorage

logger = logging.getLogger(__name__)


# =========================================================
# The run
# =========================================================


def pending(storage: CardStorage, pages: list[int]) -> list[int]:
    """The slides that still need work.

    Slides with a card list — an empty one counts too — are done; missing and
    failed ones are (re)processed. This is how the run itself resumes; the UI
    uses it on top of that to show up front how many requests it will cost.
    """
    store = storage.load()
    return [
        page for page in pages
        if (entry := store.slides.get(str(page))) is None or entry.error is not None
    ]


def run(
    storage: CardStorage,
    pages: list[int],
    *,
    graphics: bool,
    cancel: threading.Event | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> BatchResult:
    """Generates the cards of the given slides, one after another, into the store.

    A failed slide is stored with its error and retried by the next run; a rate
    limit (HTTP 429) stops the run, because every further slide would hit it too.

    cancel: once set, stop before the next slide; the request in flight is not
        aborted.
    on_progress: receives the number of finished slides after every slide.
    """
    settings = get_settings()
    generated_at = datetime.now().isoformat(timespec="seconds")
    generated = failed = 0

    # The card store is written after every slide, so it *is* the progress: an
    # aborted run resumes right here on the next start.
    for page in pending(storage, pages):
        if cancel is not None and cancel.is_set():
            return BatchResult(status="cancelled", generated=generated, failed=failed)
        try:
            image, found = pdf.get_model_input(storage.pdf_name, page, graphics=graphics)
            cards = llm.generate_cards(image, graphics=graphics)
        except RateLimitError:
            return BatchResult(status="rate_limited", generated=generated, failed=failed)
        except Exception as exc:
            logger.error("Slide %d failed: %s", page, exc)
            storage.save_page(
                page, [],
                graphics=graphics, marked=False, generated_at=generated_at,
                model=settings.default_model, error=str(exc),
            )
            failed += 1
        else:
            storage.save_page(
                page, cards,
                graphics=graphics, marked=bool(found), generated_at=generated_at,
                model=settings.default_model,
            )
            generated += 1
        if on_progress:
            on_progress(generated + failed)

    return BatchResult(status="done", generated=generated, failed=failed)


# =========================================================
# Job management: one run at a time
# =========================================================
# The progress only lives in the process: the real progress is the card store
# on disk. A backend restart therefore discards the display, not the work — a
# new run continues. This management knows no web framework: the conflict is an
# ordinary exception that the route translates into HTTP 409.
#
# The German strings below are user-visible messages and stay German on purpose.

_job_lock = threading.Lock()
_cancel = threading.Event()
_job: BatchProgress | None = None  # the running or most recently finished run

# Plain text for the final status; {n} = slides this run has stored.
_MESSAGE = {
    "done": "Fertig – {n} Folien erzeugt.",
    "cancelled": "Abgebrochen – {n} Folien wurden gesichert.",
    "rate_limited": (
        "Kontingent erschöpft – {n} Folien wurden gesichert. "
        "Ein späterer Neustart setzt dort fort."
    ),
}


def _work(job: BatchProgress, pages: list[int], graphics: bool) -> None:
    def progress(done: int) -> None:
        job.done = done

    try:
        result = run(
            CardStorage(job.pdf_name), pages,
            graphics=graphics, cancel=_cancel, on_progress=progress,
        )
        job.status = result.status
        job.message = _MESSAGE[result.status].format(n=result.generated)
        if result.failed:
            job.message += f" {result.failed} Folien sind fehlgeschlagen."
    except Exception as exc:  # the run must not die silently in the background
        logger.exception("Batch run for %s crashed", job.pdf_name)
        job.status = "error"
        job.message = str(exc)
    finally:
        job.running = False


class BatchConflict(Exception):
    """A run is already going — or there is none that could be cancelled."""


def start(pdf_name: str, *, graphics: bool | None = None) -> BatchProgress:
    """Starts a run over the open slides of the PDF in the background.

    graphics: without a value the configured default applies.
    If a run is already going, the function raises BatchConflict.
    """
    global _job
    settings = get_settings()
    mode = settings.graphics_mode(graphics)
    pages = pending(
        CardStorage(pdf_name), list(range(1, pdf.get_page_count(pdf_name) + 1))
    )

    with _job_lock:
        if _job is not None and _job.running:
            raise BatchConflict("Es läuft bereits ein Batch-Lauf. Bitte abwarten oder abbrechen.")
        _cancel.clear()
        _job = BatchProgress(pdf_name=pdf_name, total=len(pages))
        job = _job

    # Own thread instead of a BackgroundTask: the response goes out immediately
    # and the run does not hang off the request lifecycle (closing the dialog,
    # reloading).
    threading.Thread(target=_work, args=(job, pages, mode), daemon=True).start()
    return job


def status() -> BatchProgress | None:
    """The running or most recently finished run — None if there was none."""
    return _job


def cancel() -> BatchProgress:
    """Stops the run before the next slide; requests in flight stay."""
    if _job is None or not _job.running:
        raise BatchConflict("Es läuft kein Batch-Lauf.")
    _cancel.set()
    return _job
