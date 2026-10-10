# Orchestrators Backend

API and agent orchestration services for Orchestrators.

## Stack

- **Language:** Python 3.11+
- **Framework:** FastAPI
- **ASGI server:** Uvicorn
- **LLM:** Google Gemini (`google-genai`)
- **Config:** `python-dotenv` (`.env`)

## Setup

```bash
cd orchestrators-backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
cp .env.example .env
# fill in API keys in .env
pip install -r requirements.txt
```

## Environment

See [`.env.example`](.env.example). Required / expected keys:

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Google Gemini API access |
| `GRADIUM_API_KEY` | Gradium API access |

Never commit `.env`.

## Run

```bash
# Dev API server (FastAPI)
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Or the Gemini interactions smoke script (if using the script entrypoint)
python main.py
```

API docs (when the FastAPI app is running): [http://localhost:8000/docs](http://localhost:8000/docs)

## Layout

```
orchestrators-backend/
  main.py            # FastAPI app entry (and temporary Gemini smoke script)
  requirements.txt
  .env.example
  README.md
```
