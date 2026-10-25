"""The modal dialogs of the UI.

No side effects at module level: Streamlit re-runs the entry point on every
interaction, but not the modules it imports.
"""

import streamlit as st

from api import (
    activate_model,
    create_deck,
    generate_cards,
    get_available_pdfs,
    get_decks,
    get_page_graphics,
    get_pending,
    list_models,
    upload_pdfs,
)


PROVIDER_LABEL = {
    "cloud": "Cloud Provider",
    "llamacpp": "llama.cpp",
    "mlx": "MLX",
}


# =========================================================
# Shared helpers
# =========================================================


def close_editor():
    """Closes an open card editor.

    When switching slide or PDF there is nothing to discard but the editor — the
    cards come fresh from the card store anyway.
    """
    st.session_state.editing_index = None


# =========================================================
# Dialogs (modal windows in front of the UI)
# =========================================================
@st.dialog("PDF verwalten")
def pdf_dialog():
    """Selects the active PDF or uploads new ones."""

    pdfs = get_available_pdfs()

    st.markdown("**PDF auswählen**")

    if pdfs:

        choice = st.radio(
            "Verfügbare PDFs",
            pdfs,
            index=pdfs.index(st.session_state.pdf_name)
            if st.session_state.pdf_name in pdfs else 0,
            label_visibility="collapsed",
        )

        if st.button("Auswählen", type="primary"):

            if choice != st.session_state.pdf_name:
                st.session_state.pdf_name = choice
                st.session_state.current_page = 1
                close_editor()

            st.rerun()

    else:

        st.info("Noch keine PDFs vorhanden – lade unten eine hoch.")

    st.divider()

    st.markdown("**Neue PDF hochladen**")

    files = st.file_uploader(
        "PDF-Datei(en)",
        type="pdf",
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    if st.button("Hochladen", disabled=not files):

        with st.spinner("Lade hoch ..."):
            upload_pdfs(files)

        # A PDF of the same name is overwritten — the cached graphics then
        # belong to the old file.
        get_page_graphics.clear()

        # Make the freshly uploaded PDF the active one, then close the dialog.
        st.session_state.pdf_name = files[-1].name
        st.session_state.current_page = 1
        close_editor()

        st.rerun()


@st.dialog("Anki-Deck verwalten")
def deck_dialog():
    """Selects the Anki deck cards are exported to, or creates a new one."""

    decks = get_decks()

    st.markdown("**Deck auswählen**")

    if decks:

        choice = st.radio(
            "Verfügbare Decks",
            decks,
            index=decks.index(st.session_state.deck_name)
            if st.session_state.deck_name in decks else 0,
            label_visibility="collapsed",
        )

        if st.button("Auswählen", type="primary"):
            st.session_state.deck_name = choice
            st.rerun()

    else:

        st.info("Keine Decks gefunden – Anki starten oder unten ein neues anlegen.")

    st.divider()

    st.markdown("**Neues Deck anlegen**")

    new_deck = st.text_input(
        "Deckname",
        placeholder="z.B. Vorlesung 4",
        label_visibility="collapsed",
    )

    if st.button("Deck erstellen", disabled=not new_deck):

        result = create_deck(new_deck)

        if result["ok"]:
            st.session_state.deck_name = new_deck
            st.rerun()
        else:
            st.error(result["message"])


@st.dialog("Modell wählen")
def model_dialog(config: dict):
    """Lists the models of a provider and activates one."""

    st.caption(f"Aktiv: {PROVIDER_LABEL[config['provider']]} · {config['model'] or '–'}")

    provider = st.radio(
        "Anbieter",
        list(PROVIDER_LABEL),
        index=list(PROVIDER_LABEL).index(config["provider"]),
        format_func=PROVIDER_LABEL.get,
        horizontal=True,
    )

    base_url = api_key = None

    if provider == "cloud":
        base_url = st.text_input("URL", value=config["cloud_base_url"])
        # Empty keeps the stored key — the backend never sends it back.
        api_key = st.text_input(
            "API-Key", type="password", placeholder="leer = gespeicherten Key behalten"
        ) or None
    else:
        st.caption("Den Server startet die App selbst, sobald er gebraucht wird.")

    if st.button("Verfügbare Modelle anzeigen"):

        with st.spinner("Frage Modelle ab ..."):
            try:
                st.session_state.model_list = (provider, list_models(provider, base_url, api_key))
            except RuntimeError as exc:
                st.error(str(exc))

    listed = st.session_state.get("model_list")

    # The list belongs to the provider it was fetched for.
    if not listed or listed[0] != provider:
        return

    models = listed[1]

    if not models:
        st.info("Keine Modelle gefunden.")
        return

    choice = st.radio(
        "Modelle",
        models,
        index=models.index(config["model"]) if config["model"] in models else 0,
    )

    if st.button("Aktivieren", type="primary"):

        with st.spinner("Aktiviere ... (ein lokales Modell zu laden dauert etwas)"):
            try:
                activate_model(provider, choice, base_url, api_key)
            except RuntimeError as exc:
                st.error(str(exc))
                return

        # The fine-tuned model knows only the graphics-less contract.
        if provider == "mlx":
            st.session_state.graphics_enabled = False

        del st.session_state.model_list

        st.rerun()


@st.dialog("Alle Folien generieren")
def batch_dialog():
    """Generates the cards of every slide that has none yet, one after another.

    Each slide goes through the same /generate as a single slide and is stored
    right away, so a new start picks up the slides that are still open. The loop
    cannot be cancelled: a dialog is a Streamlit fragment, and neither a click
    nor Streamlit's stop button interrupts a running fragment.
    """
    pdf_name = st.session_state.pdf_name
    graphics = st.session_state.graphics_enabled
    pending = get_pending(pdf_name)
    pages = pending["pages"]

    st.markdown(
        f"**PDF:** {pdf_name}  \n"
        f"**Grafik-Modus:** {'mit Grafiken' if graphics else 'ohne Grafiken'}  \n"
        f"**Offene Folien:** {len(pages)} von {pending['total']}  \n"
        f"**Anfragen ans LLM:** {len(pages)}"
    )

    if not st.button("🚀 Lauf starten", type="primary", disabled=not pages):
        return

    progress = st.progress(0.0)
    for done, page in enumerate(pages):
        progress.progress(done / len(pages), text=f"Folie {page} ({done} von {len(pages)} erzeugt)")
        try:
            generate_cards(pdf_name, page, graphics)
        except Exception as exc:
            # Stop at the first error (a missing key or an exhausted quota would
            # hit every further slide too); a new start continues from here.
            st.error(f"Folie {page} fehlgeschlagen: {exc}")
            st.caption(f"{done} Folien wurden gesichert. Ein neuer Start setzt dort fort.")
            return

    # A toast survives the rerun, which closes the dialog and shows the cards.
    st.toast(f"{len(pages)} Folien erzeugt.")
    st.rerun()
