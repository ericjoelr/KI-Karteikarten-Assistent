# KI-Karteikarten-Assistent

Erzeugt aus Vorlesungsskripten (PDFs) Karteikarten die direkt in das Lernprogramm Anki exportiert werden können.
Ein multimodales LLM liest jede Folie samt extrahierter Grafiken und liefert schema-erzwungene Karten, die ein Mensch vor dem Export prüft — wahlweise mit Gemini, lokalem llama.cpp oder einem selbst feinabgestimmten Vision-Language-Modell.

![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

## Der Mehrwert

Aus einem Foliensatz gute Karteikarten zu erstellen, kostet Stunden. Inhalt ansehen, eine Frage für die Vorderseite überlegen, den Inhalt auf die Rückseite schreiben, sich mit Diagrammen, mathematischen Formeln oder Tabelle rumschlagen... all das kostet wertvolle Zeit die besser für das Lernen genutzt werden kann.

LLMs können den Kartenerstellungsprozess massiv beschleunigen damit mehr Zeit zum Lernen bleibt. Doch reine Text-Extraktion verliert genau das, was Folien ausmacht: Diagramme, Tabellen, Formeln. Dieses Projekt gibt dem Modell deshalb das Bild jeder Folie, lässt Grafiken aus den Folien direkt in die Karten übernehmen und lässt den Nutzer die Karten bewerten, sodass nur validierte Karten wirklich gelernt werden.

Dies funktioniert mit Cloud Modellen als auch lokal mit einem speziell für diese Aufgabe trainiertes Modell (lokal nur mit Apple Silicon). So bleiben die Folien auf deinem Rechner, du kannst den Assistenten auch offline nutzen und du hast keine API-Kosten.

## Die Oberfläche

![Oberfläche: links die gewählte Folie, rechts die generierten Karten zur Prüfung](docs/readme_graphics/nutzeroberfl%C3%A4che.png)

## Architektur

![Verarbeitungsfluss: Vorlesungs-PDF über Streamlit-Frontend, FastAPI-Backend und Vision-LLM zu geprüften Karten in Anki](docs/readme_graphics/architektur.webp)

## Selbst Starten

### Voraussetzungen

- [uv](https://docs.astral.sh/uv/) (installiert Python 3.13 selbst)
- [Anki](https://apps.ankiweb.net/) mit dem Add-on [AnkiConnect](https://ankiweb.net/shared/info/2055492159)
- **Eines** der folgenden Modell-Backends:
  - **Cloud-Anbieter** mit OpenAI-kompatibler API, einem Vision-LLM und Unterstützung für
    `json_schema`-Ausgabe, z. B. Google Gemini ([kostenloser API-Key](https://aistudio.google.com/apikey)).
    In der `.env`: `CLOUD_API_KEY` und `PROVIDER_API_KEY`.
  - **llama.cpp** mit einem eigenen Vision-Modell im Hugging-Face-Cache.
    In der `.env`: `LLAMACPP_SERVER=/pfad/zu/llama-server` (nur nötig, wenn `llama-server` nicht im `PATH` liegt).
  - **MLX-Server** (`mlx_vlm.server`, nur Apple Silicon) mit einem eigenen LoRA-Adapter.
    In der `.env`: `MLX_SERVER=/pfad/zu/mlx_vlm.server` und `MLX_ADAPTERS_DIR=/pfad/zu/adaptern`
    (ein Unterordner pro Adapter).

Die lokalen Server startet die Anwendung selbst, sobald in der Oberfläche ein Modell aktiviert wird.

### Installation

```bash
git clone https://github.com/ericjoelr/KI-Karteikarten-Assistent.git
cd KI-Karteikarten-Assistent
uv sync
cp .env.example .env          # Werte für das gewählte Modell-Backend eintragen
```

Backend und Frontend zusammen starten (Strg+C beendet beide):

```bash
make run
```

Das Frontend öffnet sich unter http://localhost:8501.

Im Frontend ein PDF hochladen (oder nach `data/pdf/` legen), eine Folie wählen und
**Karten erzeugen** klicken. 
Um Karten nach Anki zu exportieren, muss die Anwendung mit installiertem AnkiConnect im Hintergrund aktiv sein.

## Projektstruktur

```
data/                 Vorlesungsskripte als PDFs und erstellte Karten
src/backend/          PDF-Verarbeitung, Kartengenererierung und -Speicherung sowie die Anki Schnittstelle
src/backend/prompts/  System-Prompts
src/frontend/         Streamlit-Oberfläche
tests/                Tests für die Backend Logik
```

## Kontext

Entstanden als Projekt im Modul *Advanced Natural Language Processing* (2026), parallel zu einem Deep-Learning-Projekt, in dem das lokale Modell trainiert wurde.

## Lizenz

[MIT](LICENSE)
