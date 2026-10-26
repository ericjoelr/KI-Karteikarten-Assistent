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
- Eine der drei Modell-Optionen unten

### Installation

```bash
git clone https://github.com/ericjoelr/KI-Karteikarten-Assistent.git
cd KI-Karteikarten-Assistent
uv sync
```

Eine `.env` muss nicht angelegt werden: Anbieter, Modell und API-Key werden in der Oberfläche gewählt,
die sie selbst in die `.env` schreibt (von Git ignoriert).

### Modell einrichten

| Option | Läuft auf | Einrichtung |
| --- | --- | --- |
| **Cloud**, z. B. Google Gemini | jedem Rechner, braucht einen API-Key | Key in der Oberfläche |
| **Feinabgestimmtes Modell** (MLX) | Apple Silicon mit mind. 16 GB Unified Memory | `make mlx` |
| **llama.cpp** mit eigenem Vision-Modell | Modellabhängig | `llama-server` im PATH |

#### Cloud

Ein Anbieter mit OpenAI-kompatibler API, einem Vision-LLM und Unterstützung für
`json_schema`-Ausgabe. URL und API-Key werden beim Aktivieren im Modell-Dialog eingetragen;
voreingestellt ist die URL von Google Gemini.

#### Feinabgestimmtes Modell (MLX)

Qwen3-VL-4B mit dem eigenen
[LoRA-Adapter von Hugging Face](https://huggingface.co/ericjoelr/qwen3-vl-4b-karteikarten-lora).
Die Folien verlassen den Rechner nicht.

**Systemanforderungen:** Mac mit Apple Silicon, mindestens 16 GB Unified Memory
(gemessen: ca. 9 GB Spitzenbedarf beim Generieren), ca. 6 GB freier Speicherplatz.

```bash
make mlx
```

Der Befehl installiert `mlx-vlm` und lädt Basismodell und Adapter.

#### llama.cpp

Ein eigenes Vision-Modell im Hugging-Face-Cache und `llama-server` im PATH (z. B. per
`brew install llama.cpp`). Liegt er woanders, in einer `.env` `LLAMACPP_SERVER=/pfad/zu/llama-server`
setzen (Vorlage: `.env.example`).

### Starten

Backend und Frontend zusammen starten (Strg+C beendet beide):

```bash
make run
```

Das Frontend öffnet sich unter http://localhost:8501.

1. Über den Modell-Knopf den Anbieter wählen, **Verfügbare Modelle anzeigen** klicken und ein Modell
   **Aktivieren**. Lokale Server startet die Anwendung dabei selbst.
2. Ein PDF hochladen (oder nach `data/pdf/` legen), eine Folie wählen und **Karten generieren** klicken.
3. Für den Export nach Anki muss Anki mit AnkiConnect im Hintergrund laufen.

## Projektstruktur

```
.
├── data/
│   ├── pdf/                    Vorlesungsskripte als PDF (inkl. Beispiel_Vorlesung.pdf)
│   └── cards/                  Kartenbestand: eine JSON-Datei pro PDF (wird zur Laufzeit angelegt)
├── src/
│   ├── backend/                FastAPI-Backend
│   │   ├── main.py             Alle API-Routen; leiten nur an die Module weiter
│   │   ├── pdf.py              Folien als Bild rendern, Grafiken erkennen und nummeriert markieren
│   │   ├── llm.py              Anfrage ans Vision-LLM, JSON-Schema erzwingt das Kartenformat
│   │   ├── providers.py        Modell-Anbieter (Cloud, llama.cpp, MLX): Modelle auflisten, lokale Server starten
│   │   ├── storage.py          Kartenbestand lesen und schreiben (Karten, Prüfstatus, Anki-ID)
│   │   ├── anki.py             Export nach Anki: Markdown → HTML, Grafiken als Medien, AnkiConnect
│   │   ├── schemas.py          Alle Datenmodelle (Pydantic) an einer Stelle
│   │   ├── config.py           Einstellungen aus der .env, setzt den System-Prompt zusammen
│   │   └── prompts/            System-Prompt und Zusatz für den Grafik-Modus
│   └── frontend/               Streamlit-Oberfläche
│       ├── streamlit_app.py    Einstieg: Folienansicht und Review der Karten
│       ├── dialogs.py          Dialoge: PDF, Anki-Deck, Modellwahl, alle Folien generieren
│       └── api.py              Alle HTTP-Aufrufe ans Backend
├── tests/                      pytest: API-Routen, Kartenbestand, LLM-Anfrage, Modell-Anbieter
├── models/                     LoRA-Adapter des lokalen Modells (von make mlx angelegt)
├── docs/readme_graphics/       Bilder dieser README
├── .env.example                Optionale Einstellungen (die .env legt die Oberfläche selbst an)
├── Makefile                    make run: startet Backend und Frontend; make mlx: lokales Modell
└── pyproject.toml              Abhängigkeiten und Tool-Konfiguration (uv, ruff)
```

## Kontext

Entstanden als Projekt im Modul *Advanced Natural Language Processing* (2026), parallel zu einem Deep-Learning-Projekt, in dem das lokale Modell trainiert wurde.

## Lizenz

[MIT](LICENSE)
