"""The card store: the generated cards of one PDF in one JSON file.

It is the single source of truth about generated cards; Anki only holds the
accepted excerpt of it. Graphics are not stored — they are
re-extracted from the PDF deterministically on demand (pdf.get_graphics).
"""

import re
import threading
from pathlib import Path

from backend.config import get_settings
from backend.schemas import CardStatus, CardStore, SlideEntry, StoredCard, flashcard

# =========================================================
# Shared module state
# =========================================================

# The file is rewritten completely after every finished slide. The batch run
# works in a background thread while HTTP requests (export, edit) write the same
# file -> serialize the writes. The lock belongs to the module, not to the
# instance: the same file is worked on by several short-lived instances.
_lock = threading.Lock()

_KEEP = object()  # "do not touch note_id" — None explicitly means "clear it"


# =========================================================
# Public
# =========================================================


class CardStorage:
    """The card store of one PDF; the location is fixed at construction time."""

    def __init__(self, pdf_name: str):
        self.pdf_name = pdf_name
        # As with the Anki media files: strip special characters so the PDF
        # name turns into a clean (and harmless) file name.
        stem = re.sub(r"\W+", "_", Path(pdf_name).stem)
        self.path = get_settings().cards_user_dir / f"{stem}.json"

    def _save(self, store: CardStore) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write next to it first, then rename: whoever reads in parallel (the
        # frontend during a batch run) never sees a half-written file.
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(store.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def load(self) -> CardStore:
        """The whole store; an empty one if the PDF has never been processed."""
        if not self.path.exists():
            return CardStore(pdf_name=self.pdf_name)
        return CardStore.model_validate_json(self.path.read_text(encoding="utf-8"))

    def get_page(self, page: int) -> SlideEntry | None:
        """The slide's entry, or None if it was never processed.

        An entry with an *empty* card list is something else: that slide ran, the
        LLM just produced nothing for it — so a resuming run leaves it alone.
        """
        return self.load().slides.get(str(page))

    def save_page(
        self,
        page: int,
        cards: list[flashcard],
        *,
        graphics: bool,
        marked: bool,
        generated_at: str,
        model: str,
        error: str | None = None,
    ) -> None:
        """Stores the result of a generation, replacing the previous cards."""
        entry = SlideEntry(
            graphics=graphics,
            marked=marked,
            generated_at=generated_at,
            model=model,
            cards=[StoredCard(front=c.front, back=c.back) for c in cards],
            error=error,
        )
        with _lock:
            store = self.load()
            store.slides[str(page)] = entry
            self._save(store)

    def set_status(
        self,
        page: int,
        index: int,
        status: CardStatus,
        *,
        note_id: int | None | object = _KEEP,
    ) -> None:
        """Sets the review status of a card.

        Only pass note_id when it changes: the new ID on export, None on undo.
        Without a value it stays as it is — otherwise a later rejection would,
        for example, lose the ID of the note that still sits in Anki.
        """
        with _lock:
            store = self.load()
            card = store.slides[str(page)].cards[index]
            card.status = status
            if note_id is not _KEEP:
                card.note_id = note_id
            self._save(store)

    def set_content(self, page: int, index: int, *, front: str, back: str) -> None:
        """Replaces the content of a card (manual edit); the status stays."""
        with _lock:
            store = self.load()
            card = store.slides[str(page)].cards[index]
            card.front, card.back = front, back
            self._save(store)
