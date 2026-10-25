"""The data models of the application in one place: domain, storage, Anki, HTTP.

The models carry no logic — preparing a card for Anki lives in the Anki module,
reading and writing the card store in the storage module. Exception to the
module layout: they are grouped by topic instead of private-before-public,
because this file is read as the list of what the project talks about.
"""

from typing import Literal

from pydantic import BaseModel

# =========================================================
# Domain
# =========================================================


class flashcard(BaseModel):
    front: str
    back: str


class card_deck(BaseModel):
    cards: list[flashcard]


CardStatus = Literal["unreviewed", "accepted", "rejected"]


class StoredCard(flashcard):
    status: CardStatus = "unreviewed"
    note_id: int | None = None  # Anki note of the exported card


class SlideEntry(BaseModel):
    graphics: bool  # graphics mode of the run
    marked: bool  # was the slide image actually marked?
    generated_at: str  # start time of the run (ISO)
    model: str
    cards: list[StoredCard]


class CardStore(BaseModel):
    pdf_name: str
    # Slide number as a string, because JSON has no numbers as keys.
    slides: dict[str, SlideEntry] = {}


# =========================================================
# Anki
# =========================================================


class ActionResult(BaseModel):
    ok: bool
    message: str  # shown to the user as it is -> German (see anki.py)
    note_id: int | None = None  # ID of the note created in Anki (for undo)


# =========================================================
# HTTP: requests and responses
# =========================================================


class PageCardsResponse(BaseModel):
    generated: bool  # False = the slide was never processed (an empty list means it was)
    graphics: bool  # graphics mode the slide was generated in
    cards: list[StoredCard]


class StatusRequest(BaseModel):
    # A card only ever becomes "accepted" through a real export.
    status: Literal["unreviewed", "rejected"]


class GenerateRequest(BaseModel):
    pdf_name: str
    page: int
    feedback: str | None = None
    # Graphics mode; without a value the configured default applies.
    graphics: bool | None = None


class GenerateResponse(card_deck):
    # The slide's graphics as base64 JPGs; list index == grafik:<idx> reference.
    graphics: list[str]


class ProviderRequest(BaseModel):
    provider: Literal["cloud", "llamacpp", "mlx"]
    # Cloud only; without a value the stored cloud slot applies.
    base_url: str | None = None
    api_key: str | None = None


class ActivateRequest(ProviderRequest):
    model: str  # for mlx: the adapter's folder name


class CreateDeckRequest(BaseModel):
    name: str


class ExportRequest(BaseModel):
    deck: str
    card: flashcard
    page: int
    pdf_name: str
    index: int  # position of the card in the card store (for the review status)


class UndoRequest(BaseModel):
    pdf_name: str
    page: int
    index: int
