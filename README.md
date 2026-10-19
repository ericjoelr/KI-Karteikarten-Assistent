# anki_agent

Erzeugt aus Vorlesungsfolien (PDF) Anki-Karteikarten mit einem multimodalen LLM:
FastAPI-Backend (`src/backend/`) plus entkoppeltes Streamlit-Frontend (`src/frontend/`),
das ausschließlich über HTTP mit dem Backend spricht.

## Starten

```bash
uv sync                                              # Abhängigkeiten installieren
uv run uvicorn backend.main:app --reload             # Backend auf http://127.0.0.1:8000
uv run streamlit run src/frontend/streamlit_app.py   # Frontend auf http://localhost:8501
```

Datensatz-Lauf (eigenständiges Skript, ohne Server und Oberfläche). Er legt den
Kartenbestand des PDFs unter `data/cards/dataset/<pdf>/cards.json` an; ein
erneuter Aufruf setzt fort, statt Kontingent zu verbrauchen:

```bash
uv run python scripts/generate_dataset.py skript.pdf --from 1 --to 40
```

Tests:

```bash
uv run pytest -q
```

## Voraussetzungen

- Eine `.env`-Datei. Anbieter und Modell werden in der Oberfläche gewählt (Knopf oben in
  der Leiste) und dorthin zurückgeschrieben: Cloud (URL + API-Key, Standard Gemini),
  llama.cpp oder das feinabgestimmte Modell über mlx (ADR-0009).
- Anki mit dem AnkiConnect-Plugin (Port 8765) — optional; ohne Anki bleiben nur
  Deck-Auswahl und Export gesperrt.
- PDFs liegen in `data/pdf/` (oder werden über die Oberfläche hochgeladen).

## Lokale Modelle (llama.cpp, mlx)

Beide Server startet die App selbst, sobald ein Modell aktiviert wird (llama.cpp schon beim
Anzeigen der Modelle); sie brauchen nur ihre Pfade in der `.env`:

```
LLAMACPP_SERVER=/pfad/zu/llama-server
MLX_SERVER=/pfad/zum/deep-learning-repo/.venv/bin/mlx_vlm.server
MLX_ADAPTERS_DIR=/pfad/zum/deep-learning-repo/models
```

llama.cpp läuft im Router-Modus und bietet alle Bild-Modelle aus dem Hugging-Face-Cache an.
Bei mlx ist jedes Modell ein LoRA-Adapter-Ordner; das feinabgestimmte Qwen3-VL-4B läuft nur
auf Apple Silicon und kennt nur den grafiklosen Eingabevertrag, deshalb schaltet sein
Aktivieren den Grafik-Schalter aus (ADR-0008). Nach einem Neustart des Backends muss das
lokale Modell einmal neu aktiviert werden.

## Vertrag zum Deep-Learning-Projekt

Dieses Repository erzeugt den Kartenbestand; das begleitende Deep-Learning-Projekt
macht daraus Trainingsdaten und rendert die Folienbilder dafür selbst aus dem PDF
(ADR-0006). Damit Trainings- und Inferenzbild übereinstimmen, muss es diese vier
Parameter von `pdf.get_slide_image` exakt treffen:

| Parameter | Wert |
|---|---|
| Bibliothek | PyMuPDF (`pymupdf`) 1.27.2.2, Render über `page.get_pixmap` |
| Zoom-Faktor | `pymupdf.Matrix(1.5, 1.5)` |
| Farbraum | `pymupdf.csRGB` |
| Dateiformat | JPG (`pix.tobytes("jpg")`, Standardqualität dieses PyMuPDF-Builds) |

Zum Anbieter geht das Bild als `data:image/jpeg;base64,<base64>`. Die vorhandenen
Datensatz-Läufe entstanden alle **ohne** Grafik-Modus — es genügt, die Seite zu
rendern; die Grafik-Erkennung muss drüben nicht nachgebaut werden.
