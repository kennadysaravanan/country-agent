"""
Streamlit chat UI for the Country Agent.

Two ways to run:

    A) STANDALONE (recommended for free hosting on Streamlit Community Cloud)
       The UI imports and calls the agent directly — no FastAPI needed.
           streamlit run ui.py

    B) AGAINST A RUNNING FASTAPI SERVER (local dev or self-hosted prod)
       Set API_URL=http://localhost:8000 in .env, then:
           uvicorn app.main:app --port 8000      # terminal 1
           streamlit run ui.py                    # terminal 2

Mode is auto-detected:
    - If env var API_URL is set → HTTP mode (talks to FastAPI)
    - Otherwise                  → standalone mode (calls agent.ask directly)

Each Streamlit session gets a unique session_id which the agent uses as the
short-term memory key.
"""
from __future__ import annotations

import os
import uuid

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------
API_URL = (os.getenv("API_URL") or "").strip().rstrip("/")
STANDALONE = not API_URL  # No API_URL → call the agent directly.


# ---------------------------------------------------------------------------
# Two backends — same call signature
# ---------------------------------------------------------------------------
def _ask_standalone(question: str, session_id: str) -> str:
    """Call the agent in-process. Used on Streamlit Cloud."""
    # Imported lazily so HTTP-mode users don't need GROQ_API_KEY locally.
    from app.agent import ask
    return ask(question, session_id)


def _ask_via_api(question: str, session_id: str) -> str:
    """POST to FastAPI /ask. Used in local dev with a running uvicorn."""
    import httpx
    resp = httpx.post(
        f"{API_URL}/ask",
        json={"question": question, "session_id": session_id},
        timeout=60,
    )
    if resp.status_code == 200:
        return resp.json()["answer"]
    detail = resp.json().get("message") or resp.text
    return f"⚠️ API error ({resp.status_code}): {detail}"


def _reset_standalone(session_id: str) -> None:
    from app.memory import session_memory
    session_memory.clear(session_id)


def _reset_via_api(session_id: str) -> None:
    import httpx
    try:
        httpx.post(f"{API_URL}/reset", json={"session_id": session_id}, timeout=5)
    except httpx.RequestError:
        pass  # Even if the server is down, clear the local UI.


ask_backend = _ask_standalone if STANDALONE else _ask_via_api
reset_backend = _reset_standalone if STANDALONE else _reset_via_api


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Country Agent", page_icon="🌍", layout="centered")
st.title("🌍 Country Agent")
st.caption("Ask anything about countries. The agent remembers our conversation.")


# ---------------------------------------------------------------------------
# Streamlit Cloud secret check (helpful error if user forgot to set it)
# ---------------------------------------------------------------------------
if STANDALONE:
    # On Streamlit Cloud, secrets get exposed as env vars too, but we double-
    # check so the user gets a clear message if they forgot to set the secret.
    if not os.getenv("GROQ_API_KEY"):
        # Try Streamlit secrets as a fallback (works on Streamlit Cloud).
        try:
            os.environ["GROQ_API_KEY"] = st.secrets["GROQ_API_KEY"]
        except (KeyError, FileNotFoundError):
            st.error(
                "**GROQ_API_KEY is not configured.**\n\n"
                "On Streamlit Cloud: open the app's **Settings → Secrets** and add:\n\n"
                "```\nGROQ_API_KEY = \"gsk_...\"\n```\n\n"
                "Locally: copy `.env.example` to `.env` and set `GROQ_API_KEY`."
            )
            st.stop()


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "session_id" not in st.session_state:
    st.session_state.session_id = f"sess-{uuid.uuid4().hex[:12]}"
if "messages" not in st.session_state:
    st.session_state.messages = []  # [{"role": "user"|"assistant", "content": str}]


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Session")
    st.code(st.session_state.session_id, language="text")
    st.caption("Mode: " + ("standalone (in-process)" if STANDALONE else f"HTTP → {API_URL}"))

    if st.button("🗑️ Reset conversation"):
        reset_backend(st.session_state.session_id)
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.subheader("Try asking")
    examples = [
        "What is the population of Germany?",
        "What currency does Japan use?",
        "What is the capital and population of Brazil?",
        "Compare Brazil and India",
        "What languages do they speak there?",  # follow-up demo
    ]
    for ex in examples:
        if st.button(ex, key=f"ex_{ex}", use_container_width=True):
            st.session_state._pending_input = ex
            st.rerun()


# ---------------------------------------------------------------------------
# Render existing chat
# ---------------------------------------------------------------------------
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])


# ---------------------------------------------------------------------------
# Handle new input
# ---------------------------------------------------------------------------
user_input = st.chat_input("Ask about a country...")
if not user_input and "_pending_input" in st.session_state:
    user_input = st.session_state.pop("_pending_input")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        placeholder.markdown("_Thinking..._")
        try:
            answer = ask_backend(user_input, st.session_state.session_id)
        except Exception as e:  # noqa: BLE001
            answer = f"⚠️ {type(e).__name__}: {e}"
        placeholder.markdown(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
