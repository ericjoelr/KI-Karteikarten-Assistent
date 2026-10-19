import base64
from unittest.mock import MagicMock, patch

import pytest

from backend import llm
from backend.config import get_settings
from backend.schemas import card_deck, flashcard


def call(answer: card_deck | None = None, **kwargs):
    """Runs generate_cards against a doubled OpenAI client.

    Returns the cards and the kwargs the client was called with.
    """
    if answer is None:
        answer = card_deck(cards=[flashcard(front="F", back="B")])
    response = MagicMock()
    response.choices[0].message.content = answer.model_dump_json()

    with patch("backend.llm.OpenAI") as openai:
        create = openai.return_value.chat.completions.create
        create.return_value = response
        cards = llm.generate_cards(b"jpgdata", **kwargs)
    return cards, create


# =========================================================
# What goes to the provider
# =========================================================
def test_request_forces_the_card_deck_schema():
    _, create = call()

    fmt = create.call_args.kwargs["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["schema"] == card_deck.model_json_schema()


def test_system_prompt_is_exactly_the_configured_one():
    _, create = call()

    # Regression guard: nothing may be appended to the German prompt behind our
    # back (that is what instructor used to do with its English schema block).
    system = create.call_args.kwargs["messages"][0]
    assert system["role"] == "system"
    assert system["content"] == get_settings().system_prompt(graphics=True)


@pytest.mark.parametrize("graphics", [True, False])
def test_graphics_mode_decides_the_system_prompt(graphics):
    _, create = call(graphics=graphics)

    system = create.call_args.kwargs["messages"][0]["content"]
    assert system == get_settings().system_prompt(graphics=graphics)
    assert ("# Grafiken:" in system) is graphics


def test_slide_image_goes_along_as_a_data_uri():
    _, create = call()

    content = create.call_args.kwargs["messages"][1]["content"]
    uri = content[0]["image_url"]["url"]
    assert uri == "data:image/jpeg;base64," + base64.b64encode(b"jpgdata").decode()


def test_feedback_becomes_an_extra_message():
    _, create = call(feedback="Kürzer bitte")

    messages = create.call_args.kwargs["messages"]
    assert len(messages) == 3
    assert "Kürzer bitte" in messages[-1]["content"]


def test_without_feedback_there_is_no_extra_message():
    _, create = call()

    assert len(create.call_args.kwargs["messages"]) == 2


def test_one_call_is_exactly_one_provider_request():
    _, create = call()

    assert create.call_count == 1


# =========================================================
# What comes back
# =========================================================
def test_a_well_formed_answer_becomes_flashcards():
    cards, _ = call(card_deck(cards=[flashcard(front="Frage", back="Antwort")]))

    assert cards == [flashcard(front="Frage", back="Antwort")]


def test_an_empty_card_list_is_an_empty_list_and_no_error():
    cards, _ = call(card_deck(cards=[]))

    assert cards == []


def test_invalid_json_raises():
    response = MagicMock()
    response.choices[0].message.content = "not json"

    with patch("backend.llm.OpenAI") as openai:
        openai.return_value.chat.completions.create.return_value = response
        with pytest.raises(Exception):
            llm.generate_cards(b"jpgdata")
