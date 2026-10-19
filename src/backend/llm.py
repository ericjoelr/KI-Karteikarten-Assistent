"""The connection to the LLM — the one place that knows a provider.

`generate_cards` also builds the request messages, because slide image and
system prompt together are the frozen input contract (ADR-0003/ADR-0004). A
second provider (Phase 4) becomes a second function in this file.
"""

import base64

from openai import OpenAI

from backend.config import get_settings
from backend.schemas import card_deck, flashcard


def generate_cards(
    image: bytes,
    *,
    model: str | None = None,
    graphics: bool = True,
    feedback: str | None = None,
) -> list[flashcard]:
    """Has the model create flashcards from a slide image.

    The card schema goes to the provider as `response_format`, not into the
    prompt: both providers turn it into a sampling constraint, so an answer
    wrapped in a markdown code block cannot arise (ADR-0007). One call is
    exactly one provider request.

    graphics: graphics mode; off means a system prompt without the GRAFIKEN
        section (ADR-0003).
    feedback: optional HITL hint that goes into the generation.
    """
    settings = get_settings()

    client = OpenAI(
        base_url=settings.provider_base_url,
        api_key=settings.provider_api_key,
    )

    image_uri = f"data:image/jpeg;base64,{base64.b64encode(image).decode()}"
    messages: list[dict] = [
        {"role": "system", "content": settings.system_prompt(graphics=graphics)},
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": image_uri}}],
        },
    ]

    if feedback:
        # German like the system prompt: this text goes to the model.
        messages.append({
            "role": "user",
            "content": f"Berücksichtige folgendes Feedback bei der Erstellung: {feedback}",
        })

    response = client.chat.completions.create(
        model=model or settings.default_model,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "card_deck",
                # $defs/$ref stay in — both providers accept the schema as it is.
                "schema": card_deck.model_json_schema(),
                "strict": True,
            },
        },
    )

    return card_deck.model_validate_json(response.choices[0].message.content).cards
