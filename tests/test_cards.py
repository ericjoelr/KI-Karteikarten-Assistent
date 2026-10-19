import base64
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.storage import CardStorage
from backend.schemas import flashcard
from backend.main import app

client = TestClient(app)


def generate(page: int = 1, graphics: bool | None = None, sample=None):
    """Generates cards through the route with a mocked PDF/LLM."""
    sample = [flashcard(front="F", back="B")] if sample is None else sample
    body: dict = {"pdf_name": "x.pdf", "page": page}
    if graphics is not None:
        body["graphics"] = graphics
    with patch("backend.main.pdf.get_graphics", return_value=[b"jpgdata"]), \
         patch("backend.main.pdf.get_slide_image", return_value=b"img"), \
         patch("backend.main.llm.generate_cards", return_value=sample):
        return client.post("/generate", json=body)


# =========================================================
# Card store over the routes
# =========================================================
def test_generated_cards_are_readable_as_unreviewed():
    generate(page=3)

    resp = client.get("/pdfs/x.pdf/pages/3/cards")

    assert resp.status_code == 200
    body = resp.json()
    assert body["generated"] is True
    assert body["cards"] == [
        {"front": "F", "back": "B", "status": "unreviewed", "note_id": None}
    ]


def test_page_without_generation_reports_not_generated():
    resp = client.get("/pdfs/x.pdf/pages/7/cards")

    assert resp.json() == {"generated": False, "graphics": False, "cards": []}


def test_page_with_empty_card_list_counts_as_generated():
    generate(page=4, sample=[])

    body = client.get("/pdfs/x.pdf/pages/4/cards").json()
    assert body == {"generated": True, "graphics": True, "cards": []}


def test_generate_still_returns_cards_in_response():
    resp = generate(page=1)

    assert resp.json()["cards"] == [{"front": "F", "back": "B"}]


def test_stored_page_records_run_metadata():
    with patch("backend.main.pdf.get_graphics", return_value=[]), \
         patch("backend.main.pdf.get_slide_image", return_value=b"img"), \
         patch("backend.main.llm.generate_cards", return_value=[flashcard(front="F", back="B")]):
        client.post("/generate", json={"pdf_name": "x.pdf", "page": 2, "graphics": True})

    page = CardStorage("x.pdf").get_page(2)
    assert page.graphics is True
    # No graphics on the slide -> the image was unmarked.
    assert page.marked is False
    assert page.model
    assert page.generated_at


def test_generate_overwrites_previous_cards_of_the_page():
    generate(page=5, sample=[flashcard(front="old", back="old")])
    generate(page=5, sample=[flashcard(front="new", back="new")])

    stored = client.get("/pdfs/x.pdf/pages/5/cards").json()["cards"]
    assert [c["front"] for c in stored] == ["new"]


def test_graphics_route_returns_base64_graphics():
    with patch("backend.main.pdf.get_graphics", return_value=[b"jpgdata", b"more"]):
        resp = client.get("/pdfs/x.pdf/pages/1/graphics")

    assert resp.json() == {
        "graphics": [
            base64.b64encode(b"jpgdata").decode(),
            base64.b64encode(b"more").decode(),
        ]
    }


# =========================================================
# The export follows the stored graphics mode
# =========================================================
def test_export_of_page_generated_without_graphics_drops_reference():
    card = flashcard(front="F", back="Text ![x](grafik:0) end")
    generate(page=9, graphics=False, sample=[card])

    with patch("backend.main.pdf.get_graphics") as mock_graphics, \
         patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request", return_value=1234) as mock_request:
        resp = client.post("/export", json={
            "deck": "D", "card": card.model_dump(), "page": 9, "pdf_name": "x.pdf", "index": 0,
        })

    assert resp.json()["ok"] is True
    # Without graphics mode nothing is extracted in the first place ...
    mock_graphics.assert_not_called()
    actions = [c.args[0] for c in mock_request.call_args_list]
    assert "storeMediaFile" not in actions
    # ... and the reference disappears just like in the preview.
    note = next(c for c in mock_request.call_args_list if c.args[0] == "addNote")
    assert "<img" not in note.kwargs["note"]["fields"]["Back"]


def test_export_of_page_generated_with_graphics_stores_media():
    card = flashcard(front="F", back="![x](grafik:0)")
    generate(page=8, graphics=True, sample=[card])

    with patch("backend.main.pdf.get_graphics", return_value=[b"jpgdata"]), \
         patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request", return_value=1234) as mock_request:
        client.post("/export", json={
            "deck": "D", "card": card.model_dump(), "page": 8, "pdf_name": "x.pdf", "index": 0,
        })

    actions = [c.args[0] for c in mock_request.call_args_list]
    assert "storeMediaFile" in actions


# =========================================================
# Review status over the routes
# =========================================================
def export(page: int, index: int, note_id: int = 1234, card=None):
    """Exports a card through the route with a mocked AnkiConnect."""
    card = card or flashcard(front="F", back="B")
    with patch("backend.main.pdf.get_graphics", return_value=[b"jpgdata"]), \
         patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request", return_value=note_id):
        return client.post("/export", json={
            "deck": "D", "card": card.model_dump(), "page": page,
            "pdf_name": "x.pdf", "index": index,
        })


def test_export_marks_card_as_accepted_with_note_id():
    generate(page=20)

    resp = export(page=20, index=0, note_id=555)

    assert resp.json()["ok"] is True
    card = CardStorage("x.pdf").get_page(20).cards[0]
    assert (card.status, card.note_id) == ("accepted", 555)


def test_export_tags_the_card_with_the_stored_run_time():
    generate(page=30)
    generated_at = CardStorage("x.pdf").get_page(30).generated_at

    with patch("backend.main.pdf.get_graphics", return_value=[]), \
         patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request", return_value=1) as mock_request:
        client.post("/export", json={
            "deck": "D", "card": {"front": "F", "back": "B"}, "page": 30,
            "pdf_name": "x.pdf", "index": 0,
        })

    note = next(c for c in mock_request.call_args_list if c.args[0] == "addNote")
    # All cards of one run share the same timestamp — it comes from the card
    # store, not from the moment of the export.
    assert f"generiert_{generated_at.replace('T', '_').replace(':', '-')}" in note.kwargs["note"]["tags"]


def test_export_of_a_card_outside_the_store_is_refused_before_anki():
    generate(page=31)  # one card -> index 1 does not exist

    with patch("backend.main.pdf.get_graphics", return_value=[]), \
         patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request") as mock_request:
        resp = client.post("/export", json={
            "deck": "D", "card": {"front": "F", "back": "B"}, "page": 31,
            "pdf_name": "x.pdf", "index": 1,
        })

    # Do not create a note at all: it would otherwise sit in Anki without the
    # card store knowing about it (no review status, no undo).
    assert resp.json()["ok"] is False
    mock_request.assert_not_called()


def test_export_of_a_page_outside_the_store_is_refused_before_anki():
    with patch("backend.main.pdf.get_graphics", return_value=[]), \
         patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request") as mock_request:
        resp = client.post("/export", json={
            "deck": "D", "card": {"front": "F", "back": "B"}, "page": 32,
            "pdf_name": "x.pdf", "index": 0,
        })

    assert resp.json()["ok"] is False
    mock_request.assert_not_called()


def test_failed_export_leaves_the_card_unreviewed():
    generate(page=21)

    with patch("backend.main.pdf.get_graphics", return_value=[]), \
         patch("backend.anki.is_anki_running", return_value=False):
        resp = client.post("/export", json={
            "deck": "D", "card": {"front": "F", "back": "B"}, "page": 21,
            "pdf_name": "x.pdf", "index": 0,
        })

    assert resp.json()["ok"] is False
    card = CardStorage("x.pdf").get_page(21).cards[0]
    assert (card.status, card.note_id) == ("unreviewed", None)


def test_undo_deletes_the_note_and_resets_the_card():
    generate(page=22)
    export(page=22, index=0, note_id=777)

    with patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request") as mock_request:
        resp = client.post("/export/undo", json={
            "pdf_name": "x.pdf", "page": 22, "index": 0,
        })

    assert resp.json()["ok"] is True
    mock_request.assert_called_once_with("deleteNotes", notes=[777])
    card = CardStorage("x.pdf").get_page(22).cards[0]
    assert (card.status, card.note_id) == ("unreviewed", None)


def test_undo_of_a_card_that_was_never_exported_reports_an_error():
    generate(page=23)

    resp = client.post("/export/undo", json={"pdf_name": "x.pdf", "page": 23, "index": 0})

    assert resp.json()["ok"] is False


def test_rejecting_keeps_the_card_in_the_store():
    generate(page=24)

    resp = client.post("/pdfs/x.pdf/pages/24/cards/0/status", json={"status": "rejected"})

    assert resp.status_code == 200
    card = CardStorage("x.pdf").get_page(24).cards[0]
    assert card.status == "rejected"
    assert (card.front, card.back) == ("F", "B")


def test_status_route_cannot_fake_an_export():
    generate(page=25)

    resp = client.post("/pdfs/x.pdf/pages/25/cards/0/status", json={"status": "accepted"})

    assert resp.status_code == 422
    assert CardStorage("x.pdf").get_page(25).cards[0].status == "unreviewed"


def test_content_route_replaces_the_card_and_keeps_the_status():
    generate(page=26)
    client.post("/pdfs/x.pdf/pages/26/cards/0/status", json={"status": "rejected"})

    resp = client.put("/pdfs/x.pdf/pages/26/cards/0", json={"front": "new", "back": "also new"})

    assert resp.status_code == 200
    card = CardStorage("x.pdf").get_page(26).cards[0]
    assert (card.front, card.back) == ("new", "also new")
    assert card.status == "rejected"


# =========================================================
# Generating again while cards are in Anki
# =========================================================
def test_generate_is_refused_while_a_card_of_the_page_is_exported():
    generate(page=27, sample=[flashcard(front="old", back="old")])
    export(page=27, index=0)

    resp = generate(page=27, sample=[flashcard(front="new", back="new")])

    assert resp.status_code == 409
    assert "Anki" in resp.json()["detail"]
    # The old cards stay untouched.
    assert CardStorage("x.pdf").get_page(27).cards[0].front == "old"


def test_generate_is_allowed_again_after_undo():
    generate(page=28)
    export(page=28, index=0)
    with patch("backend.anki.is_anki_running", return_value=True), patch("backend.anki._request"):
        client.post("/export/undo", json={"pdf_name": "x.pdf", "page": 28, "index": 0})

    resp = generate(page=28, sample=[flashcard(front="new", back="new")])

    assert resp.status_code == 200


def test_generate_is_allowed_when_the_page_only_has_rejected_cards():
    generate(page=29)
    client.post("/pdfs/x.pdf/pages/29/cards/0/status", json={"status": "rejected"})

    assert generate(page=29).status_code == 200


# =========================================================
# The card store module directly
# =========================================================
def test_update_functions_change_status_and_content():
    generate(page=6)

    CardStorage("x.pdf").set_status(6, 0, "accepted", note_id=42)
    CardStorage("x.pdf").set_content(6, 0, front="new", back="new text")

    card = CardStorage("x.pdf").get_page(6).cards[0]
    assert (card.status, card.note_id) == ("accepted", 42)
    assert (card.front, card.back) == ("new", "new text")


def test_status_change_without_note_id_keeps_the_existing_one():
    generate(page=10)
    CardStorage("x.pdf").set_status(10, 0, "accepted", note_id=999)

    CardStorage("x.pdf").set_status(10, 0, "rejected")

    # Note 999 is still in Anki -> the ID has to stay, otherwise the export
    # can no longer be undone.
    assert CardStorage("x.pdf").get_page(10).cards[0].note_id == 999


def test_note_id_none_clears_it_explicitly():
    generate(page=11)
    CardStorage("x.pdf").set_status(11, 0, "accepted", note_id=999)

    CardStorage("x.pdf").set_status(11, 0, "unreviewed", note_id=None)

    assert CardStorage("x.pdf").get_page(11).cards[0].note_id is None
