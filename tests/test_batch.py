import threading
from unittest.mock import patch

import httpx
from openai import RateLimitError

from backend import batch
from backend.schemas import flashcard
from backend.storage import CardStorage


def run(pages, llm_result=None, llm_answers=None, **kwargs):
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
        result = batch.run(CardStorage("x.pdf"), pages, graphics=True, **kwargs)
    return result, generate


def rate_limit_error() -> RateLimitError:
    response = httpx.Response(429, request=httpx.Request("POST", "http://provider"))
    return RateLimitError("quota exhausted", response=response, body=None)


# =========================================================
# Results in the card store
# =========================================================
def test_run_fills_the_card_store_for_every_page():
    result, _ = run([1, 2, 3])

    assert (result.status, result.generated, result.failed) == ("done", 3, 0)
    for page in (1, 2, 3):
        entry = CardStorage("x.pdf").get_page(page)
        assert [c.front for c in entry.cards] == ["F"]
        assert entry.error is None


def test_page_without_cards_is_stored_as_a_finished_entry():
    run([1], llm_result=[])

    entry = CardStorage("x.pdf").get_page(1)
    assert entry is not None and entry.cards == [] and entry.error is None


def test_each_slide_is_exactly_one_provider_request():
    # Counted at the client, not at a parameter: the request count shown before
    # a run (pending slides) must match what the run really sends.
    with patch("backend.batch.pdf.get_graphics", return_value=[b"jpgdata"]), \
         patch("backend.batch.pdf.get_slide_image", return_value=b"img"), \
         patch("backend.llm.OpenAI") as openai:
        create = openai.return_value.chat.completions.create
        create.return_value.choices[0].message.content = '{"cards": []}'
        batch.run(CardStorage("x.pdf"), [1], graphics=True)

    assert create.call_count == 1


def test_run_records_mode_marking_time_and_model():
    run([1])

    entry = CardStorage("x.pdf").get_page(1)
    assert entry.graphics is True
    assert entry.marked is True  # graphics found -> marked image
    assert entry.generated_at and entry.model


def test_all_pages_of_a_run_share_one_timestamp():
    run([1, 2])

    stamps = {CardStorage("x.pdf").get_page(p).generated_at for p in (1, 2)}
    assert len(stamps) == 1


def test_graphics_off_uses_an_unmarked_image_and_skips_extraction():
    with patch("backend.batch.pdf.get_graphics") as graphics, \
         patch("backend.batch.pdf.get_slide_image", return_value=b"img") as image, \
         patch("backend.batch.llm.generate_cards", return_value=[]):
        batch.run(CardStorage("x.pdf"), [1], graphics=False)

    graphics.assert_not_called()
    assert image.call_args.kwargs["mark_graphics"] is False
    assert CardStorage("x.pdf").get_page(1).marked is False


# =========================================================
# Resuming
# =========================================================
def test_rerun_skips_finished_pages_and_redoes_missing_and_failed():
    run([1, 2])  # 1 and 2 are done ...
    CardStorage("x.pdf").save_page(
        2, [], graphics=True, marked=True,
        generated_at="t", model="m", error="broken",
    )  # ... 2 becomes a failed slide afterwards

    _, generate = run([1, 2, 3])

    assert generate.call_count == 2  # only 2 (failed) and 3 (missing)
    assert CardStorage("x.pdf").get_page(2).error is None


def test_page_with_empty_card_list_is_not_generated_again():
    run([1], llm_result=[])

    _, generate = run([1])

    generate.assert_not_called()


# =========================================================
# Errors, quota and cancellation
# =========================================================
def test_failing_page_is_marked_and_the_run_continues():
    answers = [RuntimeError("boom"), [flashcard(front="F", back="B")]]

    result, generate = run([1, 2], llm_answers=answers)

    assert generate.call_count == 2  # no retry: the next run picks slide 1 up again
    assert (result.status, result.generated, result.failed) == ("done", 1, 1)
    failed = CardStorage("x.pdf").get_page(1)
    assert failed.cards == [] and "boom" in failed.error
    assert CardStorage("x.pdf").get_page(2).error is None


def test_rate_limit_stops_the_run_and_keeps_what_was_generated():
    answers = [[flashcard(front="F", back="B")], rate_limit_error(), [flashcard(front="F", back="B")]]

    result, generate = run([1, 2, 3], llm_answers=answers)

    assert result.status == "rate_limited"
    assert generate.call_count == 2  # no third slide
    assert CardStorage("x.pdf").get_page(1) is not None
    assert CardStorage("x.pdf").get_page(2) is None
    assert CardStorage("x.pdf").get_page(3) is None


def test_cancel_flag_stops_before_the_next_page():
    cancel = threading.Event()

    def generate_then_cancel(*args, **kwargs):
        cancel.set()
        return [flashcard(front="F", back="B")]

    result, generate = run([1, 2, 3], llm_answers=generate_then_cancel, cancel=cancel)

    assert result.status == "cancelled"
    assert generate.call_count == 1
    assert CardStorage("x.pdf").get_page(1) is not None
