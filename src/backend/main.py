"""The HTTP API: every route of the backend, forwarding into the modules.

The routes are one-liners, so they live here instead of in router files.
Exception to the module layout: they are ordered by topic (providers, PDFs, cards,
generation, Anki) rather than private-before-public, because that is
how the API reads in the /docs page.
"""

import base64
from datetime import datetime

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from backend import anki, llm, pdf, providers
from backend.config import get_settings
from backend.storage import CardStorage
from backend.schemas import (
    ActionResult,
    ActivateRequest,
    CreateDeckRequest,
    ExportRequest,
    GenerateRequest,
    GenerateResponse,
    PageCardsResponse,
    ProviderRequest,
    StatusRequest,
    StoredCard,
    UndoRequest,
    flashcard,
)

app = FastAPI(title="anki_agent API", version="0.1.0")

# The frontend (Streamlit) runs as a separate process/origin -> keep CORS open.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check of the backend."""
    return {"status": "ok"}


@app.get("/config")
def config() -> dict:
    """The graphics default and the active LLM provider and model."""
    return {"graphics_enabled": get_settings().graphics_enabled, **providers.active()}


# =========================================================
# LLM providers
# =========================================================
@app.post("/providers/models")
def provider_models(req: ProviderRequest) -> dict[str, list[str]]:
    """The models a provider offers; starts the llama.cpp server if needed."""
    try:
        return {"models": providers.list_models(req.provider, base_url=req.base_url, api_key=req.api_key)}
    except providers.ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/providers/activate")
def provider_activate(req: ActivateRequest) -> dict:
    """Makes a model the active one."""
    try:
        providers.activate(req.provider, req.model, base_url=req.base_url, api_key=req.api_key)
    except providers.ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return providers.active()


# =========================================================
# PDFs
# =========================================================
@app.get("/pdfs")
def list_pdfs() -> dict[str, list[str]]:
    """All PDFs available in the PDF folder."""
    return {"pdfs": pdf.get_available_pdfs()}


@app.get("/pdfs/{name}/page-count")
def page_count(name: str) -> dict[str, int]:
    """The number of slides in the PDF."""
    return {"count": pdf.get_page_count(name)}


@app.get("/pdfs/{name}/pages/{page}/image")
def page_image(name: str, page: int) -> Response:
    """The slide as a JPG, unmarked — the preview in the frontend."""
    return Response(content=pdf.get_slide_image(name, page), media_type="image/jpeg")


@app.get("/pdfs/{name}/pages/{page}/graphics")
def page_graphics(name: str, page: int) -> dict[str, list[str]]:
    """The slide's graphics as base64 JPGs; the list index is the grafik:<idx> reference."""
    # Graphics are not part of the card store; they are re-extracted from the
    # PDF deterministically on demand (for the card preview).
    graphics = pdf.get_graphics(name, page)
    return {"graphics": [base64.b64encode(g).decode() for g in graphics]}


@app.get("/pdfs/{name}/pending")
def pending_pages(name: str) -> dict[str, list[int] | int]:
    """The slides without generated cards yet — one LLM request each."""
    total = pdf.get_page_count(name)
    slides = CardStorage(name).load().slides
    # An empty card list counts as done: the slide has nothing worth a card.
    pages = [page for page in range(1, total + 1) if str(page) not in slides]
    return {"pages": pages, "total": total}


@app.post("/pdfs", status_code=201)
async def upload_pdfs(files: list[UploadFile]) -> dict[str, list[str]]:
    """Stores uploaded PDFs in the PDF folder, overwriting files of the same name."""
    folder = get_settings().pdf_folder
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for file in files:
        (folder / file.filename).write_bytes(await file.read())
        saved.append(file.filename)
    return {"saved": saved}


# =========================================================
# Cards
# =========================================================
@app.get("/pdfs/{name}/pages/{page}/cards", response_model=PageCardsResponse)
def page_cards(name: str, page: int) -> PageCardsResponse:
    """The slide's cards from the card store, with the graphics mode they were generated in."""
    entry = CardStorage(name).get_page(page)
    if entry is None:
        return PageCardsResponse(generated=False, graphics=False, cards=[])
    return PageCardsResponse(generated=True, graphics=entry.graphics, cards=entry.cards)


@app.post("/pdfs/{name}/pages/{page}/cards/{index}/status", response_model=StoredCard)
def set_card_status(name: str, page: int, index: int, req: StatusRequest) -> StoredCard:
    """Rejects a card or brings it back to unreviewed; "accepted" needs a real export."""
    # Rejected cards stay in the card store: they are a quality signal about the
    # generation and are needed for the evaluation.
    storage = CardStorage(name)
    storage.set_status(page, index, req.status)
    return storage.get_page(page).cards[index]


@app.put("/pdfs/{name}/pages/{page}/cards/{index}", response_model=StoredCard)
def set_card_content(name: str, page: int, index: int, card: flashcard) -> StoredCard:
    """Replaces the markdown of a card (manual edit); its review status stays."""
    storage = CardStorage(name)
    storage.set_content(page, index, front=card.front, back=card.back)
    return storage.get_page(page).cards[index]


# =========================================================
# Generation
# =========================================================
# Graphics integration at a glance:
#   1. /generate extracts the graphics of the slide and marks them on the slide
#      image with numbered frames (only if the page has graphics).
#   2. The LLM references a graphic inline in the card's markdown:
#      ![Beschreibung](grafik:<idx>) — <idx> = frame number = list index.
#   3. The frontend replaces grafik:<idx> in the card preview with the base64
#      graphics from the /generate response.
#   4. /export stores referenced graphics as Anki media files and replaces
#      grafik:<idx> with the file name (see anki.py).
#
# The German strings below are user-visible messages and stay German on purpose.


@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest) -> GenerateResponse:
    """Generates the cards of one slide; 409 while cards of that slide are in Anki."""
    # Generating again replaces the cards of the slide — whatever is already in
    # Anki would be left behind as an orphaned note with no way back.
    storage = CardStorage(req.pdf_name)
    entry = storage.get_page(req.page)
    if entry and any(c.note_id is not None for c in entry.cards):
        raise HTTPException(
            status_code=409,
            detail=(
                "Von dieser Folie liegen bereits Karten in Anki. "
                "Bitte den Export erst rückgängig machen."
            ),
        )

    # Without a key the OpenAI client fails with a bare 500 — say what to do
    # instead. Local providers always carry a placeholder key.
    settings = get_settings()
    if not settings.provider_api_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "Kein Modell aktiv. Bitte oben über den Modell-Knopf einen Anbieter "
                "wählen und ein Modell aktivieren."
            ),
        )

    # Graphics mode off: no extraction, an unmarked image and a prompt without
    # the GRAFIKEN section — the second frozen input contract.
    mode = settings.graphics_mode(req.graphics)
    image, graphics = pdf.get_model_input(req.pdf_name, req.page, graphics=mode)
    generated = llm.generate_cards(image, graphics=mode, feedback=req.feedback)
    # The result goes into the card store — the single truth about generated
    # cards; the response returns it as well so the frontend need not reload.
    storage.save_page(
        req.page, generated,
        graphics=mode,
        marked=bool(graphics),
        generated_at=datetime.now().isoformat(timespec="seconds"),
        model=settings.default_model,
    )
    # JPG bytes are not JSON-capable -> base64 for the transport.
    return GenerateResponse(
        cards=generated,
        graphics=[base64.b64encode(g).decode() for g in graphics],
    )


# =========================================================
# Anki
# =========================================================
@app.get("/anki/status")
def anki_status() -> dict[str, bool]:
    """Whether AnkiConnect is reachable."""
    return {"running": anki.is_anki_running()}


@app.post("/anki/start", response_model=ActionResult)
def anki_start() -> ActionResult:
    """Launches the Anki desktop app and waits for AnkiConnect."""
    return anki.start_anki()


@app.get("/decks")
def list_decks() -> dict[str, list[str]]:
    """The Anki decks; an empty list while Anki is not running."""
    # Resilient: if Anki/AnkiConnect is not running, an empty list instead of a crash.
    if not anki.is_anki_running():
        return {"decks": []}
    return {"decks": anki.get_decks()}


@app.post("/decks", response_model=ActionResult)
def create_deck(req: CreateDeckRequest) -> ActionResult:
    """Creates a new Anki deck."""
    return anki.create_deck(req.name)


@app.post("/export", response_model=ActionResult)
def export(req: ExportRequest) -> ActionResult:
    """Exports a card to Anki and marks it accepted in the card store."""
    # Without an entry in the card store there is no place for the review status
    # and the note ID. So stop here *before* the note is created in Anki —
    # otherwise it would sit there without a trace in the store: no undo, and
    # the lock against generating again would no longer protect it.
    storage = CardStorage(req.pdf_name)
    entry = storage.get_page(req.page)
    if entry is None or not 0 <= req.index < len(entry.cards):
        return ActionResult(ok=False, message="Diese Karte steht nicht im Kartenbestand.")

    # What counts is the graphics mode the slide was generated in — not the one
    # currently selected. Otherwise the preview shows a card without an image
    # while a card with one lands in Anki (or the other way round).
    # Pass the slide's graphics along; whether the card references any is
    # decided by anki.export_card when it resolves the grafik:<idx> references.
    graphics = pdf.get_graphics(req.pdf_name, req.page) if entry.graphics else []
    result = anki.export_card(
        req.deck, req.card, req.page,
        pdf_name=req.pdf_name,
        graphics=graphics,
        generated_at=entry.generated_at,
    )
    # Accepting is the export: only once it worked is the card reviewed.
    if result.ok:
        storage.set_status(req.page, req.index, "accepted", note_id=result.note_id)
    return result


@app.post("/export/undo", response_model=ActionResult)
def export_undo(req: UndoRequest) -> ActionResult:
    """Deletes the exported note from Anki again and resets the card to unreviewed."""
    storage = CardStorage(req.pdf_name)
    card = storage.get_page(req.page).cards[req.index]
    if card.note_id is None:
        return ActionResult(ok=False, message="Diese Karte liegt nicht in Anki.")

    result = anki.delete_card(card.note_id)
    if result.ok:
        storage.set_status(req.page, req.index, "unreviewed", note_id=None)
    return result
