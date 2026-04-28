#!/usr/bin/env bash
# Starts the FastAPI server AND the Streamlit UI in one go.
# Ctrl-C stops both cleanly.

set -e
cd "$(dirname "$0")"

# 1. Create venv if missing
if [ ! -d ".venv" ]; then
  echo "Creating virtualenv..."
  python3 -m venv .venv
fi

# 2. Activate it
# shellcheck disable=SC1091
source .venv/bin/activate

# 3. Install deps
pip install -q -r requirements.txt

# 4. Check env
if [ ! -f ".env" ]; then
  echo ".env not found — copying from .env.example."
  echo "Please put your GROQ_API_KEY in .env, then run this script again."
  cp .env.example .env
  exit 1
fi

# 5. Start FastAPI (background) + Streamlit (foreground)
echo
echo "Starting FastAPI on http://localhost:8000 ..."
uvicorn app.main:app --port 8000 &
API_PID=$!
trap 'echo; echo "Stopping..."; kill $API_PID 2>/dev/null; exit 0' INT TERM
sleep 2

echo "Starting Streamlit on http://localhost:8501 ..."
echo "(Open http://localhost:8501 in your browser)"
echo
streamlit run ui.py --server.port 8501 --server.headless true

# If streamlit exits, also kill the API.
kill $API_PID 2>/dev/null || true
