"""The whole path of a card into Anki: markdown -> HTML -> media files -> note.

Cards are generated and stored as markdown; Anki only ever receives HTML derived
from it. The messages in the returned ActionResults are German on purpose — the
frontend shows them to the user as they are.
"""

import base64
import json
import platform
import re
import subprocess
import time
from pathlib import Path

import markdown
import requests

from backend.config import get_settings
from backend.schemas import ActionResult, flashcard

# =========================================================
# Constants
# =========================================================

_md = markdown.Markdown(
    extensions=[
        'extra',
        'sane_lists',
        'codehilite',
        'pymdownx.arithmatex',
        # superfences also recognizes ``` inside lists / indented;
        # the standard fenced_code from 'extra' swallows those cases.
        'pymdownx.superfences',
    ],
    extension_configs={
        'pymdownx.arithmatex': {
            # 'generic' emits LaTeX as \(...\) / \[...\] —
            # exactly the format Anki's MathJax renderer expects.
            'generic': True,
            'preview': False,
        },
    },
)

# Start of a list: optional indent, then "1." / "1)" / "- " / "* " / "+ "
_LIST_START = re.compile(
    r'^(?P<prev>[^\n]*\S[^\n]*)\n'
    r'(?P<item>[ \t]*(?:\d+[.)]|[-*+])[ \t]+\S)',
    re.MULTILINE,
)

# Matches <img> tags with a grafik:<idx> pseudo URL in the rendered HTML.
_GRAPHIC_IMG = re.compile(r'<img[^>]*\bsrc="grafik:(?P<idx>\d+)"[^>]*/?>')


# =========================================================
# Private helpers
# =========================================================


def _normalize_latex(text: str) -> str:
    # arithmatex only treats $$...$$ as block math when there are blank lines
    # around it. LLMs often put the formula directly below the text, so force
    # the blank lines.
    text = re.sub(r'(?<!\n)\n(\$\$)', r'\n\n\1', text)
    text = re.sub(r'(\$\$)\n(?!\n)', r'\1\n\n', text)
    return text


def _normalize_lists(text: str) -> str:
    # Markdown requires a blank line before a list, otherwise it is rendered as
    # running text. LLMs often omit it; insert it here.
    prev = None
    while prev != text:
        prev = text
        text = _LIST_START.sub(lambda m: f"{m.group('prev')}\n\n{m.group('item')}", text)
    return text


def _close_unbalanced_fences(text: str) -> str:
    # LLMs like to forget the closing ``` fence. An odd number of fence markers
    # -> add a closing one.
    if len(re.findall(r'(?m)^[ \t]{0,3}```', text)) % 2 == 1:
        text = text.rstrip() + '\n```\n'
    return text


def _slug(pdf_name: str) -> str:
    """Strip whitespace/special characters — for media file names and tags."""
    return re.sub(r"\W+", "_", Path(pdf_name).stem)


def _resolve_graphic_refs(html: str, pdf_name: str, page: int, graphics: list[bytes]) -> str:
    """Replaces every grafik:<idx> reference in the HTML with a real Anki media file.

    Cards reference slide graphics inline in the markdown, e.g.
    ![Iceberg-Architektur](grafik:0). As ordinary markdown image syntax that
    renders to <img src="grafik:0"> — a pseudo URL Anki cannot display. So the
    graphic is stored as a media file under a deterministic name (PDF + page +
    index, a repeated export overwrites it instead of duplicating) and the src is
    rewritten to that name. A reference to an index that does not exist (LLM
    hallucination) drops the whole <img> tag — better without the graphic than a
    broken card.
    """
    pdf_stem = _slug(pdf_name)

    def replace(match: re.Match) -> str:
        idx = int(match.group("idx"))
        if idx >= len(graphics):
            return ""  # reference to a non-existent graphic -> drop the tag
        filename = f"ankiagent_{pdf_stem}_{page}_{idx}.jpg"
        _request("storeMediaFile", filename=filename, data=base64.b64encode(graphics[idx]).decode())
        return match.group(0).replace(f"grafik:{idx}", filename)

    return _GRAPHIC_IMG.sub(replace, html)


def _request(action: str, **params) -> object:
    """Sends an AnkiConnect request and returns its result (raises on error)."""
    server = get_settings().anki_server
    response = requests.post(server, json={
        "action": action,
        "version": 6,
        "params": params,
    })
    result = json.loads(response.content)
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result["result"]


# =========================================================
# Public
# =========================================================


def markdown_to_anki_html(text: str) -> str:
    """Renders card markdown to the HTML Anki expects (MathJax, code, lists)."""
    text = _normalize_latex(text)
    text = _normalize_lists(text)
    text = _close_unbalanced_fences(text)
    _md.reset()
    return _md.convert(text)


def is_anki_running() -> bool:
    """Whether AnkiConnect answers — everything else degrades gracefully on this."""
    url = get_settings().anki_server
    try:
        requests.get(url, timeout=3)
        return True
    except requests.RequestException:
        return False


def start_anki(wait_seconds: float = 20.0) -> ActionResult:
    """Starts the Anki desktop app (platform dependent) and waits for AnkiConnect."""
    if is_anki_running():
        return ActionResult(ok=True, message="Anki läuft bereits.")

    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", "-a", "Anki"])
        elif system == "Windows":
            # Anki has to be on the PATH or findable via the app paths.
            subprocess.Popen("anki", shell=True)
        else:
            subprocess.Popen(["anki"])
    except OSError as exc:
        return ActionResult(ok=False, message=f"Anki konnte nicht gestartet werden: {exc}")

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if is_anki_running():
            return ActionResult(ok=True, message="Anki wurde gestartet.")
        time.sleep(1)

    return ActionResult(
        ok=False,
        message=(
            "Anki wurde gestartet, aber AnkiConnect antwortet noch nicht. "
            "Bitte einen Moment warten und erneut versuchen."
        ),
    )


def get_decks() -> list[str]:
    """The names of all decks in Anki."""
    return _request("deckNames")


def create_deck(name: str) -> ActionResult:
    """Creates a new Anki deck."""
    if not is_anki_running():
        return ActionResult(
            ok=False,
            message="Anki läuft nicht – Deck konnte nicht erstellt werden.",
        )

    try:
        _request("createDeck", deck=name)
    except (requests.RequestException, RuntimeError) as exc:
        return ActionResult(ok=False, message=f"Deck konnte nicht erstellt werden: {exc}")

    return ActionResult(ok=True, message=f"Deck '{name}' erstellt.")


def export_card(
    deck: str,
    card: flashcard,
    page: int,
    *,
    pdf_name: str = "",
    graphics: list[bytes] | None = None,
    generated_at: str = "",
) -> ActionResult:
    """Exports a card to Anki, including server check and error handling.

    graphics: the slide's graphic JPGs (index == grafik:<idx> reference);
    referenced graphics end up as media files in Anki (_resolve_graphic_refs).

    generated_at: start time of the run (ISO) from the card store — it becomes
    the timestamp tag that all cards of one run share.
    """
    if not is_anki_running():
        return ActionResult(
            ok=False,
            message="Anki läuft nicht – bitte Anki mit AnkiConnect-Plugin starten.",
        )

    # Tags make the card attributable to its source and its run in Anki.
    # Colons are Anki's tag hierarchy -> replace them in the timestamp.
    tags = [f"folie_{page}"]
    if pdf_name:
        tags.append(f"pdf_{_slug(pdf_name)}")
    if generated_at:
        tags.append(f"generiert_{generated_at.replace('T', '_').replace(':', '-')}")

    try:
        front = _resolve_graphic_refs(
            markdown_to_anki_html(card.front), pdf_name, page, graphics or [])
        back = _resolve_graphic_refs(
            markdown_to_anki_html(card.back), pdf_name, page, graphics or [])
        _request("createDeck", deck=deck)
        note_id = _request("addNote", note={
            "deckName": deck,
            "modelName": "Basic",
            "fields": {"Front": front, "Back": back},
            "tags": tags,
        })
    except (requests.RequestException, RuntimeError) as exc:
        return ActionResult(ok=False, message=f"Export fehlgeschlagen: {exc}")

    return ActionResult(ok=True, message=f"Karte nach '{deck}' exportiert.", note_id=note_id)


def delete_card(note_id: int) -> ActionResult:
    """Deletes a previously exported card from Anki by note ID."""
    if not is_anki_running():
        return ActionResult(
            ok=False,
            message="Anki läuft nicht – Karte konnte nicht gelöscht werden.",
        )

    try:
        _request("deleteNotes", notes=[note_id])
    except (requests.RequestException, RuntimeError) as exc:
        return ActionResult(ok=False, message=f"Löschen fehlgeschlagen: {exc}")

    return ActionResult(ok=True, message="Karte aus Anki gelöscht.")
