# Terminal 1 — the API
source .venv/bin/activate
uvicorn api.main:app --port 8077

# Terminal 2 — the website
cd frontend
npm run dev

Then open http://localhost:3000.