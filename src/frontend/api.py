"""The complete backend contract: every HTTP call the frontend makes.

The frontend imports nothing from the backend — this file is the whole seam
between them. It is read as a list, so the wrappers carry no docstrings; where
an endpoint is not obvious from its name, a comment sits next to it.
"""

import os

import requests
import streamlit as st

API_BASE = os.environ.get("ANKI_AGENT_API", "http://127.0.0.1:8000")


# =========================================================
# Transport
# =========================================================
# Generous timeouts: a generation request waits for the LLM.


def api_get(path: str, **kwargs):
    return requests.get(f"{API_BASE}{path}", timeout=120, **kwargs)


def api_post(path: str, **kwargs):
    return requests.post(f"{API_BASE}{path}", timeout=300, **kwargs)


def api_put(path: str, **kwargs):
    return requests.put(f"{API_BASE}{path}", timeout=120, **kwargs)


# =========================================================
# Configuration, LLM providers, PDFs and decks
# =========================================================


# {"graphics_enabled", "provider", "model", "ready", "cloud_base_url"};
# ready = False while the active local server is not running.
def get_config() -> dict:
    return api_get("/config").json()


# Raises RuntimeError with the backend's message if the provider is unreachable.
def list_models(provider: str, base_url: str | None, api_key: str | None) -> list[str]:
    resp = api_post(
        "/providers/models",
        json={"provider": provider, "base_url": base_url, "api_key": api_key},
    )
    if not resp.ok:
        raise RuntimeError(resp.json()["detail"])
    return resp.json()["models"]


# Starts a local server if needed (mlx: up to a few minutes). Raises
# RuntimeError on 409 (batch run going) or an unreachable provider.
def activate_model(provider: str, model: str, base_url: str | None, api_key: str | None) -> None:
    resp = api_post(
        "/providers/activate",
        json={"provider": provider, "model": model, "base_url": base_url, "api_key": api_key},
    )
    if not resp.ok:
        raise RuntimeError(resp.json()["detail"])


def get_available_pdfs() -> list[str]:
    return api_get("/pdfs").json()["pdfs"]


# Empty while Anki is not running — the backend degrades gracefully.
def get_decks() -> list[str]:
    return api_get("/decks").json()["decks"]


def create_deck(name: str) -> dict:
    return api_post("/decks", json={"name": name}).json()


def anki_running() -> bool:
    return api_get("/anki/status").json()["running"]


def start_anki() -> dict:
    return api_post("/anki/start").json()


def upload_pdfs(files) -> None:
    api_post("/pdfs", files=[("files", (f.name, f.getbuffer(), "application/pdf")) for f in files])


# =========================================================
# Slides and cards
# =========================================================


def get_page_count(name: str) -> int:
    return api_get(f"/pdfs/{name}/page-count").json()["count"]


def get_image(name: str, page: int) -> bytes:
    return api_get(f"/pdfs/{name}/pages/{page}/image").content


# The slide's card store: {"generated": bool, "graphics": bool, "cards": [...]};
# "generated" tells "never ran" from "ran, produced nothing".
def get_page_cards(name: str, page: int) -> dict:
    return api_get(f"/pdfs/{name}/pages/{page}/cards").json()


@st.cache_data(show_spinner=False)
def get_page_graphics(name: str, page: int) -> list[str]:
    # Graphics are re-extracted deterministically on the server, so one slide
    # always yields the same result -> fetching once per slide is enough.
    return api_get(f"/pdfs/{name}/pages/{page}/graphics").json()["graphics"]


# Only "unreviewed" or "rejected" — "accepted" is reachable through export_card.
def set_card_status(name: str, page: int, index: int, status: str) -> None:
    api_post(f"/pdfs/{name}/pages/{page}/cards/{index}/status", json={"status": status})


def set_card_content(name: str, page: int, index: int, front: str, back: str) -> None:
    api_put(f"/pdfs/{name}/pages/{page}/cards/{index}", json={"front": front, "back": back})


# =========================================================
# Generation
# =========================================================


# The cards are read back from the card store afterwards, not from the response.
def generate_cards(pdf_name: str, page: int, graphics: bool, feedback: str | None = None) -> None:
    resp = api_post(
        "/generate",
        json={"pdf_name": pdf_name, "page": page, "feedback": feedback, "graphics": graphics},
    )
    # 400 = no API key configured; 409 = cards of this slide are already in
    # Anki (regenerating is blocked).
    if resp.status_code in (400, 409):
        raise RuntimeError(resp.json()["detail"])
    resp.raise_for_status()


# Open slides of the PDF: {"pending": int, "total": int} — pending == requests.
def get_pending(name: str) -> dict:
    return api_get(f"/pdfs/{name}/pending").json()


# Returns the rejection message if a run is already going, otherwise None.
def start_batch(pdf_name: str, graphics: bool) -> str | None:
    resp = api_post("/generate/batch", json={"pdf_name": pdf_name, "graphics": graphics})
    # 409 = a run is already going; anything else is a real error.
    if resp.status_code == 409:
        return resp.json()["detail"]
    resp.raise_for_status()
    return None


# None while no run has happened since the backend started.
def get_batch_status() -> dict | None:
    return api_get("/generate/batch/status").json()


def cancel_batch() -> None:
    api_post("/generate/batch/cancel")


# =========================================================
# Export to Anki
# =========================================================


# index = position of the card in the card store; the backend refuses a card
# that is not in it.
def export_card(deck: str, card: dict, page: int, pdf_name: str, index: int) -> dict:
    resp = api_post("/export", json={
        "deck": deck,
        "card": {"front": card["front"], "back": card["back"]},
        "page": page, "pdf_name": pdf_name, "index": index,
    })
    return resp.json()


# Deletes the note in Anki again and puts the card back to unreviewed.
def undo_export(pdf_name: str, page: int, index: int) -> dict:
    return api_post(
        "/export/undo", json={"pdf_name": pdf_name, "page": page, "index": index}
    ).json()
