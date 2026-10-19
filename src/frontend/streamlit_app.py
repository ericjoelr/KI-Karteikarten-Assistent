"""Entry point of the Streamlit frontend: page setup, session state, screen.

Exception to the module layout: the file is ordered the way the screen is built
from top to bottom, because Streamlit executes it as a script in exactly that
order on every interaction.
"""

import re

import streamlit as st
import streamlit.components.v1 as components

from api import (
    anki_running,
    export_card,
    generate_cards,
    get_available_pdfs,
    get_config,
    get_decks,
    get_image,
    get_page_cards,
    get_page_count,
    get_page_graphics,
    set_card_content,
    set_card_status,
    start_anki,
    undo_export,
)
from dialogs import PROVIDER_LABEL, batch_dialog, close_editor, deck_dialog, model_dialog, pdf_dialog


# =========================================================
# Page setup
# =========================================================
st.set_page_config(
    page_title="Karteikarten Agent",
    layout="wide"
)

st.markdown("""
    <style>

        .block-container {
            padding-top: 1.2rem;
            padding-bottom: 0rem;
        }

        h1 {
            margin-top: -1rem;
            margin-bottom: 0.5rem;
        }

        .stButton button {
            width: 100%;
        }

        textarea {
            font-size: 0.95rem !important;
        }

        /* Hide the Streamlit footer */
        [data-testid="stBottomBlockContainer"] {
            display: none !important;
        }

        /* Prevent global overflow */
        .main > div {
            overflow: hidden !important;
        }

    </style>
""", unsafe_allow_html=True)


# =========================================================
# Load data
# =========================================================
available_pdfs = get_available_pdfs()
available_decks = get_decks()
# Active provider and model — fetched on every rerun, so the label stays current.
config = get_config()

if not available_pdfs:
    st.warning("Keine PDFs gefunden. Lade über „📄 PDF auswählen / hochladen“ eine PDF hoch.")


# =========================================================
# Session state
# =========================================================
# View state only — the cards themselves live in the backend's card store.
if 'current_page' not in st.session_state:
    st.session_state.current_page = 1

if 'pdf_name' not in st.session_state:
    st.session_state.pdf_name = available_pdfs[0] if available_pdfs else None

if 'deck_name' not in st.session_state:
    st.session_state.deck_name = available_decks[0] if available_decks else None

if 'editing_index' not in st.session_state:
    st.session_state.editing_index = None

if 'graphics_enabled' not in st.session_state:
    # Graphics mode; the initial value comes from the backend configuration.
    st.session_state.graphics_enabled = config["graphics_enabled"]


# =========================================================
# Helpers
# =========================================================
def normalize_latex(text: str):
    """Turns Anki's MathJax delimiters back into the $-syntax st.markdown renders."""
    return (
        text
        .replace("\\(", "$")
        .replace("\\)", "$")
        .replace("\\[", "$$")
        .replace("\\]", "$$")
    )


def resolve_graphic_refs(text: str, graphics: list[str]) -> str:
    """Replaces every grafik:<idx> reference in the card markdown with a data URI.

    Preview counterpart to embedding graphics on export (src/backend/anki.py):
    there the reference becomes an Anki media file, here a data URI from the
    graphics route. References to non-existent indices are removed either way.
    """
    def replace(match: re.Match) -> str:
        idx = int(match.group("idx"))
        if idx >= len(graphics):
            return ""
        return match.group(0).replace(
            f"grafik:{idx}", f"data:image/jpeg;base64,{graphics[idx]}"
        )

    return re.sub(r'!\[[^\]]*\]\(grafik:(?P<idx>\d+)\)', replace, text)


STATUS_LABEL = {
    "unreviewed": "⚪ ungeprüft",
    "accepted": "✅ akzeptiert",
    "rejected": "🚫 verworfen",
}


# =========================================================
# Main UI
# =========================================================
st.title("Karteikarten Agent")


# =========================================================
# Anki status
# =========================================================
if not anki_running():

    status_col, btn_col = st.columns([4, 1])

    status_col.warning(
        "Anki ist nicht verfügbar – der Export bleibt deaktiviert, bis Anki läuft."
    )

    if btn_col.button("▶️ Anki starten"):

        with st.spinner("Starte Anki ..."):
            result = start_anki()

        if result["ok"]:
            st.rerun()
        else:
            st.error(result["message"])


# =========================================================
# Settings bar
# =========================================================
set_col1, set_col2, set_col3, set_col4, set_col5 = st.columns([3, 3, 3, 2, 3])

with set_col1:

    pdf_label = (
        f"📄 {st.session_state.pdf_name}"
        if st.session_state.pdf_name else "📄 PDF auswählen / hochladen"
    )

    if st.button(pdf_label):
        pdf_dialog()

with set_col2:

    deck_label = (
        f"🗂️ {st.session_state.deck_name}"
        if st.session_state.deck_name else "🗂️ Deck auswählen / anlegen"
    )

    if st.button(deck_label):
        deck_dialog()

with set_col3:

    # A local server that is not running (e.g. after a backend restart) keeps
    # its selection, but has to be activated again.
    model_label = (
        f"{PROVIDER_LABEL[config['provider']]} · {config['model']}"
        if config["model"] else "🤖 Modell auswählen"
    )

    if st.button(model_label if config["ready"] else f"⚠️ {model_label} (Server aus)"):
        model_dialog(config)

with set_col4:

    st.session_state.graphics_enabled = st.toggle(
        "🖼️ Grafiken",
        value=st.session_state.graphics_enabled,
        help="Grafiken aus der Folie in die Karten einbinden. Aus: keine Abbildungen, die Folie wird nicht nach Grafiken durchsucht.",
    )

with set_col5:

    if st.button("🚀 Alle Folien generieren", disabled=not st.session_state.pdf_name):
        batch_dialog()


st.divider()

if not st.session_state.pdf_name:
    st.stop()


# =========================================================
# Main layout
# =========================================================
col_img, col_review = st.columns(
    [1, 1],
    gap="small"
)

max_pages = get_page_count(
    st.session_state.pdf_name
)


# =========================================================
# Left side: PDF viewer
# =========================================================
with col_img:

    st.subheader("Folien Auswahl")

    image_data = get_image(
        st.session_state.pdf_name,
        st.session_state.current_page
    )

    if image_data:

        st.image(
            image_data,
            width="stretch"
        )

    else:

        st.error(
            "Bild konnte nicht geladen werden."
        )

    nav_col1, nav_col2 = st.columns(
        [1, 1],
        gap="xxsmall"
    )

    # PREVIOUS
    if nav_col1.button(
        "⬅️ Vorherige",
        disabled=(
            st.session_state.current_page <= 1
        )
    ):

        st.session_state.current_page -= 1
        close_editor()

        st.rerun()

    # NEXT
    if nav_col2.button(
        "Nächste ➡️",
        disabled=(
            st.session_state.current_page >= max_pages
        )
    ):

        st.session_state.current_page += 1
        close_editor()

        st.rerun()


# =========================================================
# Right side: review
# =========================================================
with col_review:

    st.subheader("Review")

    # Cards and graphics come fresh from the backend's card store on every
    # rerun — the session state no longer holds them.
    entry = get_page_cards(
        st.session_state.pdf_name,
        st.session_state.current_page,
    )

    slide_cards = entry["cards"]

    # Graphics only if the slide was generated in graphics mode — just as on
    # export, otherwise the preview shows something else than Anki gets.
    slide_graphics = (
        get_page_graphics(st.session_state.pdf_name, st.session_state.current_page)
        if entry["graphics"] else []
    )

    exported = [i for i, c in enumerate(slide_cards) if c["note_id"] is not None]
    unreviewed = [i for i, c in enumerate(slide_cards) if c["status"] == "unreviewed"]

    def render_preview(front: str, back: str):
        """Renders both sides of a card the way Anki will show them."""
        with st.container(border=True):

            st.markdown("#### Vorderseite")

            st.markdown(
                normalize_latex(resolve_graphic_refs(front, slide_graphics)),
                unsafe_allow_html=False
            )

            st.divider()

            st.markdown("#### Rückseite")

            st.markdown(
                normalize_latex(resolve_graphic_refs(back, slide_graphics)),
                unsafe_allow_html=False
            )

    # =====================================================
    # Slide never generated
    # =====================================================
    if not entry["generated"]:

        st.write(" ")

        if st.button(
            "Karten generieren",
            type="primary"
        ):

            with st.spinner(
                "KI analysiert Folie und erstellt Karten..."
            ):

                generate_cards(
                    st.session_state.pdf_name,
                    st.session_state.current_page,
                    st.session_state.graphics_enabled,
                )

            st.rerun()

    # =====================================================
    # Review cards
    # =====================================================
    else:

        # -------------------------------------------------
        # HITL: regenerate the whole slide with feedback
        # -------------------------------------------------
        with st.expander("🔄 Folie mit Feedback neu generieren"):

            # Generating again replaces the slide's cards — whatever is already
            # in Anki would be left as an orphaned note (the backend refuses it).
            if exported:
                st.info(
                    "Von dieser Folie liegen bereits Karten in Anki. "
                    "Bitte den Export erst rückgängig machen."
                )

            slide_feedback = st.text_input(
                "Feedback ans LLM",
                placeholder="z.B. kürzer, präziser, mathematischer ...",
                key="slide_feedback",
            )

            if st.button("Neu generieren", key="regen_slide", disabled=bool(exported)):

                with st.spinner("LLM überarbeitet die Karten..."):

                    try:
                        generate_cards(
                            st.session_state.pdf_name,
                            st.session_state.current_page,
                            st.session_state.graphics_enabled,
                            feedback=slide_feedback or None,
                        )
                    except RuntimeError as exc:
                        st.error(str(exc))
                        st.stop()

                st.session_state.editing_index = None

                st.rerun()

        if not slide_cards:
            st.info("Für diese Folie hat das LLM keine Karten erzeugt.")

        # -------------------------------------------------
        # Accept all unreviewed
        # -------------------------------------------------
        if unreviewed:

            if st.button(
                f"✅ Alle {len(unreviewed)} ungeprüften Karten akzeptieren",
                key="accept_all",
            ):

                with st.spinner("Exportiere nach Anki ..."):

                    for i in unreviewed:

                        result = export_card(
                            deck=st.session_state.deck_name,
                            card=slide_cards[i],
                            page=st.session_state.current_page,
                            pdf_name=st.session_state.pdf_name,
                            index=i,
                        )

                        # Toast instead of st.error: it survives the rerun below.
                        if not result["ok"]:
                            st.toast(f"Karte {i+1}: {result['message']}", icon="⚠️")

                st.rerun()

        with st.container(height=800):  # JS overrides this dynamically

            for i, card in enumerate(slide_cards):

                with st.expander(
                    f"Karte {i+1} — {STATUS_LABEL[card['status']]}",
                    expanded=card["status"] == "unreviewed",
                ):

                    # -----------------------------------------
                    # Normal view
                    # -----------------------------------------
                    if st.session_state.editing_index != i:

                        render_preview(card["front"], card["back"])

                        st.write("")

                        # ACCEPTED: undo only
                        if card["status"] == "accepted":

                            if st.button(
                                "↩️ Rückgängig",
                                key=f"undo_{i}"
                            ):

                                result = undo_export(
                                    st.session_state.pdf_name,
                                    st.session_state.current_page,
                                    i,
                                )

                                if result["ok"]:
                                    st.rerun()
                                else:
                                    st.error(result["message"])

                        # REJECTED: stays in the store, can be brought back
                        elif card["status"] == "rejected":

                            if st.button(
                                "↩️ Zurück zu ungeprüft",
                                key=f"unrej_{i}"
                            ):

                                set_card_status(
                                    st.session_state.pdf_name,
                                    st.session_state.current_page,
                                    i,
                                    "unreviewed",
                                )

                                st.rerun()

                        # UNREVIEWED
                        else:

                            c1, c2, c3 = st.columns([1, 1, 1])

                            # ACCEPT (= export to Anki)
                            if c1.button(
                                "✅ Akzeptieren",
                                key=f"exp_{i}"
                            ):

                                result = export_card(
                                    deck=st.session_state.deck_name,
                                    card=card,
                                    page=st.session_state.current_page,
                                    pdf_name=st.session_state.pdf_name,
                                    index=i,
                                )

                                if result["ok"]:
                                    st.rerun()
                                else:
                                    st.error(result["message"])

                            # EDIT
                            if c2.button(
                                "✏️ Überarbeiten",
                                key=f"reg_{i}"
                            ):

                                st.session_state.editing_index = i

                                st.rerun()

                            # REJECT
                            if c3.button(
                                "🚫 Verwerfen",
                                key=f"rej_{i}"
                            ):

                                set_card_status(
                                    st.session_state.pdf_name,
                                    st.session_state.current_page,
                                    i,
                                    "rejected",
                                )

                                st.rerun()

                    # -----------------------------------------
                    # Inline editor
                    # -----------------------------------------
                    else:

                        with st.container(border=True):

                            # FRONT
                            st.markdown("#### Vorderseite")

                            col_ef, col_pf = st.columns(2, gap="large")

                            with col_ef:
                                current_front = st.text_area(
                                    "✏️ Bearbeitung",
                                    value=card["front"],
                                    height=150,
                                    key=f"front_edit_{i}"
                                )

                            with col_pf:
                                st.markdown("👀 Vorschau")
                                with st.container(border=True, height=150):
                                    st.markdown(
                                        normalize_latex(
                                            resolve_graphic_refs(current_front, slide_graphics)
                                        ),
                                        unsafe_allow_html=False
                                    )

                            st.divider()

                            # BACK
                            st.markdown("#### Rückseite")

                            col_eb, col_pb = st.columns(2, gap="large")

                            with col_eb:
                                current_back = st.text_area(
                                    "✏️ Bearbeitung",
                                    value=card["back"],
                                    height=280,
                                    key=f"back_edit_{i}"
                                )

                            with col_pb:
                                st.markdown("👀 Vorschau")
                                with st.container(border=True, height=280):
                                    st.markdown(
                                        normalize_latex(
                                            resolve_graphic_refs(current_back, slide_graphics)
                                        ),
                                        unsafe_allow_html=False
                                    )

                        st.write("")

                        c1, c2 = st.columns(2)

                        # SAVE
                        if c1.button(
                            "💾 Speichern",
                            key=f"save_{i}"
                        ):

                            set_card_content(
                                st.session_state.pdf_name,
                                st.session_state.current_page,
                                i,
                                current_front,
                                current_back,
                            )

                            st.session_state.editing_index = None

                            st.rerun()

                        # CANCEL
                        if c2.button(
                            "❌ Abbrechen",
                            key=f"cancel_{i}"
                        ):

                            st.session_state.editing_index = None

                            st.rerun()


# =========================================================
# Dynamic container height via JS
# =========================================================
components.html("""
<script>
(function() {
    function syncHeight() {
        const doc = window.parent.document;

        // Find the PDF image
        const img = doc.querySelector('[data-testid="stImage"] img');
        if (!img || img.getBoundingClientRect().height === 0) {
            setTimeout(syncHeight, 200);
            return;
        }

        // Bottom edge of the left column (image + nav buttons)
        const imgCol = img.closest('[data-testid="stVerticalBlock"]');
        const imgColBottom = imgCol
            ? imgCol.getBoundingClientRect().bottom
            : img.getBoundingClientRect().bottom;

        // Top edge of the "Review" subheader
        const subheaders = doc.querySelectorAll(
            '[data-testid="stHeading"] h3, [data-testid="stHeading"] h2'
        );
        let reviewTop = null;
        subheaders.forEach(h => {
            if (h.textContent.trim() === "Review") {
                reviewTop = h.getBoundingClientRect().bottom;
            }
        });

        if (!reviewTop) return;

        const targetHeight = imgColBottom - reviewTop - 16; // 16px padding

        // Set the scrollable card container
        const scrollables = doc.querySelectorAll('div[style*="overflow: auto"]');
        scrollables.forEach(el => {
            el.style.height = targetHeight + 'px';
            el.style.maxHeight = targetHeight + 'px';
        });
    }

    // Initial (two passes, in case the image loads slowly)
    setTimeout(syncHeight, 400);
    setTimeout(syncHeight, 900);

    // Recompute when the window is resized
    window.parent.addEventListener(
        'resize',
        () => setTimeout(syncHeight, 150)
    );
})();
</script>
""", height=0)
