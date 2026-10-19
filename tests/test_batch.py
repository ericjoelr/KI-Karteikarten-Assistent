import threading
from unittest.mock import patch

import pytest

from backend import batch
from backend.config import get_settings
from backend.schemas import flashcard
from backend.storage import CardStorage


@pytest.fixture(autouse=True)
def batch_settings(monkeypatch):
    """Sets the batch configuration; without a per-minute limit it never waits."""

    def apply(**env):
        for key, value in env.items():
            monkeypatch.setenv(key.upper(), str(value))
        get_settings.cache_clear()

    apply(max_requests_per_minute=0, max_requests_per_run=100)
    return apply


def run(tmp_path, pages, llm_result=None, llm_answers=None, **kwargs):
    """Starts a run with a mocked PDF/LLM on a storage location of its own.

    llm_result: the same answer for every slide.
    llm_answers: answers in order (exceptions allowed) or a function.
    """
    with patch("backend.batch.pdf.get_graphics", return_value=[b"jpgdata"]), \
         patch("backend.batch.pdf.get_slide_image", return_value=b"img"), \
         patch("backend.batch.llm.generate_cards") as generate:
        if llm_answers is not None:
            generate.side_effect = llm_answers
        else:
            generate.return_value = [flashcard(front="F", back="B")] if llm_result is None else llm_result
        result = batch.run(CardStorage("x.pdf", tmp_path), pages, graphics=True, **kwargs)
    return result, generate


class rate_limit_error(Exception):
    status_code = 429


# =========================================================
# Results in the card store
# =========================================================
def test_run_fills_the_card_store_for_every_page(tmp_path):
    result, _ = run(tmp_path, [1, 2, 3])

    assert (result.status, result.generated, result.failed) == ("done", 3, 0)
    for page in (1, 2, 3):
        entry = CardStorage("x.pdf", tmp_path).get_page(page)
        assert [c.front for c in entry.cards] == ["F"]
        assert entry.error is None


def test_page_without_cards_is_stored_as_a_finished_entry(tmp_path):
    run(tmp_path, [1], llm_result=[])

    entry = CardStorage("x.pdf", tmp_path).get_page(1)
    assert entry is not None and entry.cards == [] and entry.error is None


def test_each_reserved_slot_is_exactly_one_provider_request(tmp_path):
    # Counted at the client, not at a parameter: one slide, one provider request,
    # otherwise throttle and budget would count too low.
    with patch("backend.batch.pdf.get_graphics", return_value=[b"jpgdata"]), \
         patch("backend.batch.pdf.get_slide_image", return_value=b"img"), \
         patch("backend.llm.OpenAI") as openai:
        create = openai.return_value.chat.completions.create
        create.return_value.choices[0].message.content = '{"cards": []}'
        batch.run(CardStorage("x.pdf", tmp_path), [1], graphics=True)

    assert create.call_count == 1


def test_run_records_mode_marking_time_and_model(tmp_path):
    run(tmp_path, [1])

    entry = CardStorage("x.pdf", tmp_path).get_page(1)
    assert entry.graphics is True
    assert entry.marked is True  # graphics found -> marked image
    assert entry.generated_at and entry.model


def test_all_pages_of_a_run_share_one_timestamp(tmp_path):
    run(tmp_path, [1, 2])

    stamps = {CardStorage("x.pdf", tmp_path).get_page(p).generated_at for p in (1, 2)}
    assert len(stamps) == 1


def test_graphics_off_uses_an_unmarked_image_and_skips_extraction(tmp_path):
    with patch("backend.batch.pdf.get_graphics") as graphics, \
         patch("backend.batch.pdf.get_slide_image", return_value=b"img") as image, \
         patch("backend.batch.llm.generate_cards", return_value=[]):
        batch.run(CardStorage("x.pdf", tmp_path), [1], graphics=False)

    graphics.assert_not_called()
    assert image.call_args.kwargs["mark_graphics"] is False
    assert CardStorage("x.pdf", tmp_path).get_page(1).marked is False


# =========================================================
# Resuming
# =========================================================
def test_rerun_skips_finished_pages_and_redoes_missing_and_failed(tmp_path):
    run(tmp_path, [1, 2])  # 1 and 2 are done ...
    CardStorage("x.pdf", tmp_path).save_page(
        2, [], graphics=True, marked=True,
        generated_at="t", model="m", error="broken",
    )  # ... 2 becomes a failed slide afterwards

    _, generate = run(tmp_path, [1, 2, 3])

    assert generate.call_count == 2  # only 2 (failed) and 3 (missing)
    assert CardStorage("x.pdf", tmp_path).get_page(2).error is None


def test_page_with_empty_card_list_is_not_generated_again(tmp_path):
    run(tmp_path, [1], llm_result=[])

    _, generate = run(tmp_path, [1])

    generate.assert_not_called()


# =========================================================
# Errors, quota and cancellation
# =========================================================
def test_failing_page_is_retried_once_then_marked_and_the_run_continues(tmp_path):
    answers = [RuntimeError("boom"), RuntimeError("boom"), [flashcard(front="F", back="B")]]

    result, generate = run(tmp_path, [1, 2], llm_answers=answers)

    assert generate.call_count == 3  # slide 1: attempt + retry, slide 2: once
    assert (result.status, result.generated, result.failed) == ("done", 1, 1)
    failed = CardStorage("x.pdf", tmp_path).get_page(1)
    assert failed.cards == [] and "boom" in failed.error
    assert CardStorage("x.pdf", tmp_path).get_page(2).error is None


def test_rate_limit_stops_the_run_and_keeps_what_was_generated(tmp_path):
    answers = [[flashcard(front="F", back="B")], rate_limit_error(), [flashcard(front="F", back="B")]]

    result, generate = run(tmp_path, [1, 2, 3], llm_answers=answers)

    assert result.status == "rate_limited"
    assert generate.call_count == 2  # no retry, no third slide
    assert CardStorage("x.pdf", tmp_path).get_page(1) is not None
    assert CardStorage("x.pdf", tmp_path).get_page(2) is None
    assert CardStorage("x.pdf", tmp_path).get_page(3) is None


def test_cancel_flag_stops_before_the_next_page(tmp_path):
    cancel = threading.Event()

    def generate_then_cancel(*args, **kwargs):
        cancel.set()
        return [flashcard(front="F", back="B")]

    result, generate = run(tmp_path, [1, 2, 3], llm_answers=generate_then_cancel, cancel=cancel)

    assert result.status == "cancelled"
    assert generate.call_count == 1
    assert CardStorage("x.pdf", tmp_path).get_page(1) is not None


def test_run_stops_at_the_request_limit(tmp_path, batch_settings):
    batch_settings(max_requests_per_run=2)

    result, generate = run(tmp_path, [1, 2, 3, 4])

    assert result.status == "limit_reached"
    assert generate.call_count == 2
    assert result.generated == 2


# =========================================================
# Throttling (faked clock, no real waiting)
# =========================================================
def test_limiter_spaces_consecutive_requests(monkeypatch):
    monkeypatch.setattr(batch.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(batch.time, "sleep", lambda seconds: None)
    limiter = batch._limiter(interval=4.0, budget=10)

    # Every request gets its start slot at the minimum gap from the previous one.
    assert [limiter.take() for _ in range(5)] == [0.0, 4.0, 8.0, 12.0, 16.0]


def test_run_waits_the_configured_minimum_gap(tmp_path, monkeypatch, batch_settings):
    batch_settings(max_requests_per_minute=60)  # 1 request per second
    monkeypatch.setattr(batch.time, "monotonic", lambda: 0.0)
    waits: list[float] = []
    monkeypatch.setattr(batch.time, "sleep", waits.append)

    run(tmp_path, [1, 2, 3])

    assert waits == [1.0, 2.0]  # the first request starts immediately
