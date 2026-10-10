# Orchestrators

## Backend (Python / FastAPI)

```bash
cd orchestrators-backend
cp .env.example .env
# paste GEMINI_API_KEY (and GRADIUM_API_KEY) into .env
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

See [`orchestrators-backend/README.md`](orchestrators-backend/README.md) for stack details.

## Frontend (Next.js)

```bash
cd orchestrators-frontend
npm install
npm run dev
```
