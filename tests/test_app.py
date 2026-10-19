import base64
import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend import anki, batch, schemas
from backend.storage import CardStorage
from backend.config import Settings
from backend.schemas import flashcard
from backend.main import app

client = TestClient(app)


def test_generate_route_forwards_feedback_to_llm():
    sample = [flashcard(front="F", back="B")]
    with patch("backend.main.pdf.get_graphics", return_value=[]), \
         patch("backend.main.pdf.get_slide_image", return_value=b"img") as mock_image, \
         patch("backend.main.llm.generate_cards", return_value=sample) as mock:
        resp = client.post("/generate", json={"pdf_name": "x.pdf", "page": 1, "feedback": "shorter"})

    assert resp.status_code == 200
    assert resp.json()["cards"] == [{"front": "F", "back": "B"}]
    assert mock.call_args.kwargs["feedback"] == "shorter"
    # Without graphics the slide is rendered unmarked.
    assert mock_image.call_args.kwargs["mark_graphics"] is False


def test_generate_route_returns_graphics_and_marks_slide():
    sample = [flashcard(front="F", back="![d](grafik:0)")]
    with patch("backend.main.pdf.get_graphics", return_value=[b"jpgdata"]), \
         patch("backend.main.pdf.get_slide_image", return_value=b"img") as mock_image, \
         patch("backend.main.llm.generate_cards", return_value=sample):
        resp = client.post("/generate", json={"pdf_name": "x.pdf", "page": 1})

    assert resp.status_code == 200
    assert resp.json()["graphics"] == [base64.b64encode(b"jpgdata").decode()]
    assert mock_image.call_args.kwargs["mark_graphics"] is True


def test_generate_route_without_graphics_skips_extraction_and_marks():
    sample = [flashcard(front="F", back="B")]
    with patch("backend.main.pdf.get_graphics") as mock_graphics, \
         patch("backend.main.pdf.get_slide_image", return_value=b"img") as mock_image, \
         patch("backend.main.llm.generate_cards", return_value=sample) as mock_llm:
        resp = client.post("/generate", json={"pdf_name": "x.pdf", "page": 1, "graphics": False})

    assert resp.status_code == 200
    assert resp.json()["graphics"] == []
    mock_graphics.assert_not_called()
    assert mock_image.call_args.kwargs["mark_graphics"] is False
    assert mock_llm.call_args.kwargs["graphics"] is False


def test_generate_route_uses_configured_default_when_mode_omitted():
    sample = [flashcard(front="F", back="B")]
    with patch("backend.main.get_settings", return_value=Settings(graphics_enabled=False)), \
         patch("backend.main.pdf.get_graphics") as mock_graphics, \
         patch("backend.main.pdf.get_slide_image", return_value=b"img"), \
         patch("backend.main.llm.generate_cards", return_value=sample) as mock_llm:
        resp = client.post("/generate", json={"pdf_name": "x.pdf", "page": 1})

    assert resp.status_code == 200
    mock_graphics.assert_not_called()
    assert mock_llm.call_args.kwargs["graphics"] is False


def test_system_prompt_contains_graphics_block_only_when_enabled():
    settings = Settings()

    assert "Grafiken" in settings.system_prompt(graphics=True)
    assert "grafik:" in settings.system_prompt(graphics=True)
    assert "Grafiken" not in settings.system_prompt(graphics=False)
    assert "grafik:" not in settings.system_prompt(graphics=False)
    # Both contracts are frozen: the graphics prompt is the base plus exactly
    # one blank line plus the graphics section (ADR-0003).
    base = settings.system_prompt(graphics=False)
    assert settings.system_prompt(graphics=True).startswith(base + "\n\n# Grafiken:")


def test_config_route_reports_graphics_default():
    with patch("backend.main.get_settings", return_value=Settings(graphics_enabled=False)):
        resp = client.get("/config")

    assert resp.json()["graphics_enabled"] is False


# =========================================================
# Anki
# =========================================================
def test_inline_latex_becomes_mathjax():
    html = anki.markdown_to_anki_html("Formel $x^2$ hier")
    assert r"\(x^2\)" in html


def test_block_latex_without_blank_lines_is_normalized():
    html = anki.markdown_to_anki_html("Text\n$$a=b$$")
    assert r"\[a=b\]" in html


def test_unbalanced_code_fence_is_closed():
    html = anki.markdown_to_anki_html("```python\nprint(1)")
    assert "<code" in html


def test_grafik_reference_renders_as_img_tag():
    html = anki.markdown_to_anki_html("Answer:\n\n![Iceberg-Architektur](grafik:0)")
    assert '<img alt="Iceberg-Architektur" src="grafik:0"' in html


def test_export_renders_both_card_fields_as_html():
    with patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request", return_value=1234) as mock_request:
        anki.export_card("Deck", flashcard(front="$x$", back="- a\n- b"), 1)

    note = next(c for c in mock_request.call_args_list if c.args[0] == "addNote")
    fields = note.kwargs["note"]["fields"]
    assert r"\(x\)" in fields["Front"]
    assert "<li>" in fields["Back"]


def test_export_returns_error_when_anki_down():
    with patch("backend.anki.is_anki_running", return_value=False):
        result = anki.export_card("Deck", flashcard(front="F", back="B"), 1)

    assert result.ok is False
    assert "Anki" in result.message


def test_export_succeeds_and_returns_note_id():
    with patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request", return_value=1234):
        result = anki.export_card("Deck", flashcard(front="F", back="B"), 3)

    assert result.ok is True
    assert result.note_id == 1234


def test_export_tags_card_with_slide_pdf_and_run_timestamp():
    with patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request", return_value=1234) as mock_request:
        anki.export_card(
            "Deck", flashcard(front="F", back="B"), 7,
            pdf_name="02 - Distributed data.pdf", generated_at="2026-07-28T14:30:05",
        )

    note = next(c for c in mock_request.call_args_list if c.args[0] == "addNote")
    assert note.kwargs["note"]["tags"] == [
        "folie_7", "pdf_02_Distributed_data", "generiert_2026-07-28_14-30-05",
    ]


def test_export_omits_the_timestamp_tag_without_a_run_time():
    with patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request", return_value=1234) as mock_request:
        anki.export_card("Deck", flashcard(front="F", back="B"), 7, pdf_name="x.pdf")

    note = next(c for c in mock_request.call_args_list if c.args[0] == "addNote")
    assert note.kwargs["note"]["tags"] == ["folie_7", "pdf_x"]


def test_export_note_type_stays_basic():
    with patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request", return_value=1234) as mock_request:
        anki.export_card("Deck", flashcard(front="F", back="B"), 1)

    note = next(c for c in mock_request.call_args_list if c.args[0] == "addNote")
    assert note.kwargs["note"]["modelName"] == "Basic"


def test_export_stores_referenced_graphic_and_rewrites_src():
    card = flashcard(front="F", back="![Diagramm](grafik:0)")
    calls: dict[str, list[dict]] = {}

    def fake_request(action, **params):
        calls.setdefault(action, []).append(params)
        return 1234

    with patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request", side_effect=fake_request):
        result = anki.export_card(
            "Deck", card, 19,
            pdf_name="02 - Distributed data.pdf", graphics=[b"jpgdata"],
        )

    assert result.ok is True
    stored = calls["storeMediaFile"][0]
    assert stored["filename"] == "ankiagent_02_Distributed_data_19_0.jpg"
    assert stored["data"] == base64.b64encode(b"jpgdata").decode()
    back_html = calls["addNote"][0]["note"]["fields"]["Back"]
    assert 'src="ankiagent_02_Distributed_data_19_0.jpg"' in back_html
    assert "grafik:" not in back_html


def test_export_removes_reference_to_missing_graphic():
    card = flashcard(front="F", back="Text ![x](grafik:5) end")
    with patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request", return_value=1234) as mock_request:
        result = anki.export_card("Deck", card, 1, pdf_name="x.pdf", graphics=[])

    assert result.ok is True
    actions = [c.args[0] for c in mock_request.call_args_list]
    assert "storeMediaFile" not in actions
    note = next(c for c in mock_request.call_args_list if c.args[0] == "addNote")
    assert "<img" not in note.kwargs["note"]["fields"]["Back"]


def test_undo_deletes_note_by_id():
    with patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request") as mock_request:
        result = anki.delete_card(1234)

    mock_request.assert_called_once_with("deleteNotes", notes=[1234])
    assert result.ok is True


def test_undo_returns_error_when_anki_down():
    with patch("backend.anki.is_anki_running", return_value=False):
        result = anki.delete_card(1234)

    assert result.ok is False


def test_decks_route_empty_when_anki_down():
    with patch("backend.main.anki.is_anki_running", return_value=False):
        resp = client.get("/decks")

    assert resp.json() == {"decks": []}


def test_create_deck_calls_ankiconnect():
    with patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki._request") as mock_request:
        result = anki.create_deck("Vorlesung 4")

    mock_request.assert_called_once_with("createDeck", deck="Vorlesung 4")
    assert result.ok is True


def test_create_deck_returns_error_when_anki_down():
    with patch("backend.anki.is_anki_running", return_value=False):
        result = anki.create_deck("Vorlesung 4")

    assert result.ok is False


def test_start_anki_skips_launch_when_already_running():
    with patch("backend.anki.is_anki_running", return_value=True), \
         patch("backend.anki.subprocess.Popen") as mock_popen:
        result = anki.start_anki()

    mock_popen.assert_not_called()
    assert result.ok is True


def test_start_anki_launches_and_waits_until_up():
    # first down (check + loop), then up -> recognized as started
    with patch("backend.anki.is_anki_running", side_effect=[False, True]), \
         patch("backend.anki.subprocess.Popen") as mock_popen:
        result = anki.start_anki()

    mock_popen.assert_called_once()
    assert result.ok is True


def test_anki_status_route_reports_running():
    with patch("backend.main.anki.is_anki_running", return_value=True):
        resp = client.get("/anki/status")

    assert resp.json() == {"running": True}


# =========================================================
# Batch run over the routes
# =========================================================
@pytest.fixture(autouse=True)
def idle_batch():
    """No run left over from a previous test; the progress is process state."""
    batch._job = None
    batch._cancel.clear()
    yield
    batch._job = None


def wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def batch_status() -> dict | None:
    return client.get("/generate/batch/status").json()


def start_batch(**payload) -> dict:
    return client.post("/generate/batch", json={"pdf_name": "x.pdf", **payload})


def held_run(release: threading.Event, result: schemas.BatchResult, seen: dict):
    """Stand-in for batch.run: reports one finished slide, then waits."""

    def fake(storage, pages, *, graphics, cancel=None, on_progress=None):
        seen.update(pages=pages, graphics=graphics, cancel=cancel)
        if on_progress:
            on_progress(1)
        release.wait(3)
        if cancel is not None and cancel.is_set():
            return schemas.BatchResult(status="cancelled", generated=1, failed=0)
        return result

    return fake


def test_batch_status_is_empty_before_any_run():
    assert batch_status() is None


def test_batch_start_covers_only_the_pages_that_still_need_work():
    CardStorage("x.pdf").save_page(
        2, [flashcard(front="F", back="B")],
        graphics=False, marked=False, generated_at="t", model="m",
    )
    release, seen = threading.Event(), {}
    with patch("backend.main.pdf.get_page_count", return_value=3), \
         patch("backend.main.batch.run", side_effect=held_run(
             release, schemas.BatchResult(status="done", generated=2, failed=0), seen)):
        resp = start_batch()
        assert resp.status_code == 202
        assert wait_until(lambda: "pages" in seen)
        release.set()
        assert wait_until(lambda: batch_status()["running"] is False)

    assert seen["pages"] == [1, 3]
    assert resp.json()["total"] == 2


def test_batch_start_rejects_a_second_run():
    release, seen = threading.Event(), {}
    with patch("backend.main.pdf.get_page_count", return_value=3), \
         patch("backend.main.batch.run", side_effect=held_run(
             release, schemas.BatchResult(status="done", generated=3, failed=0), seen)):
        first = start_batch()
        second = start_batch()
        release.set()
        assert wait_until(lambda: batch_status()["running"] is False)

    assert first.status_code == 202
    assert second.status_code == 409
    assert "läuft" in second.json()["detail"]


def test_batch_status_route_reports_progress_and_result():
    release, seen = threading.Event(), {}
    with patch("backend.main.pdf.get_page_count", return_value=3), \
         patch("backend.main.batch.run", side_effect=held_run(
             release, schemas.BatchResult(status="done", generated=3, failed=0), seen)):
        start_batch()
        assert wait_until(lambda: batch_status()["done"] == 1)
        running = batch_status()
        release.set()
        assert wait_until(lambda: batch_status()["running"] is False)
        finished = batch_status()

    assert (running["pdf_name"], running["total"], running["running"]) == ("x.pdf", 3, True)
    assert finished["status"] == "done"
    assert "3" in finished["message"]


def test_batch_cancel_route_stops_the_run():
    release, seen = threading.Event(), {}
    with patch("backend.main.pdf.get_page_count", return_value=2), \
         patch("backend.main.batch.run", side_effect=held_run(
             release, schemas.BatchResult(status="done", generated=2, failed=0), seen)):
        start_batch()
        assert wait_until(lambda: "cancel" in seen)
        cancel = client.post("/generate/batch/cancel")
        release.set()
        assert wait_until(lambda: batch_status()["running"] is False)

    assert cancel.status_code == 200
    assert seen["cancel"].is_set()
    assert batch_status()["status"] == "cancelled"


def test_batch_cancel_without_a_run_is_rejected():
    assert client.post("/generate/batch/cancel").status_code == 409


def test_exhausted_quota_is_reported_in_plain_text():
    release, seen = threading.Event(), {}
    release.set()
    with patch("backend.main.pdf.get_page_count", return_value=5), \
         patch("backend.main.batch.run", side_effect=held_run(
             release, schemas.BatchResult(status="rate_limited", generated=2, failed=0), seen)):
        start_batch()
        assert wait_until(lambda: batch_status()["running"] is False)

    message = batch_status()["message"]
    assert "2" in message and "Kontingent" in message


def test_batch_start_passes_the_graphics_mode():
    release, seen = threading.Event(), {}
    release.set()
    with patch("backend.main.pdf.get_page_count", return_value=1), \
         patch("backend.main.batch.run", side_effect=held_run(
             release, schemas.BatchResult(status="done", generated=1, failed=0), seen)):
        start_batch(graphics=False)
        assert wait_until(lambda: batch_status()["running"] is False)

    assert seen["graphics"] is False


def test_pending_route_counts_the_open_pages():
    CardStorage("x.pdf").save_page(
        1, [], graphics=False, marked=False, generated_at="t", model="m",
    )
    with patch("backend.main.pdf.get_page_count", return_value=4):
        resp = client.get("/pdfs/x.pdf/pending")

    assert resp.json() == {"pending": 3, "total": 4}
