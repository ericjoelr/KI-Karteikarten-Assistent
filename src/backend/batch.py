"""Batch run: the cards of many slides in one go, one slide after another.

The UI and the dataset script call the same function (`run`) and differ only in
the card store they hand it. Exception to the module layout: the job management
for the UI sits as its own block at the end of the file, so that `run` stays
usable — and readable — without it, which is the path the dataset script takes.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Callable

from backend import llm, pdf
from backend.config import get_settings
from backend.schemas import BatchProgress, BatchResult, BatchStatus
from backend.storage import CardStorage

logger = logging.getLogger(__name__)


# =========================================================
# Private helpers
# =========================================================


class _limiter:
    """Throttle and request budget of a single run."""

    def __init__(self, interval: float, budget: int):
        self._interval = interval
        self._budget = budget
        self._next = 0.0

    def take(self) -> float | None:
        """Reserves a request, waits out the minimum gap and returns its start
        time — or None once the budget is exhausted."""
        if self._budget <= 0:
            return None
        self._budget -= 1
        now = time.monotonic()
        start = max(now, self._next)
        self._next = start + self._interval
        if start > now:
            time.sleep(start - now)
        return start


def _is_rate_limit(exc: Exception) -> bool:
    """Detects a quota/rate limit response (HTTP 429) from the provider."""
    # The client may wrap provider errors, so walk the cause chain as well.
    while exc is not None:
        if getattr(exc, "status_code", None) == 429 or type(exc).__name__ == "RateLimitError":
            return True
        exc = exc.__cause__
    return False


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
    """Generates the cards of the given slides and fills the card store.

    storage: the card store the run writes into (UI vs. dataset).
    cancel: once set, stop before the next slide; the request in flight is not
        aborted.
    on_progress: receives the number of finished slides after every slide.
    """
    settings = get_settings()
    pdf_name = storage.pdf_name
    generated_at = datetime.now().isoformat(timespec="seconds")

    # The progress *is* the card store: it is written after every finished
    # slide, so an aborted run resumes right here on the next start.
    todo = pending(storage, pages)

    limiter = _limiter(
        60 / settings.max_requests_per_minute if settings.max_requests_per_minute else 0.0,
        settings.max_requests_per_run,
    )
    generated = 0
    failed = 0

    def generate(page: int) -> None:
        # Slides are independent of each other — no context from previous slides
        # is carried over on purpose (ADR-0002).
        image, found = pdf.get_model_input(pdf_name, page, graphics=graphics)
        # One generation is exactly one provider request, so a reserved slot
        # never hides several of them. Failed answers are covered by the retry
        # in work() — which reserves a slot of its own.
        result = llm.generate_cards(image, graphics=graphics)
        storage.save_page(
            page, result,
            graphics=graphics, marked=bool(found), generated_at=generated_at,
            model=settings.default_model,
        )

    def work(page: int) -> BatchStatus | None:
        """Processes one slide; returns a reason to stop, or None."""
        nonlocal generated, failed
        for attempt in (1, 2):  # one attempt, one retry
            if limiter.take() is None:
                return "limit_reached"
            try:
                generate(page)
            except Exception as exc:
                # With the quota exhausted the rest of the run would hit the
                # same error — so stop right away.
                if _is_rate_limit(exc):
                    return "rate_limited"
                if attempt == 1:
                    logger.warning("Slide %d failed, retrying: %s", page, exc)
                    continue  # one retry; temporary glitches sort themselves out
                logger.error("Slide %d failed twice, skipping: %s", page, exc)
                # An error note instead of a card list: the slide counts as not
                # done, but the run carries on.
                storage.save_page(
                    page, [],
                    graphics=graphics, marked=False, generated_at=generated_at,
                    model=settings.default_model, error=str(exc),
                )
                failed += 1
                return None
            generated += 1
            return None

    # Strictly one slide after another: the provider's per-minute limit is
    # binding anyway, so concurrency would gain almost nothing while making the
    # run considerably harder to read (ADR-0002, consequences).
    status: BatchStatus = "done"
    for page in todo:
        if cancel is not None and cancel.is_set():
            status = "cancelled"
            break
        stop = work(page)
        if stop is not None:
            status = stop
            break
        if on_progress:
            on_progress(generated + failed)

    return BatchResult(status=status, generated=generated, failed=failed)


# =========================================================
# Job management: one run at a time
# =========================================================
# The progress only lives in the process: the real progress is the card store
# on disk. A backend restart therefore discards the display, not the work — a
# new run continues. This management knows no web framework: the conflict is an
# ordinary exception that the route translates into HTTP 409. Otherwise the
# dataset script, which calls `run` directly, would drag the web application in
# with it.
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
    "limit_reached": (
        "Obergrenze an Anfragen erreicht – {n} Folien wurden gesichert. "
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
